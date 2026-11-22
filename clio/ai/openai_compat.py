from __future__ import annotations

import threading
import time

import httpx

from clio.ai.base import AIResponse, StructuredOutput, TokenUsage, normalize_response_reason
from clio.config import ProviderConfig, ProxyConfig
from clio.i18n import t
from clio.ratelimit import make_rate_limiter, wait_for_rate_limit
from clio.utils import mask_if_looks_like_key, retry_after_seconds, with_retry


def _usage_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_OLLAMA_BASE_URL = "http://localhost:11434/v1"
# Transient client statuses worth retrying (timeout, conflict, too early, rate limit).
_RETRYABLE_STATUSES = frozenset({408, 409, 425, 429})
# How long a (model, schema) pair skips response_format after the endpoint rejected it.
_STRUCTURED_OUTPUT_RETRY_SEC = 600.0


def _strict_schema_compatible(schema: object) -> bool:
    """True when OpenAI strict mode accepts *schema*.

    Strict mode requires every object to list all of its properties in
    ``required`` and to set ``additionalProperties: false``. Pydantic leaves
    fields with defaults out of ``required``, so such schemas must not be sent
    with ``strict: true`` (the request would be rejected).
    """
    if isinstance(schema, list):
        return all(_strict_schema_compatible(item) for item in schema)
    if not isinstance(schema, dict):
        return True
    properties = schema.get("properties")
    if schema.get("type") == "object" or isinstance(properties, dict):
        props = properties if isinstance(properties, dict) else {}
        if schema.get("additionalProperties") is not False:
            return False
        if set(schema.get("required") or []) != set(props):
            return False
    for key in ("properties", "$defs", "definitions", "patternProperties"):
        mapping = schema.get(key)
        if isinstance(mapping, dict) and not all(_strict_schema_compatible(sub) for sub in mapping.values()):
            return False
    for key in ("items", "not", "anyOf", "oneOf", "allOf", "prefixItems"):
        if key in schema and not _strict_schema_compatible(schema[key]):
            return False
    extra = schema.get("additionalProperties")
    return not isinstance(extra, dict) or _strict_schema_compatible(extra)


class OpenAICompatProvider:
    """OpenAI 及兼容 API（DeepSeek、通义、Moonshot、Ollama 等）。"""

    provider_id = "openai_compat"

    def __init__(self, cfg: ProviderConfig, proxy: ProxyConfig):
        # Local Ollama endpoints do not require an API key.
        from clio.credentials import credential_required

        if not cfg.api_key and credential_required(cfg.type):
            env_name = mask_if_looks_like_key(cfg.api_key_env) or t("ai.env_not_set")
            raise ValueError(t("ai.openai_missing_key", {"provider": cfg.name, "env": env_name}))
        self._api_key = cfg.api_key
        default_base = _OLLAMA_BASE_URL if cfg.type == "ollama" else _DEFAULT_BASE_URL
        self._base_url = (cfg.base_url or default_base).rstrip("/")
        client_kwargs: dict = {"timeout": float(cfg.timeout_sec)}
        if proxy.enabled and proxy.url:
            client_kwargs["proxy"] = proxy.url
        self._client = httpx.Client(**client_kwargs)
        self._rl = make_rate_limiter(cfg.requests_per_minute)
        self._retry_attempts = max(1, cfg.retry_attempts + 1)
        self._max_tokens = cfg.max_tokens
        # (model, schema name) -> monotonic deadline; a rejection only affects that pair.
        self._structured_output_disabled_until: dict[tuple[str, str], float] = {}
        self._structured_output_lock = threading.Lock()

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _response_format(structured_output: StructuredOutput) -> dict:
        if structured_output.schema is None:
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": structured_output.name,
                "strict": _strict_schema_compatible(structured_output.schema),
                "schema": structured_output.schema,
            },
        }

    def _structured_output_disabled(self, model: str, name: str) -> bool:
        key = (model, name)
        with self._structured_output_lock:
            until = self._structured_output_disabled_until.get(key)
            if until is None:
                return False
            if time.monotonic() >= until:
                del self._structured_output_disabled_until[key]
                return False
            return True

    def _disable_structured_output(self, model: str, name: str) -> None:
        with self._structured_output_lock:
            self._structured_output_disabled_until[model, name] = time.monotonic() + _STRUCTURED_OUTPUT_RETRY_SEC

    @staticmethod
    def _structured_output_unsupported(response: httpx.Response) -> bool:
        if response.status_code not in {400, 422}:
            return False
        body = response.text.lower()
        return any(
            marker in body
            for marker in ("response_format", "response format", "json_schema", "json schema", "structured output")
        )

    def generate_text(
        self,
        prompt: str,
        model: str,
        cancel_event: threading.Event | None = None,
        *,
        temperature: float | None = None,
        system: str | None = None,
        structured_output: StructuredOutput | None = None,
    ) -> AIResponse:
        def _do() -> AIResponse:
            wait_for_rate_limit(self._rl, cancel_event, what=self.provider_id)
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload: dict = {
                "model": model,
                "messages": messages,
            }
            if temperature is not None:
                payload["temperature"] = float(temperature)
            # 0 / negative → omit max_tokens (provider default / unlimited)
            if self._max_tokens and self._max_tokens > 0:
                payload["max_tokens"] = int(self._max_tokens)
            response_format = None
            if structured_output is not None and not self._structured_output_disabled(model, structured_output.name):
                response_format = self._response_format(structured_output)
                payload["response_format"] = response_format
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            if (
                structured_output is not None
                and response_format is not None
                and self._structured_output_unsupported(response)
            ):
                self._disable_structured_output(model, structured_output.name)
                retry_payload = {key: value for key, value in payload.items() if key != "response_format"}
                response = self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=retry_payload,
                )
            sc = response.status_code
            if sc in _RETRYABLE_STATUSES or sc >= 500:
                raise httpx.HTTPStatusError(f"status {sc}", request=response.request, response=response)
            if 400 <= sc < 500:
                body = response.text[:200]
                raise ValueError(t("ai.api_error_response", {"url": self._base_url, "status": sc, "body": body}))
            # A proxy login page or gateway HTML can arrive with 200; report it like an API error.
            try:
                data = response.json()
            except ValueError as exc:
                body = response.text[:200]
                raise ValueError(
                    t("ai.api_error_response", {"url": self._base_url, "status": sc, "body": body})
                ) from exc
            if not isinstance(data, dict):
                body = response.text[:200]
                raise ValueError(t("ai.api_error_response", {"url": self._base_url, "status": sc, "body": body}))
            usage_raw = data.get("usage")
            usage = None
            if usage_raw:
                usage = TokenUsage(
                    prompt_tokens=_usage_count(usage_raw.get("prompt_tokens")),
                    completion_tokens=_usage_count(usage_raw.get("completion_tokens")),
                    total_tokens=_usage_count(usage_raw.get("total_tokens")),
                )
            choice0 = (data.get("choices") or [{}])[0]
            finish_reason = normalize_response_reason(choice0.get("finish_reason"))
            raw_content = (choice0.get("message") or {}).get("content")
            content = raw_content if isinstance(raw_content, str) else ""
            block_reason = None
            if finish_reason in {"content_filter", "content_filtered"}:
                block_reason = finish_reason
            return AIResponse(
                text="" if block_reason is not None else content,
                token_usage=usage,
                finish_reason=finish_reason,
                block_reason=block_reason,
            )

        return with_retry(
            _do,
            attempts=self._retry_attempts,
            base_delay=1.0,
            max_delay=60.0,
            retry_on=(httpx.HTTPError,),
            delay_for=retry_after_seconds,
            cancel_event=cancel_event,
            what=t("ai.retry_what_openai", {"url": self._base_url}),
        )

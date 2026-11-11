from __future__ import annotations

import threading

import httpx

from clio.ai.base import AIResponse, TokenUsage, normalize_response_reason
from clio.config import ProviderConfig, ProxyConfig
from clio.i18n import t
from clio.ratelimit import make_rate_limiter, wait_for_rate_limit
from clio.utils import mask_if_looks_like_key, retry_after_seconds, with_retry


def _usage_count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_OLLAMA_BASE_URL = "http://localhost:11434/v1"


class OpenAICompatProvider:
    """OpenAI 及兼容 API（DeepSeek、通义、Moonshot、Ollama 等）。"""

    provider_id = "openai_compat"

    def __init__(self, cfg: ProviderConfig, proxy: ProxyConfig):
        # Local Ollama endpoints do not require an API key.
        if not cfg.api_key and cfg.type != "ollama":
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

    def close(self) -> None:
        self._client.close()

    def generate_text(
        self,
        prompt: str,
        model: str,
        cancel_event: threading.Event | None = None,
        *,
        temperature: float | None = None,
        system: str | None = None,
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
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            sc = response.status_code
            if sc == 429 or sc >= 500:
                raise httpx.HTTPStatusError(f"status {sc}", request=response.request, response=response)
            if 400 <= sc < 500:
                body = response.text[:200]
                raise ValueError(t("ai.api_error_response", {"url": self._base_url, "status": sc, "body": body}))
            data = response.json()
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

    def analyze_video(self, video_path: str, prompt: str, model: str) -> AIResponse:
        raise NotImplementedError(t("ai.openai_no_video", {"provider": self.provider_id}))

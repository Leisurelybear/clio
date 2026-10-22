from __future__ import annotations

import time

import httpx

from clio.ai.base import AIResponse, TokenUsage
from clio.config import ProviderConfig, ProxyConfig
from clio.i18n import t
from clio.ratelimit import make_rate_limiter
from clio.utils import mask_if_looks_like_key, with_retry


class OpenAICompatProvider:
    """OpenAI 及兼容 API（DeepSeek、通义、Moonshot 等）。"""

    provider_id = "openai_compat"

    def __init__(self, cfg: ProviderConfig, proxy: ProxyConfig):
        if not cfg.api_key:
            env_name = mask_if_looks_like_key(cfg.api_key_env) or t("ai.env_not_set")
            raise ValueError(t("ai.openai_missing_key", {"provider": cfg.name, "env": env_name}))
        self._api_key = cfg.api_key
        self._base_url = (cfg.base_url or "https://api.openai.com/v1").rstrip("/")
        client_kwargs: dict = {"timeout": float(cfg.timeout_sec)}
        if proxy.enabled and proxy.url:
            client_kwargs["proxy"] = proxy.url
        self._client = httpx.Client(**client_kwargs)
        self._rl = make_rate_limiter(cfg.requests_per_minute)
        self._retry_attempts = max(1, cfg.retry_attempts + 1)
        self._max_tokens = cfg.max_tokens

    def close(self) -> None:
        self._client.close()

    def _maybe_wait(self) -> None:
        if self._rl is not None:
            wait = self._rl.acquire()
            if wait:
                time.sleep(wait)

    def generate_text(self, prompt: str, model: str) -> AIResponse:
        def _do() -> AIResponse:
            self._maybe_wait()
            payload: dict = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
            }
            # 0 / negative → omit max_tokens (provider default / unlimited)
            if self._max_tokens and self._max_tokens > 0:
                payload["max_tokens"] = int(self._max_tokens)
            response = self._client.post(
                f"{self._base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
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
                    prompt_tokens=usage_raw.get("prompt_tokens", 0),
                    completion_tokens=usage_raw.get("completion_tokens", 0),
                    total_tokens=usage_raw.get("total_tokens", 0),
                )
            choice0 = (data.get("choices") or [{}])[0]
            finish_reason = choice0.get("finish_reason")
            content = (choice0.get("message") or {}).get("content") or ""
            return AIResponse(
                text=content,
                token_usage=usage,
                finish_reason=finish_reason,
            )

        return with_retry(
            _do,
            attempts=self._retry_attempts,
            base_delay=1.0,
            retry_on=(httpx.HTTPError,),
            what=t("ai.retry_what_openai", {"url": self._base_url}),
        )

    def analyze_video(self, video_path: str, prompt: str, model: str) -> AIResponse:
        raise NotImplementedError(t("ai.openai_no_video", {"provider": self.provider_id}))

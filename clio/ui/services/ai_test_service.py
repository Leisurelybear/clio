from __future__ import annotations

import re
import time
from typing import Any

from clio.ai.base import AIResponse
from clio.ai.factory import _build_provider, provider_use
from clio.config import AppConfig, ProviderConfig
from clio.i18n import t
from clio.utils import mask_if_looks_like_key

_TEST_PROMPT = "Reply with exactly: ok"
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{11,}")


def _masked_secret(secret: str) -> str:
    masked = mask_if_looks_like_key(secret)
    return masked if masked != secret else "***"


def _sanitize_error(message: str, secrets: list[str]) -> str:
    result = message
    for secret in secrets:
        if secret:
            result = result.replace(secret, _masked_secret(secret))

    def replace_token(match: re.Match[str]) -> str:
        token = match.group(0)
        masked = mask_if_looks_like_key(token)
        return masked if masked != token else token

    return _TOKEN_RE.sub(replace_token, result)


def _sanitize_display_value(value: str) -> str:
    return _sanitize_error(value, [])


def _error_secrets(provider_cfg: ProviderConfig) -> list[str]:
    secrets = [provider_cfg.api_key]
    if mask_if_looks_like_key(provider_cfg.api_key_env) != provider_cfg.api_key_env:
        secrets.append(provider_cfg.api_key_env)
    return secrets


def test_provider_connection(config: AppConfig, *, provider_name: str, model: str | None = None) -> dict[str, Any]:
    provider_name = (provider_name or "").strip()
    model = (model or "").strip()
    display_provider = _sanitize_display_value(provider_name)
    display_model = _sanitize_display_value(model)

    provider_cfg = config.ai.providers.get(provider_name)
    if provider_cfg is None:
        return {
            "ok": False,
            "provider": display_provider,
            "model": display_model,
            "error": f"unknown provider: {display_provider}",
        }

    models = list(provider_cfg.models or [])
    if not model:
        if len(models) == 1:
            model = models[0]
            display_model = _sanitize_display_value(model)
        else:
            return {
                "ok": False,
                "provider": display_provider,
                "model": display_model,
                "error": "model is required when provider has zero or multiple registered models",
            }

    started = time.monotonic()
    try:
        provider = _build_provider(config, provider_name)
        with provider_use(provider):
            response = provider.generate_text(_TEST_PROMPT, model)
        if not isinstance(response, AIResponse):
            raise TypeError("AI provider returned an invalid response type")
        if response.blocked:
            raise RuntimeError(t("ai.response_blocked", {"reason": response.block_reason}))
    except Exception as exc:  # noqa: BLE001 - provider SDKs expose heterogeneous failures
        return {
            "ok": False,
            "provider": display_provider,
            "model": display_model,
            "error": _sanitize_error(str(exc), _error_secrets(provider_cfg)),
        }

    return {
        "ok": True,
        "provider": display_provider,
        "model": display_model,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "message": "Connection test succeeded",
    }


test_provider_connection.__test__ = False

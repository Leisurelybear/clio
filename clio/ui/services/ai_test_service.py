from __future__ import annotations

import re
import time
from typing import Any

from clio.ai.base import AIResponse
from clio.ai.factory import _build_provider, provider_use
from clio.ai.provider_security import (
    provider_base_url_error,
    provider_env_name,
    provider_key_env_error,
    provider_rebind_error,
)
from clio.config import AppConfig, ProviderConfig
from clio.credentials import CredentialService
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


def test_provider_connection(
    config: AppConfig,
    *,
    provider_name: str,
    model: str | None = None,
    provider_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_name = (provider_name or "").strip()
    model = (model or "").strip()
    display_provider = _sanitize_display_value(provider_name)
    display_model = _sanitize_display_value(model)

    provider_cfg = config.ai.providers.get(provider_name)
    if provider_override is not None:
        try:
            raw = dict(provider_override)
            service = config.global_cfg.credential_service
            if not isinstance(service, CredentialService):
                service = CredentialService()
            previous_cfg = config.ai.providers.get(provider_name)
            previous = vars(previous_cfg).copy() if previous_cfg is not None else {}
            if error := provider_rebind_error(provider_name, previous, raw, service):
                return {"ok": False, "error": error}
            provider_cfg = ProviderConfig(
                name=provider_name,
                type=str(raw.get("type", "gemini") or "gemini"),
                api_key=str(raw.get("api_key", "") or ""),
                api_key_env=str(raw.get("api_key_env", "") or ""),
                credential_ref=str(raw.get("credential_ref", "") or ""),
                base_url=str(raw.get("base_url", "") or ""),
                poll_interval_sec=int(raw.get("poll_interval_sec", 5) or 5),
                retry_attempts=int(raw.get("retry_attempts", 2) or 2),
                requests_per_minute=int(raw.get("requests_per_minute", 0) or 0),
                timeout_sec=float(raw.get("timeout_sec", 120) or 120),
                max_tokens=int(raw.get("max_tokens", 0) or 0),
                video_upload_max_mb=int(raw.get("video_upload_max_mb", 200) or 200),
                models=[str(v) for v in (raw.get("models") or [])],
                capabilities=[str(v) for v in (raw.get("capabilities") or [])],
            )
            if not provider_cfg.api_key:
                provider_cfg.api_key = service.resolve_provider(raw)
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "provider": display_provider,
                "model": display_model,
                "error": f"invalid provider configuration: {exc}",
            }
    if provider_cfg is None:
        return {
            "ok": False,
            "provider": display_provider,
            "model": display_model,
            "error": f"unknown provider: {display_provider}",
        }

    if provider_key_env_error(provider_name, provider_cfg.api_key_env) is not None:
        return {
            "ok": False,
            "provider": display_provider,
            "model": display_model,
            "error": t(
                "ai.provider_test_unsafe_key_env",
                {"provider": display_provider, "expected": provider_env_name(provider_name)},
            ),
        }
    if provider_base_url_error(provider_cfg.type, provider_cfg.base_url) is not None:
        return {
            "ok": False,
            "provider": display_provider,
            "model": display_model,
            "error": t("ai.provider_test_insecure_url"),
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
        if provider_override is not None:
            from dataclasses import replace

            global_cfg = config.global_cfg
            providers = dict(global_cfg.ai.providers)
            providers[provider_name] = provider_cfg
            global_cfg = replace(global_cfg, ai=replace(global_cfg.ai, providers=providers))
            config = AppConfig(global_cfg=global_cfg, project_cfg=config.project_cfg, project_dir=config.project_dir)
        provider = _build_provider(config, provider_name)
        with provider_use(provider, acquire=lambda: _build_provider(config, provider_name)) as provider:
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


test_provider_connection.__dict__["__test__"] = False

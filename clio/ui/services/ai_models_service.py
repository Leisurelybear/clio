"""Remote model discovery for the AI settings page."""

from __future__ import annotations

import contextlib
import json
import re
from typing import Any

import httpx

from clio.ai.provider_security import (
    provider_base_url_error,
    provider_key_env_error,
    provider_rebind_error,
)
from clio.config import AppConfig, ProviderConfig
from clio.credentials import CredentialService
from clio.utils import mask_if_looks_like_key

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{11,}")
_MASKED_SECRET_VALUES = frozenset({"***", "********", "••••••••••"})
_PROVIDER_TYPES = frozenset({"gemini", "openai", "openai_compat", "ollama"})
_MAX_MODEL_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_MODEL_COUNT = 1000
_MAX_MODEL_NAME_CHARS = 256
_MAX_GEMINI_MODEL_SCAN = 10_000


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


def _masked_secret(secret: str) -> str:
    masked = mask_if_looks_like_key(secret)
    return masked if masked != secret else "***"


def _provider_from_override(
    config: AppConfig, provider_name: str, override: dict[str, Any] | None
) -> ProviderConfig | None:
    existing = config.ai.providers.get(provider_name)
    if override is None:
        return existing
    raw = dict(override)
    service = config.global_cfg.credential_service
    if not isinstance(service, CredentialService):
        service = CredentialService()
    api_key = str(raw.get("api_key", "") or "")
    if api_key in _MASKED_SECRET_VALUES:
        api_key = ""
    provider = ProviderConfig(
        name=provider_name,
        type=str(raw.get("type", getattr(existing, "type", "gemini")) or "gemini"),
        api_key=api_key,
        api_key_env=str(raw.get("api_key_env", "") or ""),
        credential_ref=str(raw.get("credential_ref", "") or ""),
        base_url=str(raw.get("base_url", "") or ""),
        timeout_sec=float(raw.get("timeout_sec", 120) or 120),
        models=[str(v) for v in (raw.get("models") or [])],
        capabilities=[str(v) for v in (raw.get("capabilities") or [])],
    )
    if not provider.api_key:
        provider.api_key = service.resolve_provider(raw, provider_name)
    return provider


def _normalize_models(values: Any) -> list[str]:
    models: list[str] = []
    for value in values or []:
        if len(models) >= _MAX_MODEL_COUNT:
            break
        name = getattr(value, "name", value)
        if isinstance(name, str):
            name = name.strip()
            if name.startswith("models/"):
                name = name[7:]
            if name and len(name) <= _MAX_MODEL_NAME_CHARS and name not in models:
                models.append(name)
    return sorted(models)


def _read_json_response(response) -> Any:
    response.raise_for_status()
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > _MAX_MODEL_RESPONSE_BYTES:
            raise ValueError("model list response is too large")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("model list response is not valid JSON") from exc


def _list_openai_models(provider: ProviderConfig, config: AppConfig) -> list[str]:
    from clio.ai.openai_compat import _DEFAULT_BASE_URL, _OLLAMA_BASE_URL

    default_base = _OLLAMA_BASE_URL if provider.type == "ollama" else _DEFAULT_BASE_URL
    base_url = (provider.base_url or default_base).rstrip("/")
    kwargs: dict[str, Any] = {"timeout": max(1.0, float(provider.timeout_sec))}
    proxy = config.global_cfg.proxy
    if proxy.enabled and proxy.url:
        kwargs["proxy"] = proxy.url
    headers = {"Accept": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    with httpx.Client(**kwargs) as client, client.stream("GET", f"{base_url}/models", headers=headers) as response:
        payload = _read_json_response(response)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("model list response has invalid format")
    values = payload["data"]
    return _normalize_models(item.get("id") for item in values if isinstance(item, dict))


def _list_gemini_models(provider: ProviderConfig, config: AppConfig) -> list[str]:
    from google import genai
    from google.genai import types

    kwargs: dict[str, Any] = {"timeout": max(1, int(float(provider.timeout_sec) * 1000))}
    proxy = config.global_cfg.proxy
    if proxy.enabled and proxy.url:
        kwargs["client_args"] = {"transport": httpx.HTTPTransport(proxy=proxy.url)}
        kwargs["async_client_args"] = {"transport": httpx.AsyncHTTPTransport(proxy=proxy.url)}
    client = genai.Client(api_key=provider.api_key, http_options=types.HttpOptions(**kwargs))
    try:
        models = []
        for index, model in enumerate(client.models.list()):
            if index >= _MAX_GEMINI_MODEL_SCAN or len(models) >= _MAX_MODEL_COUNT:
                break
            methods = getattr(model, "supported_actions", None)
            if methods and not any(
                str(method).lower() in {"generatecontent", "generate_content"} for method in methods
            ):
                continue
            models.append(model)
        return _normalize_models(models)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def list_provider_models(
    config: AppConfig,
    *,
    provider_name: str,
    provider_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover models from a configured provider or an unsaved settings draft."""
    name = (provider_name or "").strip()
    display_name = mask_if_looks_like_key(name)
    try:
        provider = _provider_from_override(config, name, provider_override)
    except (TypeError, ValueError, KeyError):
        # Do not echo arbitrary draft field values: a malformed field may contain
        # a credential copied into the wrong input.
        return {"ok": False, "provider": display_name, "error": "invalid provider configuration"}
    if provider is None:
        return {"ok": False, "provider": display_name, "error": f"unknown provider: {display_name}"}
    if provider_key_env_error(name, provider.api_key_env):
        return {"ok": False, "provider": display_name, "error": "unsafe provider API-key environment name"}
    if provider.type not in _PROVIDER_TYPES:
        return {"ok": False, "provider": display_name, "error": f"unsupported provider type: {provider.type}"}
    if provider_override is not None:
        previous_cfg = config.ai.providers.get(name)
        previous = vars(previous_cfg).copy() if previous_cfg is not None else {}
        raw_override = dict(provider_override)
        if error := provider_rebind_error(
            name,
            previous,
            raw_override,
            getattr(config.global_cfg, "credential_service", None),
        ):
            # The rebind message is code-owned text that embeds the provider name on
            # purpose; only the credential itself may need masking here.
            return {
                "ok": False,
                "provider": display_name,
                "error": _sanitize_error(error, [provider.api_key]),
            }
    if provider_base_url_error(provider.type, provider.base_url):
        return {"ok": False, "provider": display_name, "error": "provider connection URL is unsafe"}
    if provider.type == "gemini" and not provider.api_key:
        return {"ok": False, "provider": display_name, "error": "API key is required"}
    try:
        models = (
            _list_gemini_models(provider, config)
            if provider.type == "gemini"
            else _list_openai_models(provider, config)
        )
        return {"ok": True, "provider": display_name, "models": models}
    except Exception as exc:  # noqa: BLE001 - provider SDKs expose heterogeneous failures
        return {
            "ok": False,
            "provider": display_name,
            "error": _sanitize_error(str(exc), [provider.api_key]),
        }


list_provider_models.__dict__["__test__"] = False

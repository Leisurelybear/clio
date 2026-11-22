from __future__ import annotations

from typing import Any

from clio.config.models import (
    ModelPriceConfig,
    ProjectWhisperConfig,
    ProviderConfig,
    TaskConfig,
)
from clio.config.validators import _filter_dc, _section
from clio.credentials import CredentialService


def _resolve_api_key(provider_raw: dict, service: CredentialService | None = None, name: str = "") -> str:
    return (service or CredentialService()).resolve_provider(provider_raw, name)


def _parse_model_prices(raw: object) -> Any:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return raw
    parsed: dict[object, object] = {}
    for model, prices in raw.items():
        if isinstance(prices, dict):
            typed = _filter_dc(prices, ModelPriceConfig)
            parsed[model] = ModelPriceConfig(
                prompt_price_per_1m_usd=typed.get("prompt_price_per_1m_usd", 0.0),
                completion_price_per_1m_usd=typed.get("completion_price_per_1m_usd", 0.0),
            )
        else:
            parsed[model] = prices
    return parsed


def _infer_provider_capabilities(provider_type: str) -> list[str]:
    if provider_type == "gemini":
        return ["video", "text"]
    return ["text"]


def _parse_providers(raw: dict, service: CredentialService | None = None) -> dict[str, ProviderConfig]:
    providers: dict[str, ProviderConfig] = {}
    for name in raw or {}:
        # A provider written as ``name:`` with no fields loads as None.
        cfg = _section(raw, name, f"ai.providers.{name}")
        typed = _filter_dc(cfg, ProviderConfig)
        provider_type = typed.get("type", "gemini")
        providers[name] = ProviderConfig(
            name=name,
            type=provider_type,
            api_key=_resolve_api_key(cfg, service, name),
            api_key_env=typed.get("api_key_env", ""),
            credential_ref=typed.get("credential_ref", ""),
            base_url=typed.get("base_url", ""),
            poll_interval_sec=typed.get("poll_interval_sec", 5),
            retry_attempts=typed.get("retry_attempts", 2),
            requests_per_minute=typed.get("requests_per_minute", 0),
            timeout_sec=typed.get("timeout_sec", 120.0),
            max_tokens=typed.get("max_tokens", 0),
            video_upload_max_mb=typed.get("video_upload_max_mb", 200),
            model_prices=_parse_model_prices(cfg.get("model_prices", {})),
            prompt_price_per_1m_usd=typed.get("prompt_price_per_1m_usd", 0.0),
            completion_price_per_1m_usd=typed.get("completion_price_per_1m_usd", 0.0),
            models=typed.get("models", []),
            capabilities=typed.get("capabilities") or _infer_provider_capabilities(provider_type),
        )
    return providers


def _parse_tasks(raw: dict) -> dict[str, TaskConfig]:
    tasks: dict[str, TaskConfig] = {}
    for name in raw or {}:
        typed = _filter_dc(_section(raw, name, f"ai task {name}"), TaskConfig)
        tasks[name] = TaskConfig(
            provider=typed.get("provider", ""),
            model=typed.get("model", ""),
            temperature=typed.get("temperature"),
            timeout_sec=typed.get("timeout_sec"),
        )
    return tasks


def _parse_whisper(raw: dict) -> ProjectWhisperConfig:
    cfg = ProjectWhisperConfig(**_filter_dc(raw, ProjectWhisperConfig))
    cfg.sanitize()
    return cfg

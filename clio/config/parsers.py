from __future__ import annotations

import os

from clio.config.models import (
    ProviderConfig,
    TaskConfig,
    WhisperConfig,
)
from clio.config.validators import _filter_dc


def _resolve_api_key(provider_raw: dict) -> str:
    env_name = provider_raw.get("api_key_env", "")
    if env_name:
        env_val = os.environ.get(env_name, "")
        if env_val:
            return env_val
    return provider_raw.get("api_key", "")


def _infer_provider_capabilities(provider_type: str) -> list[str]:
    if provider_type == "gemini":
        return ["video", "text"]
    return ["text"]


def _parse_providers(raw: dict) -> dict[str, ProviderConfig]:
    providers: dict[str, ProviderConfig] = {}
    for name, cfg in (raw or {}).items():
        typed = _filter_dc(cfg, ProviderConfig)
        provider_type = typed.get("type", "gemini")
        providers[name] = ProviderConfig(
            name=name,
            type=provider_type,
            api_key=_resolve_api_key(cfg),
            api_key_env=typed.get("api_key_env", ""),
            base_url=typed.get("base_url", ""),
            poll_interval_sec=typed.get("poll_interval_sec", 5),
            retry_attempts=typed.get("retry_attempts", 2),
            requests_per_minute=typed.get("requests_per_minute", 0),
            timeout_sec=typed.get("timeout_sec", 120.0),
            max_tokens=typed.get("max_tokens", 0),
            models=typed.get("models", []),
            capabilities=typed.get("capabilities") or _infer_provider_capabilities(provider_type),
        )
    return providers


def _parse_tasks(raw: dict) -> dict[str, TaskConfig]:
    tasks: dict[str, TaskConfig] = {}
    for name, cfg in (raw or {}).items():
        tasks[name] = TaskConfig(
            provider=cfg.get("provider", ""),
            model=cfg.get("model", ""),
        )
    return tasks


def _parse_whisper(raw: dict) -> WhisperConfig:
    cfg = WhisperConfig(**_filter_dc(raw, WhisperConfig))
    cfg.sanitize()
    return cfg

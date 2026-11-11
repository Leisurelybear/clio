from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from clio.ai.base import TaskName, TextAIProvider, VideoAIProvider, provider_supports_video
from clio.ai.gemini import GeminiProvider
from clio.ai.openai_compat import OpenAICompatProvider
from clio.config import AppConfig, TaskConfig
from clio.i18n import t

_PROVIDER_TYPES: dict[str, type[Any]] = {
    "gemini": GeminiProvider,
    "openai": OpenAICompatProvider,
    "openai_compat": OpenAICompatProvider,
    "ollama": OpenAICompatProvider,
}


@dataclass
class _CachedEntry:
    provider: TextAIProvider
    created_at: float
    closing: bool = False
    active_calls: int = 0


_provider_cache: dict[tuple, _CachedEntry] = {}
_retired_entries: list[_CachedEntry] = []
_provider_cache_lock = threading.Lock()


def _close_provider(provider: TextAIProvider) -> None:
    try:
        provider.close()
    except Exception:
        pass


def _find_entry(provider: TextAIProvider) -> _CachedEntry | None:
    for entry in _provider_cache.values():
        if entry.provider is provider:
            return entry
    return next((entry for entry in _retired_entries if entry.provider is provider), None)


@contextmanager
def provider_use(provider: TextAIProvider) -> Iterator[TextAIProvider]:
    """Keep a cached provider alive while one AI request is using it."""
    with _provider_cache_lock:
        entry = _find_entry(provider)
        if entry is not None:
            entry.active_calls += 1
    try:
        yield provider
    finally:
        to_close: TextAIProvider | None = None
        with _provider_cache_lock:
            entry = _find_entry(provider)
            if entry is not None:
                entry.active_calls = max(0, entry.active_calls - 1)
                if entry.closing and entry.active_calls == 0:
                    if entry in _retired_entries:
                        _retired_entries.remove(entry)
                    to_close = entry.provider
        if to_close is not None:
            _close_provider(to_close)


def _build_provider(config: AppConfig, provider_name: str) -> TextAIProvider:
    provider_cfg = config.ai.providers.get(provider_name)
    if not provider_cfg:
        raise ValueError(t("ai.provider_undefined", {"name": provider_name}))
    cache_key = (
        provider_name,
        provider_cfg.api_key,
        provider_cfg.base_url,
        provider_cfg.type,
        provider_cfg.poll_interval_sec,
        provider_cfg.timeout_sec,
        provider_cfg.max_tokens,
        provider_cfg.video_upload_max_mb,
        provider_cfg.retry_attempts,
        provider_cfg.requests_per_minute,
        config.proxy.url,
        config.proxy.enabled,
    )

    ttl_sec = float("inf") if config.ai.provider_ttl_min <= 0 else config.ai.provider_ttl_min * 60

    close_after_unlock: TextAIProvider | None = None
    with _provider_cache_lock:
        cached = _provider_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached.created_at < ttl_sec:
            if not cached.closing:
                return cached.provider
        if cached is not None:
            cached.closing = True
            del _provider_cache[cache_key]
            if cached.active_calls:
                _retired_entries.append(cached)
            else:
                close_after_unlock = cached.provider

    if close_after_unlock is not None:
        _close_provider(close_after_unlock)

    cls = _PROVIDER_TYPES.get(provider_cfg.type)
    if not cls:
        raise ValueError(
            t("ai.provider_type_unsupported", {"type": provider_cfg.type, "available": ", ".join(_PROVIDER_TYPES)})
        )
    provider = cls(provider_cfg, config.proxy)
    close_after_unlock = None
    result = provider
    with _provider_cache_lock:
        existing = _provider_cache.get(cache_key)
        if existing is not None and not existing.closing:
            close_after_unlock = provider
            result = existing.provider
        else:
            _provider_cache[cache_key] = _CachedEntry(provider=provider, created_at=time.monotonic())
    if close_after_unlock is not None:
        _close_provider(close_after_unlock)
    return result


def _clear_provider_cache() -> None:
    """Close all cached providers and clear the cache (for testing / config reload)."""
    with _provider_cache_lock:
        providers = [e.provider for e in _provider_cache.values()] + [e.provider for e in _retired_entries]
        _provider_cache.clear()
        _retired_entries.clear()
    for provider in providers:
        _close_provider(provider)


def get_task_config(config: AppConfig, task: TaskName | str) -> TaskConfig:
    task_name = task.value if isinstance(task, TaskName) else task
    task_cfg = config.ai.tasks.get(task_name)
    if not task_cfg:
        raise ValueError(t("ai.task_not_configured", {"task": task_name}))
    return task_cfg


def get_task_provider(config: AppConfig, task: TaskName | str) -> tuple[TextAIProvider, str]:
    task_cfg = get_task_config(config, task)
    provider = _build_provider(config, task_cfg.provider)
    return provider, task_cfg.model


def get_video_provider(config: AppConfig, task: TaskName | str) -> tuple[VideoAIProvider, str]:
    task_cfg = get_task_config(config, task)
    provider_cfg = config.ai.providers.get(task_cfg.provider)
    if provider_cfg is not None and not provider_supports_video(provider_cfg):
        raise ValueError(t("ai.task_requires_video", {"task": task, "provider": task_cfg.provider}))
    provider, model = get_task_provider(config, task)
    if not isinstance(provider, VideoAIProvider):
        raise ValueError(t("ai.task_provider_no_video", {"task": task}))
    return provider, model

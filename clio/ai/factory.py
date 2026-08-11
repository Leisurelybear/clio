from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from clio.ai.base import TaskName, TextAIProvider, VideoAIProvider, provider_supports_video
from clio.ai.gemini import GeminiProvider
from clio.ai.openai_compat import OpenAICompatProvider
from clio.config import AppConfig, TaskConfig

_PROVIDER_TYPES: dict[str, type[Any]] = {
    "gemini": GeminiProvider,
    "openai": OpenAICompatProvider,
    "openai_compat": OpenAICompatProvider,
}


@dataclass
class _CachedEntry:
    provider: TextAIProvider
    created_at: float
    closing: bool = False


_provider_cache: dict[tuple, _CachedEntry] = {}
_provider_cache_lock = threading.Lock()


def _build_provider(config: AppConfig, provider_name: str) -> TextAIProvider:
    provider_cfg = config.ai.providers.get(provider_name)
    if not provider_cfg:
        raise ValueError(f"未定义的 AI 厂家: {provider_name}")
    cache_key = (
        provider_name,
        provider_cfg.api_key,
        provider_cfg.base_url,
        provider_cfg.type,
        provider_cfg.poll_interval_sec,
        provider_cfg.timeout_sec,
        provider_cfg.max_tokens,
        provider_cfg.retry_attempts,
        provider_cfg.requests_per_minute,
        config.proxy.url,
        config.proxy.enabled,
    )

    ttl_sec = float("inf") if config.ai.provider_ttl_min <= 0 else config.ai.provider_ttl_min * 60

    with _provider_cache_lock:
        cached = _provider_cache.get(cache_key)
        if cached is not None and time.monotonic() - cached.created_at < ttl_sec:
            if not cached.closing:
                return cached.provider
        if cached is not None:
            cached.closing = True
            try:
                cached.provider.close()
            except Exception:
                pass
            del _provider_cache[cache_key]

    cls = _PROVIDER_TYPES.get(provider_cfg.type)
    if not cls:
        raise ValueError(f"不支持的厂家类型 '{provider_cfg.type}'，可选: {', '.join(_PROVIDER_TYPES)}")
    provider = cls(provider_cfg, config.proxy)
    with _provider_cache_lock:
        existing = _provider_cache.get(cache_key)
        if existing is not None and not existing.closing:
            provider.close()
            return existing.provider
        _provider_cache[cache_key] = _CachedEntry(provider=provider, created_at=time.monotonic())
    return provider


def _clear_provider_cache() -> None:
    """Close all cached providers and clear the cache (for testing / config reload)."""
    with _provider_cache_lock:
        providers = [e.provider for e in _provider_cache.values()]
        _provider_cache.clear()
    for p in providers:
        try:
            p.close()
        except Exception:
            pass


def get_task_config(config: AppConfig, task: TaskName | str) -> TaskConfig:
    task_name = task.value if isinstance(task, TaskName) else task
    task_cfg = config.ai.tasks.get(task_name)
    if not task_cfg:
        raise ValueError(f"未配置 AI 任务: {task_name}")
    return task_cfg


def get_task_provider(config: AppConfig, task: TaskName | str) -> tuple[TextAIProvider, str]:
    task_cfg = get_task_config(config, task)
    provider = _build_provider(config, task_cfg.provider)
    return provider, task_cfg.model


def get_video_provider(config: AppConfig, task: TaskName | str) -> tuple[VideoAIProvider, str]:
    task_cfg = get_task_config(config, task)
    provider_cfg = config.ai.providers.get(task_cfg.provider)
    if provider_cfg is not None and not provider_supports_video(provider_cfg):
        raise ValueError(
            f"任务 '{task}' 需要视频分析能力，但厂家 '{task_cfg.provider}' 不支持视频。"
            f"请在 config.yaml 中为 '{task_cfg.provider}' 配置 capabilities: [video] 或使用 gemini 厂家。"
        )
    provider, model = get_task_provider(config, task)
    if not isinstance(provider, VideoAIProvider):
        raise ValueError(f"任务 '{task}' 使用的厂家不支持视频分析")
    return provider, model

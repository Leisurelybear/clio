"""Dependency availability endpoints (ffmpeg/ffprobe, AI API keys)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from clio.ai.base import TaskName
from clio.doctor import provider_key_missing
from clio.utils import probe_ffmpeg_deps

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_REQUIRED_TASKS = (
    TaskName.VIDEO_ANALYZE.value,
    TaskName.VOICEOVER.value,
    TaskName.VLOG_PLAN.value,
)


def handle_get_deps_ffmpeg(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """GET /api/deps/ffmpeg — probe ffmpeg/ffprobe without side effects."""
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    paths = getattr(cfg, "paths", None)
    ffmpeg = getattr(paths, "ffmpeg", "") or ""
    ffprobe = getattr(paths, "ffprobe", "") or ""
    handler._send_json(probe_ffmpeg_deps(ffmpeg, ffprobe))


def handle_get_deps_keys(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """GET /api/deps/keys — report unbound tasks and referenced providers without a key.

    Never leaks key values; only presence + env var name are returned.
    """
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    tasks = cfg.ai.tasks
    unbound_tasks = [
        name
        for name in _REQUIRED_TASKS
        if not getattr(tasks.get(name), "provider", "") or not getattr(tasks.get(name), "model", "")
    ]
    referenced = sorted({task.provider for task in tasks.values() if getattr(task, "provider", "")})
    missing: list[dict[str, str]] = []
    for name in referenced:
        provider = cfg.ai.providers.get(name)
        if provider is None:
            missing.append(
                {
                    "provider": name,
                    "api_key_env": "",
                    "detail": f"任务引用了未配置的 provider '{name}'",
                }
            )
            continue
        detail = provider_key_missing(provider, os.environ)
        if detail:
            missing.append(
                {
                    "provider": name,
                    "api_key_env": provider.api_key_env or "",
                    "detail": detail,
                }
            )
    handler._send_json({"ok": True, "missing": missing, "unbound_tasks": unbound_tasks})

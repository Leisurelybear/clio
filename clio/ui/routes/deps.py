"""Dependency availability endpoints (ffmpeg/ffprobe, AI API keys)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from clio.ai.base import TaskName
from clio.doctor import provider_key_missing
from clio.ffmpeg_install import install_ffmpeg, normalize_system, select_asset
from clio.i18n import t
from clio.task_center.manager import TaskAlreadyRunningError, TaskManager
from clio.task_center.models import TaskKind, TaskStatus
from clio.task_center.reporter import TaskCancelled
from clio.task_center.store import TaskQuery
from clio.utils import probe_ffmpeg_deps

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_REQUIRED_TASKS = (
    TaskName.VIDEO_ANALYZE.value,
    TaskName.VOICEOVER.value,
    TaskName.VLOG_PLAN.value,
)


def _managed_task_manager(handler: HandlerProtocol) -> TaskManager | None:
    try:
        manager = handler._get_task_manager()
    except (AttributeError, TypeError):
        return None
    return manager if isinstance(manager, TaskManager) else None


def _run_ffmpeg_install_task(context) -> dict[str, Any]:
    def report(pct: int, message: str) -> None:
        context.reporter.progress(phase="ffmpeg_install", current=pct, total=100, message=message)

    result = install_ffmpeg(mode="download", reporter=report, cancel_event=context.cancel_event)
    if result.method == "cancelled":
        raise TaskCancelled(t("deps.ffmpeg.cancelled"))
    if not result.ok:
        raise RuntimeError(result.message or t("deps.ffmpeg.failed"))
    return {"ffmpeg": result.ffmpeg, "ffprobe": result.ffprobe, "method": result.method}


def _ensure_ffmpeg_install_handler(manager: TaskManager) -> None:
    manager.ensure_registered(
        TaskKind.FFMPEG_INSTALL,
        _run_ffmpeg_install_task,
        concurrency_key="ffmpeg-install",
        max_concurrency=1,
        cancellable=True,
    )


def _active_ffmpeg_task(manager: TaskManager):
    tasks = manager.store.list(
        TaskQuery(
            statuses=(TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING),
            kinds=(TaskKind.FFMPEG_INSTALL,),
            visibility=None,
            limit=1,
        )
    )
    return tasks[0] if tasks else None


def _ffmpeg_install_capabilities() -> dict[str, Any]:
    import platform as platform_module

    system = normalize_system()
    return {
        "platform": system,
        "automatic": select_asset(system, platform_module.machine()) is not None,
    }


def handle_get_deps_ffmpeg_install_status(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    del qs
    capabilities = _ffmpeg_install_capabilities()
    manager = _managed_task_manager(handler)
    if manager is None:
        return handler._send_json({**capabilities, "status": "idle", "running": False})
    task = _active_ffmpeg_task(manager)
    if task is None:
        return handler._send_json({**capabilities, "status": "idle", "running": False})
    return handler._send_json(
        {
            **capabilities,
            "status": task.status,
            "running": True,
            "task_id": task.id,
            "progress_pct": task.progress_pct or 0,
            "message": task.message,
            "cancellable": task.cancellable,
        }
    )


def handle_post_deps_ffmpeg_install(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    del qs
    manager = _managed_task_manager(handler)
    if manager is None:
        return handler._send_json({"ok": False, "error": t("deps.task_manager_unavailable")}, 503)
    capabilities = _ffmpeg_install_capabilities()
    if not capabilities["automatic"]:
        return handler._send_json(
            {
                **capabilities,
                "ok": False,
                "running": False,
                "error": t("deps.ffmpeg.unsupported_platform"),
            },
            400,
        )
    _ensure_ffmpeg_install_handler(manager)
    if _active_ffmpeg_task(manager) is not None:
        return handler._send_json({"ok": False, "error": t("deps.ffmpeg.already_running")}, 409)
    try:
        task = manager.submit(
            TaskKind.FFMPEG_INSTALL,
            t("deps.ffmpeg.task_title"),
            input_summary={"mode": "download"},
            reject_if_active=True,
        )
    except TaskAlreadyRunningError:
        return handler._send_json({"ok": False, "error": t("deps.ffmpeg.already_running")}, 409)
    return handler._send_json(
        {"ok": True, "message": t("deps.ffmpeg.started"), "task_id": task.id, "task": task.to_dict()}
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
                    "detail": t("deps.ffmpeg.provider_unconfigured", {"name": name}),
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

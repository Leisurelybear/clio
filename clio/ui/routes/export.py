"""Export routes for plan to video editing software drafts."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from clio.config import load_config
from clio.export import export_plan
from clio.plan_model import Plan
from clio.plan_readiness import (
    check_plan_export_readiness,
    collect_project_indices,
    readiness_block_payload,
)
from clio.task_center.manager import TaskAlreadyRunningError, TaskManager
from clio.task_center.models import TaskKind, TaskStatus
from clio.task_center.reporter import TaskCancelled
from clio.task_center.store import TaskQuery
from clio.ui.handler_protocol import HandlerProtocol
from clio.ui.services.file_service import _is_safe_basename
from clio.utils import resolve_binary, write_bytes_atomic


def _managed_task_manager(handler: Any) -> TaskManager | None:
    try:
        manager = handler._get_task_manager()
    except (AttributeError, TypeError):
        return None
    return manager if isinstance(manager, TaskManager) else None


def _copy_draft_to_jianying(
    draft_output_dir: Path,
    jianying_draft_dir: str,
    day_label: str,
) -> Path | None:
    """Copy generated draft to JianYing draft directory.

    Returns the target draft directory path, or None if skipped.
    """
    if not jianying_draft_dir:
        return None
    target_base = Path(os.path.expanduser(jianying_draft_dir))
    if not target_base.is_dir():
        return None

    draft_name = f"vlog_export_{day_label}"
    target_dir = target_base / draft_name
    target_dir.mkdir(parents=True, exist_ok=True)

    src = draft_output_dir / "draft_content.json"
    if not src.is_file():
        return None

    dst = target_dir / "draft_content.json"
    write_bytes_atomic(dst, src.read_bytes())
    return target_dir


def _run_export_task(context) -> dict[str, Any]:
    data = context.input_data
    project_dir = Path(context.task.project_path or data["project_dir"])
    cfg = load_config(data.get("config_path") or "config.yaml", project_dir=project_dir)
    day = str(data.get("day", "day1"))
    fmt = str(data.get("format", "jianying"))
    plan_path = cfg.plans_dir / f"{day}_plan.json"
    out_dir = cfg.paths.output_dir / "export" / f"{day}_{fmt}"
    context.reporter.progress(phase="export", current=0, total=1, message="正在生成剪映草稿…")
    context.reporter.raise_if_cancelled()
    result_path = export_plan(
        fmt,
        plan_path,
        out_dir,
        day_label=day,
        project_dir=cfg.project_dir or project_dir,
        ffprobe=resolve_binary(cfg.paths.ffprobe, "ffprobe"),
        texts_dir=cfg.texts_dir,
        canvas_ratio=cfg.export.canvas_ratio,
        index_width=cfg.naming.index_width,
    )
    context.reporter.raise_if_cancelled()
    copied = None
    if cfg.export.auto_copy_draft and cfg.export.jianying_draft_dir:
        copied = _copy_draft_to_jianying(result_path, cfg.export.jianying_draft_dir, day)
    if context.cancel_event.is_set():
        raise TaskCancelled("草稿导出已取消")
    context.reporter.progress(phase="export", current=1, total=1, message="剪映草稿已生成")
    result: dict[str, Any] = {"day": day, "format": fmt, "artifact": f"export/{day}_{fmt}"}
    if copied is not None:
        result["copied_to_jianying"] = copied.name
    return result


def _ensure_export_handler(manager: TaskManager) -> None:
    manager.ensure_registered(
        TaskKind.EXPORT,
        _run_export_task,
        concurrency_key=lambda task: (
            f"export:{task.project_id or task.project_path}:"
            f"{task.input_data.get('day', 'day1')}:{task.input_data.get('format', 'jianying')}"
        ),
        max_concurrency=1,
        cancellable=True,
    )


def _active_export_task(manager: TaskManager, project_id: str, day: str, fmt: str):
    tasks = manager.store.list(
        TaskQuery(
            project_id=project_id,
            statuses=(TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING),
            kinds=(TaskKind.EXPORT,),
            visibility=None,
            limit=50,
        )
    )
    return next(
        (task for task in tasks if task.input_data.get("day") == day and task.input_data.get("format") == fmt),
        None,
    )


def handle_post_export(
    handler: HandlerProtocol,
    qs: dict[str, list[str]],
    obj: dict,
) -> None:
    """POST /api/export — export plan to JianYing draft."""
    day = obj.get("day", "day1")
    if not isinstance(day, str) or not _is_safe_basename(day):
        handler._send_json({"ok": False, "error": "invalid day"}, 400)
        return
    fmt = obj.get("format", "jianying")
    force = obj.get("force", False)
    if not isinstance(force, bool):
        return handler._send_json({"ok": False, "error": "force must be a boolean"}, 400)

    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)

    plan_path = cfg.plans_dir / f"{day}_plan.json"
    if not plan_path.is_file():
        handler._send_json({"ok": False, "error": f"plan 文件不存在: {plan_path}"}, 404)
        return

    try:
        plan = Plan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        handler._send_json({"ok": False, "error": str(e)}, 400)
        return

    known, offline = collect_project_indices(cfg)
    result = check_plan_export_readiness(plan, known_indices=known, offline_indices=offline, source="original")
    blocked = readiness_block_payload(result, force=force)
    if blocked is not None:
        handler._send_json(blocked, 400)
        return

    manager = _managed_task_manager(handler)
    if manager is not None:
        _ensure_export_handler(manager)
        project_id = str(Path(proj_dir).resolve())
        if _active_export_task(manager, project_id, day, fmt) is not None:
            return handler._send_json({"ok": False, "error": "同一草稿导出任务正在运行"}, 409)
        config_path = getattr(handler, "config_path", None)
        try:
            task = manager.submit(
                TaskKind.EXPORT,
                f"导出剪映草稿: {day}",
                project_id=project_id,
                project_name=Path(proj_dir).name,
                project_path=project_id,
                input_data={
                    "config_path": str(config_path) if isinstance(config_path, Path) else None,
                    "project_dir": project_id,
                    "day": day,
                    "format": fmt,
                },
                input_summary={"day": day, "format": fmt, "segment_count": len(plan.sequence)},
                reject_if_active=True,
            )
        except TaskAlreadyRunningError:
            return handler._send_json({"ok": False, "error": "同一草稿导出任务正在运行"}, 409)
        return handler._send_json(
            {
                "ok": True,
                "started": True,
                "task_id": task.id,
                "task": task.to_dict(),
                "artifact": f"export/{day}_{fmt}",
            },
            202,
        )

    state = handler._get_state(str(Path(proj_dir).resolve()))
    with state.job_lock:
        if state.job_thread is not None and state.job_thread.is_alive():
            return handler._send_json({"ok": False, "error": "另一个任务正在运行"}, 409)

        out_dir = cfg.paths.output_dir / "export" / f"{day}_{fmt}"
        try:
            result_path = export_plan(
                fmt,
                plan_path,
                out_dir,
                day_label=day,
                project_dir=cfg.project_dir or proj_dir,
                ffprobe=resolve_binary(cfg.paths.ffprobe, "ffprobe"),
                texts_dir=cfg.texts_dir,
                canvas_ratio=cfg.export.canvas_ratio,
                index_width=cfg.naming.index_width,
            )
        except (FileNotFoundError, ValueError) as e:
            return handler._send_json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            return handler._send_json({"ok": False, "error": str(e)}, 500)

    result_body = {"ok": True, "path": str(result_path)}

    if cfg.export.auto_copy_draft and cfg.export.jianying_draft_dir:
        jy_dir = _copy_draft_to_jianying(result_path, cfg.export.jianying_draft_dir, day)
        if jy_dir:
            result_body["jianying_draft"] = str(jy_dir)

    handler._send_json(result_body)

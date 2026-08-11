"""Export routes for plan to video editing software drafts."""

from __future__ import annotations

import json
import os
from pathlib import Path

from clio.export import export_plan
from clio.plan_model import Plan
from clio.plan_readiness import (
    check_plan_export_readiness,
    collect_project_indices,
    readiness_block_payload,
)
from clio.ui.handler_protocol import HandlerProtocol
from clio.ui.services.file_service import _is_safe_basename


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
    dst.write_bytes(src.read_bytes())
    return target_dir


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
                ffprobe=cfg.paths.ffprobe,
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

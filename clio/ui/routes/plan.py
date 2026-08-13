"""Route handlers: /api/plan, /api/plans, /api/cut, /api/plan/readiness"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio.pipeline import run_cut_all
from clio.plan_model import Plan
from clio.plan_readiness import (
    check_plan_export_readiness,
    collect_project_indices,
    readiness_block_payload,
)
from clio.schema import add_schema_version
from clio.tasks.cut import (
    list_existing_cut_videos,
    list_orphaned_cut_backups,
    resolve_cut_output_dir,
    restore_orphaned_cut_backups,
)
from clio.ui.services.file_service import _is_safe_basename, _save_atomic

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


def _plans_dir(handler: HandlerProtocol, qs: dict[str, Any]) -> Path:
    """Resolve plans directory from AppConfig (honors custom plans_subdir)."""
    proj_dir = handler._resolve_project_dir(qs)
    return handler._get_config(proj_dir).plans_dir


def handle_get_plans(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/plans."""
    plans_dir = _plans_dir(handler, qs)
    plans = []
    if plans_dir.is_dir():
        for p in sorted(plans_dir.glob("*_plan.json")):
            day_label = p.stem.replace("_plan", "")
            if day_label:
                plans.append({"day_label": day_label, "path": str(p)})
    handler._send_json({"plans": plans})


def handle_get_plan(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/plan."""
    day = qs.get("day", [""])[0]
    if not _is_safe_basename(day) or not day:
        return handler._send_json({"error": "forbidden"}, 403)
    p = _plans_dir(handler, qs) / f"{day}_plan.json"
    if not p.is_file():
        return handler._send_json({"error": f"规划文件不存在: {p}"}, 404)
    handler._send_bytes(p.read_bytes(), "application/json; charset=utf-8")


def handle_put_plan(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/plan."""
    day = qs.get("day", [""])[0]
    if not _is_safe_basename(day) or not day:
        return handler._send_json({"ok": False, "error": "forbidden"}, 403)
    p = _plans_dir(handler, qs) / f"{day}_plan.json"
    plan = Plan.from_dict(obj if isinstance(obj, dict) else {})
    issues = plan.validate_for_save()
    if issues:
        return handler._send_json(
            {
                "ok": False,
                "error": issues[0].message,
                "issues": [i.to_dict() for i in issues],
            },
            400,
        )
    data = add_schema_version(plan.to_dict())
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    p.parent.mkdir(parents=True, exist_ok=True)
    _save_atomic(p, payload)
    handler._send_json({"ok": True, "path": str(p)})


def handle_post_plan_readiness(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """POST /api/plan/readiness — optional inline plan body for dirty buffer checks."""
    body = obj if isinstance(obj, dict) else {}
    day = body.get("day") or (qs.get("day", ["day1"])[0] if qs else "day1")
    day = str(day)
    if not _is_safe_basename(day) or not day:
        return handler._send_json({"ok": False, "error": "forbidden"}, 403)
    source = str(body.get("source") or "compressed")
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)

    inline = body.get("plan")
    if isinstance(inline, dict):
        plan = Plan.from_dict(inline)
    else:
        path = cfg.plans_dir / f"{day}_plan.json"
        if not path.is_file():
            return handler._send_json(
                {
                    "ok": False,
                    "errors": [
                        {
                            "level": "error",
                            "code": "plan_missing",
                            "message": f"规划文件不存在: {path}",
                        }
                    ],
                    "warnings": [],
                },
                404,
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            return handler._send_json({"ok": False, "error": str(e)}, 400)
        plan = Plan.from_dict(raw if isinstance(raw, dict) else {})

    known, offline = collect_project_indices(cfg)
    result = check_plan_export_readiness(plan, known_indices=known, offline_indices=offline, source=source)
    handler._send_json(result.to_dict())


def handle_post_cut(handler: HandlerProtocol, qs: dict[str, list[str]], obj: dict) -> None:
    """Handle POST /api/cut."""
    day_label = obj.get("day_label", "day1")
    if not isinstance(day_label, str) or not _is_safe_basename(day_label):
        return handler._send_json({"ok": False, "error": "invalid day_label"}, 400)
    source = obj.get("source", "compressed")
    reencode = obj.get("reencode", False)
    if not isinstance(reencode, bool):
        return handler._send_json({"ok": False, "error": "reencode must be a boolean"}, 400)
    out_dir_raw = obj.get("output_dir", None)
    force = obj.get("force", False)
    if not isinstance(force, bool):
        return handler._send_json({"ok": False, "error": "force must be a boolean"}, 400)
    overwrite = obj.get("overwrite", False)
    if not isinstance(overwrite, bool):
        return handler._send_json({"ok": False, "error": "overwrite must be a boolean"}, 400)

    if source not in ("compressed", "original"):
        return handler._send_json({"ok": False, "error": "source must be compressed|original"}, 400)

    out_path = Path(out_dir_raw) if out_dir_raw else None
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    try:
        actual_out_path = resolve_cut_output_dir(cfg, day_label, out_path)
    except ValueError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)

    plan_path = cfg.plans_dir / f"{day_label}_plan.json"
    if not plan_path.is_file():
        return handler._send_json({"ok": False, "error": f"规划文件不存在: {plan_path}"}, 404)
    try:
        plan = Plan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError) as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)
    known, offline = collect_project_indices(cfg)
    result = check_plan_export_readiness(plan, known_indices=known, offline_indices=offline, source=str(source))
    blocked = readiness_block_payload(result, force=force)
    if blocked is not None:
        return handler._send_json(blocked, 400)

    existing = list_existing_cut_videos(actual_out_path)
    if existing and not overwrite:
        preview = existing[:12]
        more = len(existing) - len(preview)
        hint = "、".join(preview) + (f" 等 {more} 个" if more > 0 else "")
        return handler._send_json(
            {
                "ok": False,
                "error": f"输出目录已有 {len(existing)} 个裁剪视频，确认覆盖后重试",
                "code": "cut_output_exists",
                "count": len(existing),
                "files": existing,
                "preview": hint,
                "output_dir": str(actual_out_path),
            },
            409,
        )

    state = handler._get_state(str(proj_dir.resolve()))
    with state.job_lock:
        if state.job_thread is not None and state.job_thread.is_alive():
            return handler._send_json({"ok": False, "error": "另一个任务正在运行"}, 409)
        state.job_cancel_event.clear()

        def _cut_job():
            try:
                run_cut_all(
                    cfg,
                    day_label=day_label,
                    output_dir=out_path,
                    reencode=bool(reencode),
                    source=source,
                    overwrite=True,
                    cancel_event=state.job_cancel_event,
                )
            except Exception as e:
                print(f"[cut] 失败: {e}")
            finally:
                with state.job_lock:
                    state.job_thread = None

        state.job_thread = threading.Thread(target=_cut_job, daemon=True)
        state.job_thread.start()

    handler._send_json(
        {
            "ok": True,
            "output_dir": str(actual_out_path),
            "day_label": day_label,
            "started": True,
        }
    )


def handle_get_cut_orphaned_backups(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """GET /api/cut/orphaned-backups — leftover *.clio_bak from interrupted re-cuts."""
    proj_out = handler._get_project_output(qs)
    items = list_orphaned_cut_backups(proj_out)
    handler._send_json({"ok": True, "count": len(items), "items": items})


def handle_post_cut_restore_backups(handler: HandlerProtocol, qs: dict[str, list[str]], obj: dict) -> None:
    """POST /api/cut/restore-backups — restore orphaned cut backups (old files).

    When a target and its backup coexist, restoring would discard a completed
    new cut, so the client must pass keep_target=true to keep new files or
    false to restore old files (default). Conflicting items without an
    explicit decision are reported with HTTP 409 (GAP-P1-06).
    """
    body = obj if isinstance(obj, dict) else {}
    only = body.get("paths")
    if only is not None and not isinstance(only, list):
        return handler._send_json({"ok": False, "error": "paths must be a list"}, 400)
    keep_target = body.get("keep_target") if "keep_target" in body else None
    proj_out = handler._get_project_output(qs)
    result = restore_orphaned_cut_backups(proj_out, only=only, keep_target=keep_target)
    if result["conflicts"]:
        return handler._send_json(
            {
                "ok": False,
                "error": "目标与备份共存，需明确选择保留新版或恢复旧版",
                "conflicts": result["conflicts"],
            },
            409,
        )
    handler._send_json({"ok": True, **result})

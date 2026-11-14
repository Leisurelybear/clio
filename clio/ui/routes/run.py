"""Route handlers: /api/run/start, /api/rerun"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio._constants import VIDEO_EXTS
from clio.config import load_config
from clio.i18n import t
from clio.pipeline import (
    PIPELINE_STEPS,
    SINGLE_VIDEO_RERUN_STEPS,
    run_analyze_all,
    run_compress_all,
    run_generate_scripts,
    run_pipeline_steps,
)
from clio.progress import ProgressTracker
from clio.task_center.executor import project_artifact_key
from clio.task_center.manager import TaskAlreadyRunningError, TaskManager
from clio.task_center.models import TaskKind, TaskStatus
from clio.task_center.reporter import TaskCancelled, TaskProgressReporter
from clio.task_center.store import TaskQuery
from clio.tasks._video_loader import load_selected_videos
from clio.tasks.plan import plan_overwrite_confirmation_required
from clio.tasks.transcribe import run_transcribe_one
from clio.ui.services.file_service import _find_original_for_compressed, _find_texts_dirs, _is_safe_basename
from clio.ui.services.project_service import _project_output_dir, collect_allowed_project_paths
from clio.ui.services.run_preview import (
    analysis_rerun_risk,
    artifact_overwrite_paths,
    build_run_preview,
    legacy_audio_removal_risk,
)
from clio.utils import media_dependencies_for_steps, media_dependency_error, preflight_config_media_deps
from clio.vmeta import VideoMeta

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


def _managed_task_manager(handler: Any) -> TaskManager | None:
    """Return the real manager on an HTTP handler; legacy test doubles return MagicMock."""
    try:
        manager = handler._get_task_manager()
    except (AttributeError, TypeError):
        return None
    return manager if isinstance(manager, TaskManager) else None


def _active_managed_run(manager: TaskManager, project_id: str):
    tasks = manager.store.list(
        TaskQuery(
            project_id=project_id,
            statuses=(TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING),
            kinds=(TaskKind.PIPELINE, TaskKind.RERUN),
            visibility=None,
            limit=1,
        )
    )
    return tasks[0] if tasks else None


def _ensure_run_handlers(manager: TaskManager) -> None:
    manager.ensure_registered(
        TaskKind.PIPELINE,
        _run_pipeline_task,
        concurrency_key=project_artifact_key,
        max_concurrency=1,
        cancellable=True,
    )
    manager.ensure_registered(
        TaskKind.RERUN,
        _run_rerun_task,
        concurrency_key=project_artifact_key,
        max_concurrency=1,
        cancellable=True,
    )


def _task_config(context) -> Any:
    inherited = context.input_data.get("_config")
    if inherited is not None:
        return copy.deepcopy(inherited)
    config_path = context.input_data.get("config_path") or "config.yaml"
    project_path = Path(context.task.project_path or context.input_data["project_dir"])
    cfg = load_config(config_path, project_dir=project_path)
    cfg = copy.deepcopy(cfg)
    if "use_transcripts" in context.input_data:
        cfg.plan.use_transcripts = bool(context.input_data["use_transcripts"])
    return cfg


def _run_pipeline_task(context) -> dict[str, Any]:
    cfg = _task_config(context)
    input_data = context.input_data
    legacy = ProgressTracker(cfg.paths.output_dir)
    tracker = TaskProgressReporter(context.reporter, legacy)
    try:
        result = run_pipeline_steps(
            cfg,
            input_data.get("day_label", "day1"),
            input_data.get("steps"),
            tracker=tracker,
            cancel_event=context.cancel_event,
            files=input_data.get("files"),
            all_days=bool(input_data.get("all_days", False)),
            overwrite=bool(input_data.get("overwrite", False)),
            confirm_plan_overwrite=bool(input_data.get("confirm_plan_overwrite", False)),
            confirm_artifact_overwrite=bool(input_data.get("confirm_artifact_overwrite", False)),
            context_override=input_data.get("context_override"),
            task_prompts=input_data.get("task_prompts"),
        )
    except TaskCancelled:
        tracker.cancelled(t("task.cancelled"))
        raise
    except Exception as e:
        tracker.error(f"pipeline failed: {e}")
        raise
    return result


def _run_rerun_task(context) -> dict[str, Any]:
    input_data = context.input_data
    cfg = _task_config(context)
    cfg.analyze.skip_existing = False
    confirm_artifact_overwrite = bool(input_data.get("confirm_artifact_overwrite", False))
    video_basename = str(input_data["video_basename"])
    task_name = str(input_data["task"])
    original_video = Path(input_data["original_video"])
    texts_raw = input_data.get("texts_json")
    texts_json = Path(texts_raw) if texts_raw else None
    legacy = ProgressTracker(cfg.paths.output_dir, rerun=True, rerun_video=video_basename)
    tracker = TaskProgressReporter(context.reporter, legacy)

    def _log(message: str) -> None:
        print(f"  [rerun] {message}")
        tracker.log(message)

    def _run_voiceover_step() -> Any:
        return run_generate_scripts(
            cfg,
            tracker=tracker,
            single_file=texts_json,
            cancel_event=context.cancel_event,
            confirm_artifact_overwrite=confirm_artifact_overwrite,
        )

    def _run_transcribe_step() -> Any:
        result = run_transcribe_one(
            cfg,
            original_video,
            cancel_event=context.cancel_event,
            progress_callback=lambda pct: tracker.update(
                phase="transcribe",
                current=pct,
                total=100,
                message=t("task.transcribing", {"video": video_basename, "pct": pct}),
            ),
        )
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    step_runners = {
        "compress": lambda: run_compress_all(
            cfg,
            tracker=tracker,
            single_file=original_video,
            cancel_event=context.cancel_event,
            overwrite=True,
        ),
        "transcribe": _run_transcribe_step,
        "analyze": lambda: run_analyze_all(
            cfg,
            tracker=tracker,
            single_file=original_video,
            cancel_event=context.cancel_event,
            confirm_artifact_overwrite=confirm_artifact_overwrite,
        ),
        "voiceover": _run_voiceover_step,
    }
    step_labels = {
        "compress": t("task.step.compress"),
        "transcribe": t("run.step.transcribe.label"),
        "analyze": t("task.step.analyze"),
        "voiceover": t("task.step.voiceover"),
    }

    try:
        _log(f"▶ Starting rerun {task_name} — {video_basename}")
        for step_name in SINGLE_VIDEO_RERUN_STEPS:
            if task_name not in (step_name, "all"):
                continue
            context.reporter.raise_if_cancelled()
            step_label = step_labels[step_name]
            _log(f"Step: {step_label}...")
            step_result = step_runners[step_name]()
            if task_name == "all" and step_name == "analyze":
                texts_json = next(
                    (
                        Path(record.text_path).with_suffix(".json")
                        for record in (step_result or [])
                        if getattr(record, "text_path", None) is not None
                    ),
                    None,
                )
                if texts_json is None:
                    raise RuntimeError(f"no analysis result found for {video_basename}")
            _log(f"✓ {step_label} complete")
        context.reporter.raise_if_cancelled()
        tracker.done(f"{task_name} → {video_basename} complete")
    except TaskCancelled:
        tracker.cancelled(t("task.cancelled"))
        raise
    except Exception as e:
        tracker.error(f"rerun failed: {e}")
        raise
    return {"task": task_name, "video": video_basename}


def _resolve_run_project_dir(
    proj_dir: Path,
    project_dir_raw: object,
    *,
    allowed_paths: set[str] | None = None,
) -> tuple[Path, str | None]:
    """Resolve the single final project dir a run targets (P1-P31).

    A body-level ``project_dir`` (legacy ``input_dir``) overrides the
    query-resolved *proj_dir*; the final dir alone must feed config, output,
    state, lock and tasks so they cannot split. When *allowed_paths* is set,
    the override must be in that registry/serve allowlist (R-033a).
    """
    if project_dir_raw is None:
        return proj_dir, None
    if not isinstance(project_dir_raw, str):
        return proj_dir, "project_dir must be a string"
    project_dir_raw = project_dir_raw.strip()
    if not project_dir_raw:
        return proj_dir, None
    project_dir = Path(project_dir_raw).expanduser()
    if not project_dir.is_dir():
        return proj_dir, f"project_dir not found: {project_dir_raw}"
    resolved = project_dir.resolve()
    if allowed_paths is not None and str(resolved) not in allowed_paths:
        return proj_dir, f"project_dir not allowed: {project_dir_raw}"
    return resolved, None


def _apply_run_project_dir_override(
    cfg,
    project_dir_raw: str | None,
    *,
    allowed_paths: set[str] | None = None,
) -> tuple[Any, str | None]:
    """Backward-compatible config-copy override (legacy callers/tests).

    Prefer ``_resolve_run_project_dir`` + reloading the config for the final
    dir; this helper only swaps ``_project_dir`` on a deep copy of *cfg*.
    """
    base = getattr(cfg, "_project_dir", None) or getattr(cfg, "project_dir", None) or Path()
    resolved, error = _resolve_run_project_dir(base, project_dir_raw, allowed_paths=allowed_paths)
    if error is not None or resolved == base:
        return cfg, error
    run_cfg = copy.deepcopy(cfg)
    run_cfg._project_dir = resolved
    return run_cfg, None


# Backward-compatible alias used by tests
def _apply_run_input_dir_override(
    cfg, input_dir_raw: str | None, *, allowed_paths: set[str] | None = None
) -> tuple[Any, str | None]:
    return _apply_run_project_dir_override(cfg, input_dir_raw, allowed_paths=allowed_paths)


def _resolve_found_original(orig: str | None, proj_dir: Path) -> Path | None:
    """Turn _find_original_for_compressed result (abs path or basename) into a Path."""
    if not orig:
        return None
    p = Path(orig)
    if p.is_file():
        return p.resolve()
    if not p.is_absolute():
        cand = (proj_dir / p).resolve()
        if cand.is_file():
            return cand
    # Match by basename against videos.json
    name = p.name
    for sel in load_selected_videos(proj_dir):
        if sel.name == name and sel.is_file():
            return sel.resolve()
    return None


def _is_safe_video_reference(value: str) -> bool:
    """Allow a plain filename or a safe project-relative path (nested originals)."""
    if not value or value.startswith(("/", "\\")) or Path(value).is_absolute() or Path(value).drive:
        return False
    parts = [part for part in value.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return False
    return all(_is_safe_basename(part) for part in parts)


@dataclass
class _RunStartOptions:
    proj_dir: Path
    config_path: Path | None
    cfg: Any
    day_label: str
    steps: list[str] | None
    files_list: list[str] | None
    all_days: bool
    overwrite: bool
    confirm_plan_overwrite: bool
    confirm_artifact_overwrite: bool
    confirm_legacy_audio_removal: bool
    confirm_analysis_rerun: bool
    context_override: str | None
    task_prompts: dict[str, str] | None

    @property
    def selected_steps(self) -> list[str]:
        return self.steps if self.steps is not None else list(PIPELINE_STEPS)


def _bool_field(obj: dict[str, Any], key: str, default: bool = False) -> tuple[bool | None, str | None]:
    value = obj.get(key, default)
    if not isinstance(value, bool):
        return None, f"{key} must be a boolean"
    return value, None


def _validate_run_steps(steps: Any) -> tuple[list[str] | None, str | None]:
    if steps is None:
        return None, None
    if not isinstance(steps, list):
        return None, "steps must be a list"
    if not all(isinstance(step, str) for step in steps):
        return None, "steps items must be strings"
    unknown = sorted(set(steps) - set(PIPELINE_STEPS))
    if unknown:
        return None, f"unknown pipeline step(s): {', '.join(unknown)}"
    return steps, None


def _validate_run_files(files_list: Any) -> tuple[list[str] | None, str | None]:
    if files_list is None:
        return None, None
    if not isinstance(files_list, list):
        return None, "files must be a list of video names"
    if not all(isinstance(item, str) for item in files_list):
        return None, "files items must be strings"
    return files_list, None


def _validate_context_override(value: Any) -> tuple[str | None, str | None]:
    if value is not None and not isinstance(value, str):
        return None, "context_override must be a string"
    if isinstance(value, str) and len(value) > 100_000:
        return None, "context_override is too large"
    return value or None, None


def _validate_task_prompts(value: Any) -> tuple[dict[str, str] | None, str | None]:
    if value is not None and (
        not isinstance(value, dict)
        or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    ):
        return None, "task_prompts must be an object of strings"
    prompts = value or None
    if prompts and (len(prompts) > 20 or any(len(item) > 100_000 for item in prompts.values())):
        return None, "task_prompts is too large"
    return prompts, None


def _resolve_run_start_project(
    handler: HandlerProtocol,
    qs: dict[str, Any],
    obj: dict[str, Any],
) -> tuple[Path | None, Path | None, Any | None, str | None]:
    proj_dir = handler._resolve_project_dir(qs)
    config_path = getattr(handler, "config_path", None)
    if not isinstance(config_path, Path):
        config_path = None
    allowed = collect_allowed_project_paths(proj_dir, config_path)
    proj_dir, cfg_error = _resolve_run_project_dir(
        proj_dir,
        obj.get("project_dir") if obj.get("project_dir") is not None else obj.get("input_dir"),
        allowed_paths=allowed,
    )
    if cfg_error:
        return None, config_path, None, cfg_error
    cfg = copy.deepcopy(handler._get_config(proj_dir))
    return proj_dir, config_path, cfg, None


def _parse_run_start_options(
    handler: HandlerProtocol,
    qs: dict[str, Any],
    obj: dict[str, Any],
) -> _RunStartOptions | None:
    day_label = obj.get("day_label", "day1")
    if not isinstance(day_label, str) or not _is_safe_basename(day_label):
        handler._send_json({"ok": False, "error": "invalid day_label"}, 400)
        return None
    proj_dir, config_path, cfg, cfg_error = _resolve_run_start_project(handler, qs, obj)
    if cfg_error or proj_dir is None or cfg is None:
        handler._send_json({"ok": False, "error": cfg_error}, 400)
        return None
    if "use_transcripts" in obj:
        if not isinstance(obj["use_transcripts"], bool):
            handler._send_json({"ok": False, "error": "use_transcripts must be a boolean"}, 400)
            return None
        cfg.plan.use_transcripts = obj["use_transcripts"]
    steps, error = _validate_run_steps(obj.get("steps"))
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    files_list, error = _validate_run_files(obj.get("files"))
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    all_days, error = _bool_field(obj, "all_days")
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    if all_days and files_list is not None:
        handler._send_json(
            {"ok": False, "error": "all_days cannot be combined with an explicit file selection"},
            400,
        )
        return None
    overwrite, error = _bool_field(obj, "overwrite")
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    confirm_plan_overwrite, error = _bool_field(obj, "confirm_plan_overwrite")
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    confirm_artifact_overwrite, error = _bool_field(obj, "confirm_artifact_overwrite")
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    confirm_legacy_audio_removal, error = _bool_field(obj, "confirm_legacy_audio_removal")
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    confirm_analysis_rerun, error = _bool_field(obj, "confirm_analysis_rerun")
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    context_override, error = _validate_context_override(obj.get("context_override"))
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    task_prompts, error = _validate_task_prompts(obj.get("task_prompts"))
    if error:
        handler._send_json({"ok": False, "error": error}, 400)
        return None
    return _RunStartOptions(
        proj_dir=proj_dir,
        config_path=config_path,
        cfg=cfg,
        day_label=day_label,
        steps=steps,
        files_list=files_list,
        all_days=bool(all_days),
        overwrite=bool(overwrite),
        confirm_plan_overwrite=bool(confirm_plan_overwrite),
        confirm_artifact_overwrite=bool(confirm_artifact_overwrite),
        confirm_legacy_audio_removal=bool(confirm_legacy_audio_removal),
        confirm_analysis_rerun=bool(confirm_analysis_rerun),
        context_override=context_override,
        task_prompts=task_prompts,
    )


def _run_start_confirmation_error(options: _RunStartOptions) -> tuple[dict[str, Any], int] | None:
    cfg = options.cfg
    selected_steps = options.selected_steps
    if (
        "plan" in selected_steps
        and not options.confirm_plan_overwrite
        and plan_overwrite_confirmation_required(cfg, options.day_label, options.files_list)
    ):
        return (
            {
                "ok": False,
                "code": "plan_overwrite_confirmation_required",
                "error": t("task.plan.overwrite_confirmation_required", {"day": options.day_label}),
                "day_label": options.day_label,
            },
            409,
        )
    artifact_paths = artifact_overwrite_paths(
        cfg,
        selected_steps,
        force=options.overwrite,
        use_transcripts=cfg.plan.use_transcripts,
        files=options.files_list,
        day_label=options.day_label,
        all_days=options.all_days,
        context_override=options.context_override,
        task_prompts=options.task_prompts,
    )
    if artifact_paths and not options.confirm_artifact_overwrite:
        items = [path.name for path in artifact_paths]
        return (
            {
                "ok": False,
                "code": "artifact_overwrite_confirmation_required",
                "error": t("task.artifact.overwrite_confirmation_required", {"items": ", ".join(items)}),
                "items": items,
            },
            409,
        )
    legacy_audio_risk = legacy_audio_removal_risk(
        cfg,
        selected_steps,
        force=options.overwrite,
        use_transcripts=cfg.plan.use_transcripts,
        files=options.files_list,
        context_override=options.context_override,
        task_prompts=options.task_prompts,
    )
    if legacy_audio_risk is not None and not options.confirm_legacy_audio_removal:
        return (
            {
                "ok": False,
                "code": "legacy_audio_removal_confirmation_required",
                "error": t(
                    "task.legacy_audio_removal.confirmation_required",
                    {
                        "analyze_count": legacy_audio_risk["analyze_count"],
                        "voiceover_count": legacy_audio_risk["voiceover_count"],
                        "compress_count": legacy_audio_risk["compress_count"],
                    },
                ),
                "legacy_audio_removal": legacy_audio_risk,
            },
            409,
        )
    analysis_risk = analysis_rerun_risk(
        cfg,
        selected_steps,
        force=options.overwrite,
        use_transcripts=cfg.plan.use_transcripts,
        files=options.files_list,
        context_override=options.context_override,
        task_prompts=options.task_prompts,
    )
    if analysis_risk is not None and not options.confirm_analysis_rerun:
        return (
            {
                "ok": False,
                "code": "analysis_rerun_confirmation_required",
                "error": t("task.analysis_rerun.confirmation_required", {"count": analysis_risk["count"]}),
                "analysis_rerun": analysis_risk,
            },
            409,
        )
    return None


def _submit_run_start(handler: HandlerProtocol, options: _RunStartOptions) -> None:
    preflight = preflight_config_media_deps(
        options.cfg,
        required=media_dependencies_for_steps(options.steps),
    )
    if not preflight["ok"]:
        handler._send_json(media_dependency_error(preflight), 424)
        return
    manager = _managed_task_manager(handler)
    if manager is None:
        handler._send_json({"ok": False, "error": "task center unavailable"}, 500)
        return
    _ensure_run_handlers(manager)
    project_id = str(options.proj_dir.resolve())
    if _active_managed_run(manager, project_id) is not None:
        handler._send_json({"ok": False, "error": t("task.project_busy")}, 409)
        return
    input_data = {
        "config_path": str(options.config_path) if options.config_path is not None else None,
        "project_dir": project_id,
        "day_label": options.day_label,
        "steps": options.steps,
        "files": options.files_list,
        "all_days": options.all_days,
        "overwrite": options.overwrite,
        "confirm_plan_overwrite": options.confirm_plan_overwrite,
        "confirm_artifact_overwrite": options.confirm_artifact_overwrite,
        "confirm_legacy_audio_removal": options.confirm_legacy_audio_removal,
        "confirm_analysis_rerun": options.confirm_analysis_rerun,
        "use_transcripts": options.cfg.plan.use_transcripts,
    }
    retryable = not (options.context_override or options.task_prompts)
    input_summary = {
        "steps": list(options.steps or []),
        "file_count": len(options.files_list) if options.files_list is not None else None,
        "day_label": options.day_label,
        "all_days": options.all_days,
        "retryable": retryable,
    }
    if not retryable:
        input_summary["retry_blocked_reason"] = t("task.retry_blocked")
    try:
        task = manager.submit(
            TaskKind.PIPELINE,
            t("task.pipeline_title"),
            project_id=project_id,
            project_name=options.proj_dir.name,
            project_path=project_id,
            input_data=input_data,
            private_input_data={
                "_config": options.cfg,
                "context_override": options.context_override,
                "task_prompts": options.task_prompts,
            },
            input_summary=input_summary,
            reject_if_active=True,
        )
    except TaskAlreadyRunningError:
        handler._send_json({"ok": False, "error": t("task.project_busy")}, 409)
        return
    label = "+".join(options.steps) if options.steps else "all"
    handler._send_json(
        {
            "ok": True,
            "message": f"pipeline started ({label})",
            "task_id": task.id,
            "task": task.to_dict(),
        }
    )


def handle_post_run_start(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle POST /api/run/start."""
    options = _parse_run_start_options(handler, qs, obj)
    if options is None:
        return
    confirmation_error = _run_start_confirmation_error(options)
    if confirmation_error is not None:
        payload, status = confirmation_error
        handler._send_json(payload, status)
        return
    _submit_run_start(handler, options)


def handle_post_run_preview(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle POST /api/run/preview."""
    day_label = obj.get("day_label", "day1")
    if not isinstance(day_label, str) or not _is_safe_basename(day_label):
        return handler._send_json({"ok": False, "error": "invalid day_label"}, 400)
    steps = obj.get("steps")
    files_list = obj.get("files")
    if files_list is not None and not isinstance(files_list, list):
        return handler._send_json({"ok": False, "error": "files must be a list of video names"}, 400)
    if files_list is not None and not all(isinstance(item, str) for item in files_list):
        return handler._send_json({"ok": False, "error": "files items must be strings"}, 400)
    all_days = obj.get("all_days", False)
    if not isinstance(all_days, bool):
        return handler._send_json({"ok": False, "error": "all_days must be a boolean"}, 400)
    if all_days and files_list is not None:
        return handler._send_json(
            {"ok": False, "error": "all_days cannot be combined with an explicit file selection"},
            400,
        )
    overwrite = obj.get("overwrite", False)
    if not isinstance(overwrite, bool):
        return handler._send_json({"ok": False, "error": "overwrite must be a boolean"}, 400)
    use_transcripts = obj.get("use_transcripts", True)
    if not isinstance(use_transcripts, bool):
        return handler._send_json({"ok": False, "error": "use_transcripts must be a boolean"}, 400)
    if steps is not None and (not isinstance(steps, list) or not all(isinstance(step, str) for step in steps)):
        return handler._send_json({"ok": False, "error": "steps must be a list of strings"}, 400)
    if steps is not None:
        unknown = sorted(set(steps) - set(PIPELINE_STEPS))
        if unknown:
            return handler._send_json({"ok": False, "error": f"unknown pipeline step(s): {', '.join(unknown)}"}, 400)
    context_override = obj.get("context_override")
    if context_override is not None and not isinstance(context_override, str):
        return handler._send_json({"ok": False, "error": "context_override must be a string"}, 400)
    if isinstance(context_override, str) and len(context_override) > 100_000:
        return handler._send_json({"ok": False, "error": "context_override is too large"}, 400)
    context_override = context_override or None
    task_prompts = obj.get("task_prompts")
    if task_prompts is not None and (
        not isinstance(task_prompts, dict)
        or not all(isinstance(key, str) and isinstance(value, str) for key, value in task_prompts.items())
    ):
        return handler._send_json({"ok": False, "error": "task_prompts must be an object of strings"}, 400)
    task_prompts = task_prompts or None
    if task_prompts and (len(task_prompts) > 20 or any(len(value) > 100_000 for value in task_prompts.values())):
        return handler._send_json({"ok": False, "error": "task_prompts is too large"}, 400)

    proj_dir = handler._resolve_project_dir(qs)
    config_path = getattr(handler, "config_path", None)
    if not isinstance(config_path, Path):
        config_path = None
    allowed = collect_allowed_project_paths(proj_dir, config_path)
    proj_dir, cfg_error = _resolve_run_project_dir(
        proj_dir,
        obj.get("project_dir") if obj.get("project_dir") is not None else obj.get("input_dir"),
        allowed_paths=allowed,
    )
    if cfg_error:
        return handler._send_json({"ok": False, "error": cfg_error}, 400)
    # Preview must reflect the same final project dir as the actual run (P1-P31).
    cfg = handler._get_config(proj_dir)
    preview = build_run_preview(
        cfg,
        steps or [],
        force=overwrite,
        use_transcripts=use_transcripts,
        files=files_list,
        day_label=day_label,
        all_days=all_days,
        context_override=context_override,
        task_prompts=task_prompts,
    )
    if isinstance(preview, dict):
        preview["preflight"] = preflight_config_media_deps(
            cfg,
            required=media_dependencies_for_steps(steps),
        )
    handler._send_json({"ok": True, "preview": preview})


@dataclass
class _RerunOptions:
    proj_dir: Path
    cfg: Any
    config_path: Path | None
    proj_out: Path
    video_basename: str
    task: str
    source_view: str
    index_value: str | None
    abspath_raw: str
    confirm_artifact_overwrite: bool

    @property
    def stem(self) -> str:
        return Path(self.video_basename).stem


def _parse_rerun_options(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> _RerunOptions | None:
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    config_path = getattr(handler, "config_path", None)
    if not isinstance(config_path, Path):
        config_path = None
    proj_out = _project_output_dir(proj_dir)

    video_value = obj.get("video")
    task_value = obj.get("task")
    if not isinstance(video_value, str) or not isinstance(task_value, str):
        handler._send_json({"ok": False, "error": "invalid video filename"}, 400)
        return None
    video_basename = video_value.strip()
    task = task_value.strip()
    if not video_basename or not _is_safe_video_reference(video_basename):
        handler._send_json({"ok": False, "error": "invalid video filename"}, 400)
        return None
    if task not in ("compress", "analyze", "texts", "voiceover", "transcribe", "all"):
        handler._send_json(
            {
                "ok": False,
                "error": "requires video (filename) and task (compress|analyze|texts|voiceover|transcribe|all)",
            },
            400,
        )
        return None
    if task == "texts":
        task = "analyze"

    source_view = obj.get("source", "compressed")
    if source_view not in ("compressed", "original"):
        handler._send_json({"ok": False, "error": "source must be compressed or original"}, 400)
        return None
    index_value = obj.get("index")
    if index_value is not None and not isinstance(index_value, str):
        handler._send_json({"ok": False, "error": "index must be a string"}, 400)
        return None
    abspath_value = obj.get("abspath")
    if abspath_value is not None and not isinstance(abspath_value, str):
        handler._send_json({"ok": False, "error": "abspath must be a string"}, 400)
        return None
    confirm_artifact_overwrite = obj.get("confirm_artifact_overwrite", False)
    if not isinstance(confirm_artifact_overwrite, bool):
        handler._send_json({"ok": False, "error": "confirm_artifact_overwrite must be a boolean"}, 400)
        return None
    return _RerunOptions(
        proj_dir=proj_dir,
        cfg=cfg,
        config_path=config_path,
        proj_out=proj_out,
        video_basename=video_basename,
        task=task,
        source_view=source_view,
        index_value=index_value,
        abspath_raw=(abspath_value or "").strip(),
        confirm_artifact_overwrite=confirm_artifact_overwrite,
    )


def _resolve_rerun_abspath(proj_dir: Path, abspath_raw: str) -> Path | None:
    if not abspath_raw:
        return None
    candidate = Path(abspath_raw).resolve()
    if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTS:
        return None
    selected = {p.resolve() for p in load_selected_videos(proj_dir)}
    try:
        candidate.relative_to(proj_dir.resolve())
        under_project = True
    except ValueError:
        under_project = False
    return candidate if candidate in selected or under_project else None


def _resolve_rerun_original_video(options: _RerunOptions) -> tuple[Path | None, str | None]:
    original_video = _resolve_rerun_abspath(options.proj_dir, options.abspath_raw)
    if original_video is not None:
        return original_video, None

    if options.source_view == "original":
        candidate = (options.proj_dir / options.video_basename).resolve()
        if candidate.is_file():
            return candidate, None
        for path in load_selected_videos(options.proj_dir):
            if (path.name == options.video_basename or path.stem.lower() == options.stem.lower()) and path.is_file():
                return path.resolve(), None
        original_video = _resolve_found_original(
            _find_original_for_compressed(
                options.stem,
                options.proj_dir,
                options.cfg.compressed_dir,
                project_dir=options.proj_dir,
            ),
            options.proj_dir,
        )
        if original_video is None:
            return None, f"original video not found: {options.video_basename}"
        return original_video, None

    comp_dir = options.cfg.compressed_dir
    if comp_dir:
        for ext in VIDEO_EXTS:
            candidate = comp_dir / f"{options.stem}{ext}"
            if not candidate.is_file():
                continue
            meta = VideoMeta.read(candidate)
            if meta is not None:
                source_path = Path(meta.source_path)
                if source_path.is_file():
                    return source_path.resolve(), None
    original_video = _resolve_found_original(
        _find_original_for_compressed(options.stem, options.proj_dir, comp_dir, project_dir=options.proj_dir),
        options.proj_dir,
    )
    if original_video is None:
        return None, f"no matching original video for {options.stem}"
    return original_video, None


def _resolve_rerun_texts_json(options: _RerunOptions) -> Path | None:
    if options.task != "voiceover":
        return None
    raw_index = (options.index_value or "").strip()
    index_prefix = (
        re.sub(r"[^a-zA-Z0-9_-]", "", raw_index)
        if raw_index
        else (options.stem.split("_", 1)[0] if "_" in options.stem else options.stem)
    )
    for texts_dir in _find_texts_dirs(
        options.proj_out,
        preferred_subdir=getattr(getattr(options.cfg, "analyze", None), "texts_subdir", None) or "texts",
    ):
        candidates = sorted(texts_dir.glob(f"{index_prefix}_*.json"))
        if candidates:
            return candidates[0]
    return None


def _rerun_artifact_confirmation(
    options: _RerunOptions,
    original_video: Path,
) -> tuple[dict[str, Any], int] | None:
    artifact_steps: list[str] = []
    if options.task in ("analyze", "all"):
        artifact_steps.append("analyze")
    if options.task in ("voiceover", "all"):
        artifact_steps.append("voiceover")
    if not artifact_steps:
        return None
    artifact_cfg = copy.deepcopy(options.cfg)
    artifact_cfg.analyze.skip_existing = False
    artifact_paths = artifact_overwrite_paths(
        artifact_cfg,
        artifact_steps,
        force=False,
        use_transcripts=bool(getattr(options.cfg.plan, "use_transcripts", True)),
        files=[str(original_video)],
    )
    if not artifact_paths or options.confirm_artifact_overwrite:
        return None
    items = [path.name for path in artifact_paths]
    return (
        {
            "ok": False,
            "code": "artifact_overwrite_confirmation_required",
            "error": t("task.artifact.overwrite_confirmation_required", {"items": ", ".join(items)}),
            "items": items,
        },
        409,
    )


def _submit_rerun(
    handler: HandlerProtocol,
    options: _RerunOptions,
    original_video: Path,
    texts_json: Path | None,
) -> None:
    manager = _managed_task_manager(handler)
    if manager is None:
        handler._send_json({"ok": False, "error": "task center unavailable"}, 500)
        return
    _ensure_run_handlers(manager)
    project_id = str(options.proj_dir.resolve())
    if _active_managed_run(manager, project_id) is not None:
        handler._send_json({"ok": False, "error": t("task.project_busy")}, 409)
        return
    try:
        managed_task = manager.submit(
            TaskKind.RERUN,
            t("task.rerun_title", {"task": options.task, "video": options.video_basename}),
            project_id=project_id,
            project_name=options.proj_dir.name,
            project_path=project_id,
            input_data={
                "config_path": str(options.config_path) if options.config_path is not None else None,
                "project_dir": project_id,
                "task": options.task,
                "video_basename": options.video_basename,
                "original_video": str(original_video),
                "texts_json": str(texts_json) if texts_json is not None else None,
                "confirm_artifact_overwrite": options.confirm_artifact_overwrite,
            },
            private_input_data={"_config": options.cfg},
            input_summary={"task": options.task, "video": options.video_basename},
            reject_if_active=True,
        )
    except TaskAlreadyRunningError:
        handler._send_json({"ok": False, "error": t("task.project_busy")}, 409)
        return
    handler._send_json(
        {
            "ok": True,
            "message": f"started rerun {options.task} ({options.video_basename})",
            "task_id": managed_task.id,
            "task": managed_task.to_dict(),
        }
    )


def handle_post_rerun(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle POST /api/rerun."""
    options = _parse_rerun_options(handler, qs, obj)
    if options is None:
        return
    original_video, error = _resolve_rerun_original_video(options)
    if error is not None:
        handler._send_json({"ok": False, "error": error}, 404)
        return
    assert original_video is not None
    texts_json = _resolve_rerun_texts_json(options)
    if options.task == "voiceover" and texts_json is None:
        handler._send_json({"ok": False, "error": f"no analysis result found for {options.stem}"}, 404)
        return
    confirmation = _rerun_artifact_confirmation(options, original_video)
    if confirmation is not None:
        payload, status = confirmation
        handler._send_json(payload, status)
        return
    _submit_rerun(handler, options, original_video, texts_json)

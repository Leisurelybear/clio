"""Route handlers: /api/run/start, /api/rerun"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio._constants import VIDEO_EXTS
from clio.config import load_config
from clio.pipeline import run_analyze_all, run_compress_all, run_generate_scripts, run_pipeline_steps
from clio.progress import ProgressTracker
from clio.task_center.manager import TaskAlreadyRunningError, TaskManager
from clio.task_center.models import TaskKind, TaskStatus
from clio.task_center.reporter import TaskCancelled, TaskProgressReporter
from clio.task_center.store import TaskQuery
from clio.tasks._video_loader import load_selected_videos
from clio.tasks.transcribe import run_transcribe_one
from clio.ui.services.file_service import _find_original_for_compressed, _find_texts_dirs, _is_safe_basename
from clio.ui.services.project_service import _project_output_dir, collect_allowed_project_paths
from clio.ui.services.run_preview import build_run_preview
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
        concurrency_key=lambda task: f"run:{task.project_id or task.project_path}",
        max_concurrency=1,
        cancellable=True,
    )
    manager.ensure_registered(
        TaskKind.RERUN,
        _run_rerun_task,
        concurrency_key=lambda task: f"run:{task.project_id or task.project_path}",
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
            overwrite=bool(input_data.get("overwrite", False)),
            context_override=input_data.get("context_override"),
            task_prompts=input_data.get("task_prompts"),
        )
    except TaskCancelled:
        tracker.cancelled("任务已取消")
        raise
    except Exception as e:
        tracker.error(f"pipeline failed: {e}")
        raise
    return result


def _run_rerun_task(context) -> dict[str, Any]:
    input_data = context.input_data
    cfg = _task_config(context)
    cfg.analyze.skip_existing = False
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

    try:
        _log(f"▶ Starting rerun {task_name} — {video_basename}")
        for step_name, step_fn, step_label in [
            (
                "compress",
                lambda: run_compress_all(
                    cfg, tracker=tracker, single_file=original_video, cancel_event=context.cancel_event
                ),
                "压缩视频",
            ),
            (
                "analyze",
                lambda: run_analyze_all(
                    cfg, tracker=tracker, single_file=original_video, cancel_event=context.cancel_event
                ),
                "AI 分析",
            ),
            (
                "voiceover",
                lambda: run_generate_scripts(
                    cfg, tracker=tracker, single_file=texts_json, cancel_event=context.cancel_event
                ),
                "生成口播",
            ),
        ]:
            if task_name not in (step_name, "all"):
                continue
            context.reporter.raise_if_cancelled()
            _log(f"Step: {step_label}...")
            step_fn()
            _log(f"✓ {step_label} complete")
        if task_name in ("transcribe", "all"):
            context.reporter.raise_if_cancelled()
            _log("Step: transcribing audio...")
            from clio.transcribe import check_whisper

            if not check_whisper():
                raise RuntimeError("faster-whisper 未安装。执行: python main.py whisper install")
            result = run_transcribe_one(
                cfg,
                original_video,
                cancel_event=context.cancel_event,
                progress_callback=lambda pct: tracker.update(
                    phase="transcribe", current=pct, total=100, message=f"{video_basename}: 转录 ({pct}%)"
                ),
            )
            if "error" in result:
                raise RuntimeError(result["error"])
            _log("✓ transcription complete")
        context.reporter.raise_if_cancelled()
        tracker.done(f"{task_name} → {video_basename} complete")
    except TaskCancelled:
        tracker.cancelled("任务已取消")
        raise
    except Exception as e:
        tracker.error(f"rerun failed: {e}")
        raise
    return {"task": task_name, "video": video_basename}


def _resolve_run_project_dir(
    proj_dir: Path,
    project_dir_raw: str | None,
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


def handle_post_run_start(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle POST /api/run/start."""
    day_label = obj.get("day_label", "day1")
    if not isinstance(day_label, str) or not _is_safe_basename(day_label):
        return handler._send_json({"ok": False, "error": "invalid day_label"}, 400)
    steps = obj.get("steps")
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
    # Config, output, state, lock and tasks all derive from the single final
    # project dir, so a body override can never split them (P1-P31).
    cfg = handler._get_config(proj_dir)
    # Isolate run-local config so body flags never mutate the shared cache entry.
    cfg = copy.deepcopy(cfg)
    if "use_transcripts" in obj:
        if not isinstance(obj["use_transcripts"], bool):
            return handler._send_json({"ok": False, "error": "use_transcripts must be a boolean"}, 400)
        cfg.plan.use_transcripts = obj["use_transcripts"]
    if steps is not None:
        if not isinstance(steps, list):
            return handler._send_json({"ok": False, "error": "steps must be a list"}, 400)
        for s in steps:
            if not isinstance(s, str):
                return handler._send_json({"ok": False, "error": "steps items must be strings"}, 400)
    files_list = obj.get("files")
    if files_list is not None and not isinstance(files_list, list):
        return handler._send_json({"ok": False, "error": "files must be a list of video names"}, 400)
    if files_list is not None and not all(isinstance(item, str) for item in files_list):
        return handler._send_json({"ok": False, "error": "files items must be strings"}, 400)
    overwrite = obj.get("overwrite", False)
    if not isinstance(overwrite, bool):
        return handler._send_json({"ok": False, "error": "overwrite must be a boolean"}, 400)
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

    manager = _managed_task_manager(handler)
    if manager is None:
        return handler._send_json({"ok": False, "error": "task center unavailable"}, 500)
    _ensure_run_handlers(manager)
    project_id = str(proj_dir.resolve())
    if _active_managed_run(manager, project_id) is not None:
        return handler._send_json({"ok": False, "error": "pipeline is already running"}, 409)
    input_data = {
        "config_path": str(config_path) if config_path is not None else None,
        "project_dir": project_id,
        "day_label": day_label,
        "steps": steps,
        "files": files_list,
        "overwrite": overwrite,
        "use_transcripts": cfg.plan.use_transcripts,
    }
    try:
        task = manager.submit(
            TaskKind.PIPELINE,
            "运行素材处理流水线",
            project_id=project_id,
            project_name=proj_dir.name,
            project_path=project_id,
            input_data=input_data,
            private_input_data={
                "_config": cfg,
                "context_override": context_override,
                "task_prompts": task_prompts,
            },
            input_summary={
                "steps": list(steps or []),
                "file_count": len(files_list) if files_list is not None else None,
                "day_label": day_label,
            },
            reject_if_active=True,
        )
    except TaskAlreadyRunningError:
        return handler._send_json({"ok": False, "error": "pipeline is already running"}, 409)
    label = "+".join(steps) if steps else "all"
    return handler._send_json(
        {
            "ok": True,
            "message": f"pipeline started ({label})",
            "task_id": task.id,
            "task": task.to_dict(),
        }
    )


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
    overwrite = obj.get("overwrite", False)
    if not isinstance(overwrite, bool):
        return handler._send_json({"ok": False, "error": "overwrite must be a boolean"}, 400)
    use_transcripts = obj.get("use_transcripts", True)
    if not isinstance(use_transcripts, bool):
        return handler._send_json({"ok": False, "error": "use_transcripts must be a boolean"}, 400)
    if steps is not None and (not isinstance(steps, list) or not all(isinstance(step, str) for step in steps)):
        return handler._send_json({"ok": False, "error": "steps must be a list of strings"}, 400)

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
    )
    handler._send_json({"ok": True, "preview": preview})


def handle_post_rerun(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle POST /api/rerun."""
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    config_path = getattr(handler, "config_path", None)
    if not isinstance(config_path, Path):
        config_path = None
    proj_out = _project_output_dir(proj_dir)

    video_basename = (obj.get("video") or "").strip()
    task = (obj.get("task") or "").strip()
    if not video_basename or not _is_safe_basename(video_basename):
        return handler._send_json({"ok": False, "error": "invalid video filename"}, 400)
    if task not in ("compress", "analyze", "texts", "voiceover", "transcribe", "all"):
        return handler._send_json(
            {
                "ok": False,
                "error": "requires video (filename) and task (compress|analyze|texts|voiceover|transcribe|all)",
            },
            400,
        )
    # 向后兼容
    if task == "texts":
        task = "analyze"

    stem = Path(video_basename).stem

    # Resolve original video path (supports external paths via .vmeta / videos.json)
    source_view = obj.get("source", "compressed")
    abspath_raw = (obj.get("abspath") or "").strip()
    original_video: Path | None = None

    if abspath_raw:
        candidate = Path(abspath_raw).resolve()
        if candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTS:
            selected = {p.resolve() for p in load_selected_videos(proj_dir)}
            under_project = False
            try:
                candidate.relative_to(proj_dir.resolve())
                under_project = True
            except ValueError:
                under_project = False
            if candidate in selected or under_project:
                original_video = candidate

    if original_video is None and source_view == "original":
        candidate = (proj_dir / video_basename).resolve()
        if candidate.is_file():
            original_video = candidate
        else:
            # Frontend may send compressed-style name in original view, or an
            # external basename only present in videos.json / .vmeta.
            for p in load_selected_videos(proj_dir):
                if p.name == video_basename or p.stem.lower() == stem.lower():
                    if p.is_file():
                        original_video = p.resolve()
                        break
            if original_video is None:
                original_video = _resolve_found_original(
                    _find_original_for_compressed(stem, proj_dir, cfg.compressed_dir, project_dir=proj_dir),
                    proj_dir,
                )
        if original_video is None:
            return handler._send_json({"ok": False, "error": f"original video not found: {video_basename}"}, 404)
    elif original_video is None:
        # Compressed view: .vmeta.source_path is authoritative for external originals
        comp_dir = cfg.compressed_dir
        if comp_dir:
            for _ext in VIDEO_EXTS:
                cand = comp_dir / f"{stem}{_ext}"
                if cand.is_file():
                    meta = VideoMeta.read(cand)
                    if meta is not None:
                        sp = Path(meta.source_path)
                        if sp.is_file():
                            original_video = sp.resolve()
                            break
        if original_video is None:
            original_video = _resolve_found_original(
                _find_original_for_compressed(stem, proj_dir, comp_dir, project_dir=proj_dir),
                proj_dir,
            )
            if original_video is None:
                return handler._send_json({"ok": False, "error": f"no matching original video for {stem}"}, 404)

    # Resolve texts JSON path (for voiceover rerun)
    raw_index = obj.get("index") or ""
    index_prefix = (
        re.sub(r"[^a-zA-Z0-9_-]", "", raw_index) if raw_index else (stem.split("_", 1)[0] if "_" in stem else stem)
    )
    texts_json = None
    if task in ("voiceover", "all"):
        for td in _find_texts_dirs(
            proj_out,
            preferred_subdir=getattr(getattr(cfg, "analyze", None), "texts_subdir", None) or "texts",
        ):
            candidates = sorted(td.glob(f"{index_prefix}_*.json"))
            if candidates:
                texts_json = candidates[0]
                break
        if texts_json is None:
            return handler._send_json({"ok": False, "error": f"no analysis result found for {stem}"}, 404)

    manager = _managed_task_manager(handler)
    if manager is None:
        return handler._send_json({"ok": False, "error": "task center unavailable"}, 500)
    _ensure_run_handlers(manager)
    project_id = str(proj_dir.resolve())
    if _active_managed_run(manager, project_id) is not None:
        return handler._send_json({"ok": False, "error": "a task is already running"}, 409)
    try:
        managed_task = manager.submit(
            TaskKind.RERUN,
            f"重跑 {task}: {video_basename}",
            project_id=project_id,
            project_name=proj_dir.name,
            project_path=project_id,
            input_data={
                "config_path": str(config_path) if config_path is not None else None,
                "project_dir": project_id,
                "task": task,
                "video_basename": video_basename,
                "original_video": str(original_video),
                "texts_json": str(texts_json) if texts_json is not None else None,
            },
            input_summary={"task": task, "video": video_basename},
            reject_if_active=True,
        )
    except TaskAlreadyRunningError:
        return handler._send_json({"ok": False, "error": "a task is already running"}, 409)
    return handler._send_json(
        {
            "ok": True,
            "message": f"started rerun {task} ({video_basename})",
            "task_id": managed_task.id,
            "task": managed_task.to_dict(),
        }
    )

"""Pipeline orchestration — imports all task functions from clio/tasks/.
All public symbols are re-exported for backward compatibility."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

# Keep these orchestration-specific items in pipeline.py
from clio.config import AppConfig
from clio.i18n import t
from clio.log import timed
from clio.progress import ProgressReporter, ProgressTracker

# Re-export everything for backward compatibility (imported by main.py, ui)
from clio.tasks._helpers import (
    ClipRecord,  # noqa: F401
    _build_stem,  # noqa: F401
    _eta_line,  # noqa: F401
    _next_index,  # noqa: F401
    _rewrite_script_md,  # noqa: F401
    _rewrite_text_file,  # noqa: F401
    _write_csv,  # noqa: F401
    _write_text_file,  # noqa: F401
)
from clio.tasks.analyze import run_analyze_all
from clio.tasks.compress import run_compress_all
from clio.tasks.consistency import run_entity_consistency
from clio.tasks.cut import run_cut_all  # noqa: F401
from clio.tasks.label import run_label_videos
from clio.tasks.plan import discover_day_labels, run_plan_all_days, run_plan_vlog
from clio.tasks.refine import run_refine_scripts, run_refine_texts  # noqa: F401
from clio.tasks.reindex import auto_reindex_if_needed
from clio.tasks.scripts import run_generate_plan_voiceovers, run_generate_scripts
from clio.tasks.transcribe import run_transcribe_all


def run_full_pipeline(config: AppConfig, day_label: str = "day1", strict: bool = False) -> None:
    """Run every pipeline step with the same failure semantics as the Web UI."""
    run_pipeline_steps(config, day_label=day_label, steps=list(_STEP_FUNCS), strict=strict)


def _step_label(name: str) -> str:
    """Step label for pipeline logs, resolved from the active locale."""
    return t(f"run.step.{name}.label", fallback=name)


_STEP_FUNCS: dict[str, Callable[..., Any]] = {
    "compress": run_compress_all,
    "transcribe": run_transcribe_all,
    "analyze": run_analyze_all,
    "consistency": run_entity_consistency,
    "voiceover": run_generate_scripts,
    "plan": run_plan_vlog,
    "plan_voiceover": run_generate_plan_voiceovers,
    "label": run_label_videos,
}

PIPELINE_STEPS = tuple(_STEP_FUNCS)

# Single-video reruns support a subset of the main pipeline. Derive their order
# from the canonical pipeline so adding or reordering a stage cannot leave the
# rerun path on a stale, parallel sequence.
SINGLE_VIDEO_RERUN_STEPS = tuple(
    step for step in PIPELINE_STEPS if step in {"compress", "transcribe", "analyze", "voiceover"}
)

_STEP_DAY_ARG = {"consistency", "plan", "plan_voiceover"}


def _cancel_pipeline_step(step: str, tracker: ProgressReporter | None) -> None:
    msg = t("pipeline.cancelled", {"step": _step_label(step)})
    print(msg)
    if tracker:
        tracker.cancelled(msg)


def _build_step_kwargs(
    step: str,
    *,
    cancel_event: threading.Event | None,
    files: list[str] | None,
    overwrite: bool,
    confirm_plan_overwrite: bool,
    confirm_artifact_overwrite: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict:
    kwargs: dict = {}
    if cancel_event:
        kwargs["cancel_event"] = cancel_event
    if files is not None:
        kwargs["files"] = files
    if overwrite:
        kwargs["overwrite"] = True
    if step == "plan" and confirm_plan_overwrite:
        kwargs["confirm_plan_overwrite"] = True
    if step in {"analyze", "plan", "voiceover", "plan_voiceover"} and confirm_artifact_overwrite:
        kwargs["confirm_artifact_overwrite"] = True
    if context_override:
        kwargs["context_override"] = context_override
    if task_prompts:
        kwargs["task_prompts"] = task_prompts
    return kwargs


def _run_step(
    step: str,
    config: AppConfig,
    day_label: str,
    day_labels: list[str],
    tracker: ProgressReporter | None,
    kwargs: dict,
    *,
    all_days: bool,
    overwrite: bool,
    confirm_artifact_overwrite: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
    cancel_event: threading.Event | None,
) -> Any:
    fn = _STEP_FUNCS[step]
    if step == "plan" and all_days:
        return run_plan_all_days(
            config,
            tracker=tracker,
            cancel_event=cancel_event,
            overwrite=overwrite,
            confirm_artifact_overwrite=confirm_artifact_overwrite,
            context_override=context_override,
            task_prompts=task_prompts,
        )
    if step in _STEP_DAY_ARG and all_days:
        results: list[Any] = []
        for scoped_day in day_labels:
            if cancel_event and cancel_event.is_set():
                break
            scoped_result = fn(config, scoped_day, tracker, **kwargs)
            if scoped_result is not None:
                results.append(scoped_result)
        return results
    if step in _STEP_DAY_ARG:
        return fn(config, day_label, tracker, **kwargs)
    return fn(config, tracker, **kwargs)


def _record_step_result(
    summary: dict[str, Any],
    step: str,
    label: str,
    result: Any,
    *,
    strict: bool,
) -> None:
    if isinstance(result, int) and not isinstance(result, bool) and result != 0:
        raise RuntimeError(t("pipeline.step_failed", {"label": label, "code": result}))
    processed = len(result) if isinstance(result, list) else int(result is not None)
    failed = getattr(result, "failed", 0)
    if not isinstance(failed, int) or isinstance(failed, bool):
        failed = 0
    summary["steps"].append({"name": step, "processed": processed, "failed": failed})
    summary["processed"] += processed
    summary["error_count"] += failed
    if strict and failed > 0:
        raise RuntimeError(t("task.analyze.incomplete", {"completed": processed, "failed": failed}))


def run_pipeline_steps(
    config: AppConfig,
    day_label: str = "day1",
    steps: list[str] | None = None,
    tracker: ProgressReporter | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    all_days: bool = False,
    overwrite: bool = False,
    confirm_plan_overwrite: bool = False,
    confirm_artifact_overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run the requested pipeline steps with optional progress tracking."""
    config.paths.output_dir.mkdir(parents=True, exist_ok=True)
    if all_days and files is not None:
        raise ValueError(t("pipeline.all_days_files_conflict"))
    auto_reindex_if_needed(config)
    if not steps:
        steps = list(_STEP_FUNCS.keys())
    day_labels = discover_day_labels(config) if all_days else [day_label]

    if tracker is None:
        tracker = ProgressTracker(config.paths.output_dir)

    unknown = [s for s in steps if s not in _STEP_FUNCS]
    if unknown:
        raise ValueError(
            t("pipeline.unknown_steps", {"unknown": ", ".join(unknown), "available": ", ".join(_STEP_FUNCS)})
        )

    summary: dict[str, Any] = {"steps": [], "processed": 0, "warning_count": 0, "error_count": 0}
    try:
        for step in steps:
            if cancel_event and cancel_event.is_set():
                _cancel_pipeline_step(step, tracker)
                break
            label = _step_label(step)
            if tracker:
                tracker.update(phase=step, current=0, total=1, message=f"{label}...")
            with timed(f"=== {label} ==="):
                kwargs = _build_step_kwargs(
                    step,
                    cancel_event=cancel_event,
                    files=files,
                    overwrite=overwrite,
                    confirm_plan_overwrite=confirm_plan_overwrite,
                    confirm_artifact_overwrite=confirm_artifact_overwrite,
                    context_override=context_override,
                    task_prompts=task_prompts,
                )
                result = _run_step(
                    step,
                    config,
                    day_label,
                    day_labels,
                    tracker,
                    kwargs,
                    all_days=all_days,
                    overwrite=overwrite,
                    confirm_artifact_overwrite=confirm_artifact_overwrite,
                    context_override=context_override,
                    task_prompts=task_prompts,
                    cancel_event=cancel_event,
                )
                _record_step_result(summary, step, label, result, strict=strict)
        if not (cancel_event and cancel_event.is_set()):
            tracker.done(t("pipeline.done", {"output_dir": config.paths.output_dir}))
    except Exception as e:
        tracker.error(str(e))
        raise
    return summary

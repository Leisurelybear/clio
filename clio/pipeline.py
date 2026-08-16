"""Pipeline orchestration — imports all task functions from clio/tasks/.
All public symbols are re-exported for backward compatibility."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

# Keep these orchestration-specific items in pipeline.py
from clio.config import AppConfig
from clio.log import timed
from clio.progress import ProgressTracker

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
from clio.tasks.analyze import run_analyze_all  # noqa: F401
from clio.tasks.compress import run_compress_all  # noqa: F401
from clio.tasks.cut import run_cut_all  # noqa: F401
from clio.tasks.label import run_label_videos  # noqa: F401
from clio.tasks.plan import run_plan_all_days, run_plan_vlog  # noqa: F401
from clio.tasks.refine import run_refine_scripts, run_refine_texts  # noqa: F401
from clio.tasks.reindex import auto_reindex_if_needed  # noqa: F401
from clio.tasks.scripts import run_generate_scripts  # noqa: F401
from clio.tasks.transcribe import run_transcribe_all  # noqa: F401


def run_full_pipeline(config: AppConfig, day_label: str = "day1") -> None:
    config.paths.output_dir.mkdir(parents=True, exist_ok=True)
    auto_reindex_if_needed(config)
    with timed("=== 1/7 压缩原视频 ==="):
        run_compress_all(config)
    with timed("=== 2/7 AI 分析素材 ==="):
        run_analyze_all(config)
    with timed("=== 3/7 生成口播文案 ==="):
        run_generate_scripts(config)
    with timed("=== 4/7 语音转录 ==="):
        run_transcribe_all(config)
    with timed("=== 5/7 vlog 剪辑规划 ==="):
        run_plan_vlog(config, day_label)
    with timed("=== 6/7 烧录序号标注 ==="):
        run_label_videos(config)
    print("\n完成！输出目录:", config.paths.output_dir)


_STEP_LABELS = {
    "compress": "压缩原视频",
    "analyze": "AI 分析素材",
    "voiceover": "生成口播文案",
    "transcribe": "语音转录",
    "plan": "vlog 剪辑规划",
    "label": "烧录序号标注",
}

_STEP_FUNCS: dict[str, Callable[..., Any]] = {
    "compress": run_compress_all,
    "analyze": run_analyze_all,
    "voiceover": run_generate_scripts,
    "transcribe": run_transcribe_all,
    "plan": run_plan_vlog,
    "label": run_label_videos,
}

_STEP_DAY_ARG = {"plan"}


def run_pipeline_steps(
    config: AppConfig,
    day_label: str = "day1",
    steps: list[str] | None = None,
    tracker: ProgressTracker | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """运行指定步骤列表（可选的进度跟踪）。

    steps 取值: analyze, voiceover, plan, label（默认全部）。
    """
    config.paths.output_dir.mkdir(parents=True, exist_ok=True)
    auto_reindex_if_needed(config)
    if not steps:
        steps = list(_STEP_FUNCS.keys())

    if tracker is None:
        tracker = ProgressTracker(config.paths.output_dir)

    unknown = [s for s in steps if s not in _STEP_FUNCS]
    if unknown:
        raise ValueError(f"未知的 pipeline step: {', '.join(unknown)}（可选: {', '.join(_STEP_FUNCS)}）")

    summary: dict[str, Any] = {"steps": [], "processed": 0, "warning_count": 0, "error_count": 0}
    try:
        for step in steps:
            if cancel_event and cancel_event.is_set():
                msg = f"[取消] pipeline 被用户终止（步骤: {_STEP_LABELS.get(step, step)}）"
                print(msg)
                if tracker:
                    tracker.cancelled(msg)
                break
            label = _STEP_LABELS.get(step, step)
            if tracker:
                tracker.update(phase=step, current=0, total=1, message=f"{label}...")
            with timed(f"=== {label} ==="):
                fn = _STEP_FUNCS[step]
                kwargs: dict = {}
                if cancel_event:
                    kwargs["cancel_event"] = cancel_event
                if files is not None:
                    kwargs["files"] = files
                if overwrite:
                    kwargs["overwrite"] = True
                if context_override:
                    kwargs["context_override"] = context_override
                if task_prompts:
                    kwargs["task_prompts"] = task_prompts
                if step in _STEP_DAY_ARG:
                    result = fn(config, day_label, tracker, **kwargs)
                else:
                    result = fn(config, tracker, **kwargs)
                if isinstance(result, int) and not isinstance(result, bool) and result != 0:
                    raise RuntimeError(f"{label}失败（退出码 {result}）")
                processed = len(result) if isinstance(result, list) else int(result is not None)
                summary["steps"].append({"name": step, "processed": processed, "failed": 0})
                summary["processed"] += processed
        if not (cancel_event and cancel_event.is_set()):
            tracker.done(f"完成！输出目录: {config.paths.output_dir}")
    except Exception as e:
        tracker.error(str(e))
        raise
    return summary

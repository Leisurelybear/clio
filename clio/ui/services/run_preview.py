from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTENSIONS
from clio.artifact_guard import artifact_requires_overwrite_confirmation, read_reusable_artifact
from clio.config.models import AppConfig
from clio.i18n import t
from clio.identity import SEGMENT_SUFFIX_RE, is_legacy_split_path, prefer_canonical_compressed
from clio.index import ArtifactIndex
from clio.tasks._helpers import (
    MediaSelection,
    _matches_selected_artifact,
    _matches_selected_stem,
    _path_key,
    _project_selection_stems,
    _selection_from_files,
)
from clio.tasks.analyze import (
    _build_stem_to_path,
    _effective_analysis_context,
    _legacy_analysis_lineage_fingerprint,
    _load_transcript_context,
    _resolve_original,
    _uses_multiple_windows,
    analysis_artifact_overwrite_required,
    analysis_cache_status,
)
from clio.tasks.compress import _find_reusable, _scan_existing_compressed
from clio.tasks.consistency import entity_consistency_paths, load_entity_consistency
from clio.tasks.label import _find_compressed_video, _label_index
from clio.tasks.plan import (
    _collect_plan_clips,
    _no_analyzed_media_for_day_message,
    _plan_lineage_fingerprint,
    _resolve_filter_by_day,
    discover_day_labels,
    plan_artifact_overwrite_required,
    plan_overwrite_confirmation_required,
)
from clio.tasks.scripts import (
    _load_plan_voiceover_inputs,
    _load_script_template,
    _plan_voiceover_lineage_fingerprint,
    _voiceover_lineage_fingerprint,
    plan_voiceover_artifact_overwrite_required,
    voiceover_artifact_overwrite_required,
)
from clio.ui.services.run_estimate import (
    AnalyzeInput,
    combine_usage,
    consistency_call,
    estimate_analyze_usage,
    estimate_usage,
    not_applicable_usage,
    plan_calls,
    plan_voiceover_call,
    voiceover_call,
)
from clio.utils import get_duration_sec, plan_day_stem, resolve_binary
from clio.vmeta import VideoMeta


def _step_label(name: str) -> str:
    """Step label for the run preview, resolved from the active locale."""
    return t(f"run.step.{name}.label", fallback=name)


_RUN_PREVIEW_STAGES = (
    ("source_prep", ("compress", "transcribe")),
    ("ai_analysis", ("analyze",)),
    ("plan_alignment", ("consistency", "voiceover", "plan")),
    ("voiceover_delivery", ("plan_voiceover", "label")),
)

# Runtime order. Preview rows follow what the pipeline will actually execute so
# cascade review cannot report a downstream artifact as safely reusable.
PIPELINE_PREVIEW_ORDER = (
    "compress",
    "transcribe",
    "analyze",
    "consistency",
    "voiceover",
    "plan",
    "plan_voiceover",
    "label",
)

# Upstream step -> steps whose cached artifacts its rerun can invalidate.
# ``label`` is intentionally absent: runtime reuses labeled videos by existence
# only, so a changed analysis does not invalidate an existing labeled output.
_PIPELINE_CASCADE_EDGES = {
    "compress": ("analyze", "transcribe"),
    "transcribe": ("analyze",),
    "analyze": ("consistency", "voiceover", "plan"),
    "consistency": ("plan",),
    "voiceover": ("plan",),
    "plan": ("plan_voiceover",),
}

# Step -> upstream steps that must produce their artifact before the step has an
# input at all. Unlike the cascade graph this ignores cache identity rules: a
# missing transcript or analysis is simply unproducible until its producer runs.
_PIPELINE_INPUT_EDGES = {
    "transcribe": ("compress",),
    "analyze": ("compress", "transcribe"),
    "consistency": ("analyze",),
    "voiceover": ("analyze",),
    "plan": ("analyze",),
    "plan_voiceover": ("plan",),
    "label": ("analyze",),
}

_DAY_SCOPED_PREVIEW_STEPS = frozenset({"consistency", "plan", "plan_voiceover"})

# Rows whose whole input artifact is rewritten by a same-run producer. Even
# with a stale artifact on disk their reuse cannot be decided before that
# producer finishes, so they wait instead of being reported as reusable.
_DEFER_WHEN_PRODUCER_RUNS = frozenset({"plan_voiceover"})


def _ordered_steps(steps: Iterable[str]) -> list[str]:
    """Return unique step names in canonical pipeline order; unknown steps last."""
    unique: list[str] = []
    for step in steps:
        if step not in unique:
            unique.append(step)
    in_order = [step for step in PIPELINE_PREVIEW_ORDER if step in unique]
    return in_order + [step for step in unique if step not in PIPELINE_PREVIEW_ORDER]


def _cascade_sources(config: AppConfig, *, use_transcripts: bool) -> dict[str, tuple[str, ...]]:
    """Reverse the cascade graph, dropping edges that do not apply to this run.

    Transcript audio comes from the compressed video only while compression keeps
    audio; with ``remove_audio`` transcription reads the original footage, so a
    compress rerun cannot invalidate an existing transcript.
    """
    sources: dict[str, list[str]] = {}
    for upstream, targets in _PIPELINE_CASCADE_EDGES.items():
        for target in targets:
            if upstream == "compress" and target == "transcribe" and config.compress.remove_audio:
                continue
            if upstream == "transcribe" and not use_transcripts:
                continue
            sources.setdefault(target, []).append(upstream)
    return {target: tuple(items) for target, items in sources.items()}


def _input_sources(config: AppConfig, *, use_transcripts: bool) -> dict[str, tuple[str, ...]]:
    """Reverse the input graph: which earlier steps unlock a step's missing input."""
    sources: dict[str, list[str]] = {}
    for target, upstreams in _PIPELINE_INPUT_EDGES.items():
        for upstream in upstreams:
            if upstream == "transcribe" and not use_transcripts:
                continue
            sources.setdefault(target, []).append(upstream)
    return {target: tuple(items) for target, items in sources.items()}


def _row_is_reusable(row: dict[str, Any]) -> bool:
    """A row is reusable only when every item is a verified existing artifact."""
    total = int(row.get("total") or 0)
    return (
        total > 0
        and not row.get("disabled")
        and not row.get("deferred")
        and not row.get("blocking")
        and not row.get("warnings")
        and not row.get("cascade_assumed")
        and int(row.get("will_run") or 0) == 0
        and int(row.get("will_skip") or 0) == total
    )


def _steps_that_run(preview_steps: list[dict[str, Any]]) -> set[str]:
    """Steps with confirmed work in this request."""
    return {
        str(row.get("name")) for row in preview_steps if not row.get("disabled") and int(row.get("will_run") or 0) > 0
    }


def _defer_same_run_inputs(
    *,
    sources: dict[str, tuple[str, ...]],
    selected_steps: list[str],
    producers: set[str],
    preview_steps: list[dict[str, Any]],
    day_labels: list[str],
    media_max: int,
) -> set[str]:
    """Turn "input not produced yet" into a wait instead of a blocker.

    When the same request runs the step that creates the missing input, the row is
    not actionable and not blocked: it simply follows that upstream step. Volume
    stays a maximum because it depends on output that does not exist yet.
    Returns every step that may produce output in this run, including the rows
    that were just deferred.
    """
    rows_by_name = {str(row.get("name")): row for row in preview_steps}
    selected = set(selected_steps)
    may_run = set(producers)
    for name in PIPELINE_PREVIEW_ORDER:
        row = rows_by_name.get(name)
        if row is None:
            continue
        if row.get("disabled"):
            may_run.discard(name)
            continue
        providers = [src for src in sources.get(name, ()) if src in selected and src in may_run]
        if not providers:
            continue
        total = int(row.get("total") or 0)
        if total > 0 and name not in _DEFER_WHEN_PRODUCER_RUNS:
            # A stale artifact may be invalidated by the producer, but the row
            # still has a usable input shape, so the cascade pass covers it.
            continue
        # Waiting is the only user-facing meaning for a deferred row, so a
        # cascade marker left by an earlier pass must not survive it.
        row.pop("cascade_from", None)
        row.pop("cascade_assumed", None)
        if total > 0:
            # Keep the current skip count as a maximum, never as a promise.
            row.update(
                {
                    "deferred": True,
                    "waiting_on": providers,
                    "warnings": [],
                    "will_run": 0,
                    "will_run_max": total,
                    "will_skip_min": 0,
                    "estimate_is_minimum": True,
                    **not_applicable_usage(),
                }
            )
            may_run.add(name)
            continue
        row.update(
            {
                "deferred": True,
                "waiting_on": providers,
                "warnings": [],
                "estimate_is_minimum": True,
            }
        )
        # The expected volume follows upstream output that does not exist yet, so
        # it stays an upper bound; the confirmed minimum remains zero.
        expected = len(day_labels) if name in _DAY_SCOPED_PREVIEW_STEPS and day_labels else media_max
        if expected > 0:
            row.update(
                {
                    "total": expected,
                    "will_run": 0,
                    "will_skip": 0,
                    "will_run_max": expected,
                    "will_skip_min": 0,
                }
            )
        may_run.add(name)
    return may_run


def _apply_run_cascade(
    *,
    sources: dict[str, tuple[str, ...]],
    selected_steps: list[str],
    producers: set[str],
    preview_steps: list[dict[str, Any]],
) -> set[str]:
    """Mark reusable artifacts that same-run upstream work may invalidate.

    ``will_run`` stays the confirmed minimum. A cascaded row keeps its reusable
    count in ``will_skip`` as a maximum, so ``will_run_max``/``will_skip_min``
    describe the real range instead of pretending the cache is final. Only a
    selected step that may actually run propagates the risk.
    """
    rows_by_name = {str(row.get("name")): row for row in preview_steps}
    selected = set(selected_steps)
    may_run = set(producers)
    for name in PIPELINE_PREVIEW_ORDER:
        row = rows_by_name.get(name)
        if row is None:
            continue
        if row.get("deferred"):
            # Waiting rows already expose their own maximum; a cascade marker
            # would describe the same uncertainty twice.
            may_run.add(name)
            continue
        will_run = int(row.get("will_run") or 0)
        will_skip = int(row.get("will_skip") or 0)
        upstream = tuple(src for src in sources.get(name, ()) if src in selected and src in may_run)
        if upstream and will_skip > 0 and not row.get("disabled") and int(row.get("total") or 0) > 0:
            row["cascade_from"] = list(upstream)
            row["cascade_assumed"] = will_skip
            row["will_run_max"] = will_run + will_skip
            row["will_skip_min"] = 0
            row["estimate_is_minimum"] = True
        if will_run > 0 or row.get("cascade_assumed"):
            may_run.add(name)
    return may_run


def _preview_signature(preview_steps: list[dict[str, Any]]) -> tuple[Any, ...]:
    """Snapshot the fields that defer/cascade passes can change."""
    fields = (
        "total",
        "will_run",
        "will_run_max",
        "will_skip",
        "will_skip_min",
        "deferred",
        "waiting_on",
        "cascade_from",
        "cascade_assumed",
        "estimate_is_minimum",
        "warnings",
        "blocking",
        "disabled",
    )
    return tuple(
        (str(row.get("name")), tuple((field, repr(row.get(field))) for field in fields)) for row in preview_steps
    )


def _build_stage_summary(selected_steps: list[str], preview_steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Project step previews into the four user-facing stages.

    The detailed step rows remain in the response for custom execution and
    compatibility. This compact projection gives the guided UI one authoritative
    status/next-action contract without making it repeat cache rules locally.
    """
    rows_by_name = {str(row.get("name")): row for row in preview_steps}
    selected = set(selected_steps)
    stages: list[dict[str, Any]] = []
    blockers: list[str] = []
    for key, stage_steps in _RUN_PREVIEW_STAGES:
        steps = [step for step in stage_steps if step in selected]
        rows = [rows_by_name[step] for step in steps if step in rows_by_name]
        required_rows = [row for row in rows if not row.get("disabled")]
        deferred_rows = [row for row in required_rows if row.get("deferred")]
        total = sum(int(row.get("total") or 0) for row in rows)
        will_run = sum(int(row.get("will_run") or 0) for row in rows)
        will_skip = sum(int(row.get("will_skip") or 0) for row in rows)
        will_run_max = sum(int(row.get("will_run_max", row.get("will_run") or 0)) for row in rows)
        will_skip_min = sum(int(row.get("will_skip_min", row.get("will_skip") or 0)) for row in rows)
        cascade_assumed = sum(int(row.get("cascade_assumed") or 0) for row in rows)
        warnings = [str(item) for row in rows for item in (row.get("warnings") or [])]
        # Optional steps that are disabled for this run must not block the stage:
        # they carry an explanatory warning but no work or artifact requirement.
        required_warnings = [str(item) for row in required_rows for item in (row.get("warnings") or [])]
        waiting_on = sorted({str(item) for row in deferred_rows for item in (row.get("waiting_on") or [])})
        known = len(rows) == len(steps)
        cascade_from = sorted({str(item) for row in required_rows for item in (row.get("cascade_from") or [])})
        reusable = bool(required_rows) and known and all(_row_is_reusable(row) for row in required_rows)
        missing_inputs = any(int(row.get("total") or 0) == 0 for row in required_rows if not row.get("deferred"))
        # A row that cannot use the requested scope blocks its stage even when a
        # sibling step can still run. Otherwise the run button would remain enabled
        # while the scoped consistency/plan work silently does nothing.
        blocked = any(row.get("blocking") for row in required_rows) or (
            will_run == 0 and (missing_inputs or (bool(required_warnings) and not reusable))
        )
        # A deferred stage is satisfied later in the same run, so it waits instead
        # of acting as a final blocker while nothing is actionable yet.
        waiting = not blocked and will_run == 0 and bool(deferred_rows)
        missing = list(required_warnings)
        if not steps:
            status = "excluded"
        elif not known:
            status = "unknown"
        elif not required_rows:
            status = "disabled"
        elif blocked:
            status = "blocked"
        elif waiting:
            status = "waiting"
        elif reusable:
            status = "reusable"
        else:
            status = "ready"
        if status in {"unknown", "blocked"}:
            blockers.extend(item for item in missing if item not in blockers)
        usage = combine_usage(rows)
        output_steps = [str(row["name"]) for row in required_rows]
        output_target = {
            "source_prep": "video",
            "ai_analysis": "texts",
            "plan_alignment": ("plan" if "plan" in steps else "voiceover" if "voiceover" in steps else "texts"),
            "voiceover_delivery": "plan" if "plan_voiceover" in steps else "video",
        }[key]
        stages.append(
            {
                "key": key,
                "steps": steps,
                "status": status,
                "total": total,
                "will_run": will_run,
                "will_run_max": will_run_max,
                "will_skip": will_skip,
                "will_skip_min": will_skip_min,
                "warnings": warnings,
                "blockers": missing if status in {"unknown", "blocked"} else [],
                "estimate": usage,
                "estimate_is_minimum": bool(cascade_assumed or deferred_rows),
                "deferred": bool(deferred_rows) and len(deferred_rows) == len(required_rows),
                "waiting_on": waiting_on,
                "cascade_from": cascade_from,
                "output": {"target": output_target, "steps": output_steps} if output_steps else None,
            }
        )
    next_stage = next(
        (stage["key"] for stage in stages if stage["status"] not in {"excluded", "disabled", "reusable", "waiting"}),
        None,
    )
    return {
        "stages": stages,
        "next_stage": next_stage,
        "blockers": blockers,
        "outputs": [stage["output"] for stage in stages if stage["status"] == "reusable" and stage["output"]],
    }


def build_run_preview(
    config: AppConfig,
    steps: Iterable[str],
    *,
    force: bool,
    use_transcripts: bool,
    files: list[str] | None = None,
    day_label: str = "day1",
    all_days: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    config = copy.deepcopy(config)
    config.plan.use_transcripts = use_transcripts
    force = force or not config.analyze.skip_existing
    # Report rows in the order the pipeline executes so cascade review matches runtime.
    selected_steps = _ordered_steps(steps)
    selected = _selection_from_files(files) if files is not None else None
    preview_day_labels = _preview_day_labels(config, day_label, all_days)

    source_input_info, all_source_videos = _source_input_info(config)
    from clio.tasks._video_loader import source_videos as _load_source_videos

    all_recursive_source_videos = list(_load_source_videos(config))
    compressed_input_info, all_compressed_videos = _compressed_input_info(config)
    all_analysis_jsons = _analysis_jsons(config)
    index = ArtifactIndex(
        output_dir=config.paths.output_dir,
        project_dir=config.project_dir or Path(),
        compressed_dir=config.compressed_dir,
        texts_dir=config.texts_dir,
        scripts_dir=config.scripts_dir,
        transcripts_dir=config.transcripts_dir,
    )
    index.build()

    filtered_source_videos = _filter_stem_paths(all_source_videos, selected)
    compressed_videos = _filter_stem_paths(all_compressed_videos, selected, artifact=True)
    analysis_jsons = _filter_artifact_paths(all_analysis_jsons, selected)

    input_info = compressed_input_info if _starts_from_compressed(selected_steps) else source_input_info
    input_info = {
        **input_info,
        "count": len(compressed_videos) if input_info["mode"] == "compressed" else len(filtered_source_videos),
    }

    preview_steps = [
        _step_preview(
            config,
            step,
            source_videos=filtered_source_videos,
            all_recursive_source_videos=all_recursive_source_videos,
            compressed_videos=compressed_videos,
            analysis_jsons=analysis_jsons,
            all_analysis_jsons=all_analysis_jsons,
            index=index,
            force=force,
            use_transcripts=use_transcripts,
            day_labels=preview_day_labels,
            files=files,
            context_override=context_override,
            task_prompts=task_prompts,
        )
        for step in selected_steps
    ]

    # A cascade can turn an initially reusable row into "may run", which can in
    # turn unlock another row that must wait on it. Iterate to a fixed point so
    # the reported status does not depend on the order of the two passes.
    input_sources = _input_sources(config, use_transcripts=use_transcripts)
    cascade_sources = _cascade_sources(config, use_transcripts=use_transcripts)
    media_max = max(len(filtered_source_videos), len(compressed_videos))
    may_run = _steps_that_run(preview_steps)
    for _ in range(len(PIPELINE_PREVIEW_ORDER) + 1):
        before = _preview_signature(preview_steps)
        may_run = _defer_same_run_inputs(
            sources=input_sources,
            selected_steps=selected_steps,
            producers=may_run,
            preview_steps=preview_steps,
            day_labels=preview_day_labels,
            media_max=media_max,
        )
        may_run = _apply_run_cascade(
            sources=cascade_sources,
            selected_steps=selected_steps,
            producers=may_run,
            preview_steps=preview_steps,
        )
        if _preview_signature(preview_steps) == before:
            break

    # Surface empty selection so UI/CLI can prompt migrate / add-videos.
    # Attach once on input, not per-step, so totals.warnings stays step-scoped.
    empty_selection_note: str | None = None
    if input_info.get("mode") == "videos" and input_info.get("count", 0) == 0 and config.project_dir is not None:
        vjson = config.project_dir / "videos.json"
        empty_selection_note = (
            t("api.preview_no_videos_json") if not vjson.is_file() else t("api.preview_empty_videos_json")
        )
        input_info = {**input_info, "hint": empty_selection_note}

    totals = {
        "selected_steps": len(selected_steps),
        "will_run": sum(step["will_run"] for step in preview_steps),
        "will_skip": sum(step["will_skip"] for step in preview_steps),
        "will_run_max": sum(int(step.get("will_run_max", step["will_run"])) for step in preview_steps),
        "will_skip_min": sum(int(step.get("will_skip_min", step["will_skip"])) for step in preview_steps),
        "warnings": sum(len(step["warnings"]) for step in preview_steps),
        "deferred_steps": sum(1 for step in preview_steps if step.get("deferred")),
        "estimate_is_minimum": any(step.get("estimate_is_minimum") for step in preview_steps),
    }
    totals.update(combine_usage(preview_steps))
    result = {
        "input": input_info,
        "steps": preview_steps,
        "totals": totals,
    }
    result.update(_build_stage_summary(selected_steps, preview_steps))
    if "plan" in selected_steps and plan_overwrite_confirmation_required(config, day_label, files):
        result["plan_overwrite"] = {"required": True, "day_label": day_label}
    overwrite_paths = artifact_overwrite_paths(
        config,
        selected_steps,
        force=force,
        use_transcripts=use_transcripts,
        files=files,
        day_label=day_label,
        all_days=all_days,
        context_override=context_override,
        task_prompts=task_prompts,
    )
    if overwrite_paths:
        result["artifact_overwrite"] = {
            "required": True,
            "items": [path.name for path in overwrite_paths],
        }
    legacy_audio_risk = legacy_audio_removal_risk(
        config,
        selected_steps,
        force=force,
        use_transcripts=use_transcripts,
        files=files,
        context_override=context_override,
        task_prompts=task_prompts,
    )
    if legacy_audio_risk is not None:
        result["legacy_audio_removal"] = legacy_audio_risk
    if "analyze" in selected_steps:
        analysis_risk = analysis_rerun_risk(
            config,
            selected_steps,
            force=force,
            use_transcripts=use_transcripts,
            files=files,
            context_override=context_override,
            task_prompts=task_prompts,
        )
        if analysis_risk is not None:
            result["analysis_rerun"] = analysis_risk
    return result


ANALYSIS_RERUN_REASON_ORDER = (
    "invalid",
    "source_mismatch",
    "provider_model",
    "prompt",
    "context",
    "transcript",
    "compressed_media",
    "window",
    "legacy",
    "upstream",
    "unknown",
)


def _pending_compressed_originals(config: AppConfig, source_videos: list[Path], *, force: bool) -> set[str]:
    """Original stems whose compressed output this run will rewrite."""
    stems = {source.stem.lower() for source in source_videos}
    if force:
        return stems
    try:
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
        existing, _ = _scan_existing_compressed(config, ffprobe, quiet=True)
    except (OSError, RuntimeError):
        # Unverifiable output is treated as pending, matching the compress preview.
        return stems
    return {
        source.stem.lower() for source in source_videos if _find_reusable(source, existing, config, ffprobe)[1] is None
    }


def _pending_transcript_stems(config: AppConfig, compressed_videos: list[Path]) -> set[str]:
    """Compressed stems whose transcript this run will regenerate."""
    index = ArtifactIndex(
        output_dir=config.paths.output_dir,
        project_dir=config.project_dir or Path(),
        compressed_dir=config.compressed_dir,
        texts_dir=config.texts_dir,
        scripts_dir=config.scripts_dir,
        transcripts_dir=config.transcripts_dir,
    )
    index.build()
    pending: set[str] = set()
    for compressed in compressed_videos:
        entry = index.lookup(compressed_stem=compressed.stem)
        if entry is None or not entry.transcript:
            pending.add(compressed.stem)
    return pending


def analysis_rerun_risk(
    config: AppConfig,
    steps: Iterable[str],
    *,
    force: bool,
    use_transcripts: bool,
    files: list[str] | None,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, Any] | None:
    """Explain cache-invalidated analysis reruns before a run starts."""
    if force or not config.analyze.skip_existing or "analyze" not in set(steps):
        return None

    selected = _selection_from_files(files) if files is not None else None
    project_selection = _project_selection_stems(config)
    selected_paths = project_selection.paths if project_selection is not None else None
    stem_cache = _build_stem_to_path(config.project_dir)
    compressed_videos = _video_files(config.compressed_dir, recursive=False)
    if selected is not None:
        compressed_videos = [
            compressed
            for compressed in compressed_videos
            if _matches_selected_stem(compressed, selected, artifact=True)
        ]
    source_videos = list(_source_input_info(config)[1])
    filtered_sources = _filter_stem_paths(source_videos, selected) if selected is not None else source_videos
    pairs = _analysis_input_pairs(config, compressed_videos, source_videos, stem_cache, selected_paths)

    # Same-run upstream work can rewrite analysis input even when the current
    # cache would otherwise be accepted; that is not a safe "reusable" answer.
    selected_names = set(steps)
    at_risk_stems: set[str] = set()
    if "compress" in selected_names:
        pending_originals = _pending_compressed_originals(config, filtered_sources, force=force)
        at_risk_stems |= {
            compressed.stem for compressed, original in pairs if original.stem.lower() in pending_originals
        }
    if "transcribe" in selected_names and use_transcripts and config.whisper.enabled:
        pending_transcripts = _pending_transcript_stems(config, compressed_videos)
        at_risk_stems |= {compressed.stem for compressed, _original in pairs if compressed.stem in pending_transcripts}

    reason_counts = {reason: 0 for reason in ANALYSIS_RERUN_REASON_ORDER}
    rerun_count = 0
    for compressed, original in pairs:
        status = analysis_cache_status(
            config,
            compressed,
            original,
            compressed.stem.split("_", 1)[0],
            overwrite=False,
            context_override=context_override,
            task_prompts=task_prompts,
            duration_sec=_analysis_duration_sec(config, compressed, original),
            use_transcripts=use_transcripts,
        )
        if not status.rerun_required:
            if compressed.stem in at_risk_stems:
                rerun_count += 1
                reason_counts["upstream"] = reason_counts.get("upstream", 0) + 1
            continue
        reasons = list(status.reasons)
        if status.invalid:
            reasons.append("invalid")
        if status.source_mismatch:
            reasons.append("source_mismatch")
        if not reasons:
            reasons.append("unknown")
        rerun_count += 1
        for reason in dict.fromkeys(reasons):
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    if rerun_count == 0:
        return None
    return {
        "required": True,
        "count": rerun_count,
        "reasons": [
            {"key": reason, "count": reason_counts[reason]}
            for reason in ANALYSIS_RERUN_REASON_ORDER
            if reason_counts[reason]
        ],
    }


def _analysis_input_pairs(
    config: AppConfig,
    compressed_videos: list[Path],
    source_videos: list[Path],
    stem_cache: dict[str, Path | None],
    selected_paths: frozenset[str] | None,
) -> list[tuple[Path, Path]]:
    source_names = {source.stem.lower(): source.name for source in source_videos}
    pairs: list[tuple[Path, Path]] = []
    for compressed in _resolvable_analysis_inputs(compressed_videos, source_names):
        original = _resolve_original(
            compressed.stem,
            stem_cache,
            config.project_dir,
            compressed,
            selected_paths,
        )
        if original is not None:
            pairs.append((compressed, original))
    return pairs


def legacy_audio_removal_risk(
    config: AppConfig,
    steps: Iterable[str],
    *,
    force: bool,
    use_transcripts: bool,
    files: list[str] | None,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, int] | None:
    """Return counts for artifacts rerun only because audio removal changed.

    This is intentionally narrower than stale-artifact detection. It identifies
    results produced by the previous ``remove_audio=false`` default which a
    current ``remove_audio=true`` configuration would silently regenerate.
    """
    selected_steps = set(steps)
    if force or not config.analyze.skip_existing or not config.compress.remove_audio:
        return None
    if not selected_steps.intersection({"analyze", "voiceover", "compress"}):
        return None

    analysis_paths = (
        _legacy_audio_analysis_paths(
            config,
            files=files,
            use_transcripts=use_transcripts,
            context_override=context_override,
            task_prompts=task_prompts,
        )
        if "analyze" in selected_steps
        else []
    )
    voiceover_count = (
        _legacy_audio_voiceover_count(
            config,
            analysis_paths,
            files=files,
            context_override=context_override,
            task_prompts=task_prompts,
        )
        if "analyze" in selected_steps and "voiceover" in selected_steps
        else 0
    )
    compress_count = _legacy_audio_compress_count(config, files=files) if "compress" in selected_steps else 0
    counts = {
        "analyze_count": len(analysis_paths),
        "voiceover_count": voiceover_count,
        "compress_count": compress_count,
    }
    if not any(counts.values()):
        return None
    return {"required": True, **counts}


def _legacy_audio_analysis_paths(
    config: AppConfig,
    *,
    files: list[str] | None,
    use_transcripts: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> list[Path]:
    if not config.compressed_dir.is_dir():
        return []
    selected = _selection_from_files(files) if files is not None else None
    project_selection = _project_selection_stems(config)
    selected_paths = project_selection.paths if project_selection is not None else None
    stem_cache = _build_stem_to_path(config.project_dir)
    compressed_videos = sorted(
        path for path in config.compressed_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    legacy_config = copy.deepcopy(config)
    legacy_config.global_cfg.compress.remove_audio = False
    risky: list[Path] = []

    for compressed in prefer_canonical_compressed(compressed_videos):
        parts = compressed.stem.split("_", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        original = _resolve_original(
            compressed.stem,
            stem_cache,
            config.project_dir,
            compressed,
            selected_paths,
        )
        if original is None:
            continue
        if selected is not None and not (
            _matches_selected_stem(compressed, selected, artifact=True) or _matches_selected_stem(original, selected)
        ):
            continue

        duration_sec = _analysis_duration_sec(config, compressed, original)
        effective_context = _effective_analysis_context(config, original, context_override)
        transcript_context = _load_transcript_context(config, compressed) if use_transcripts else ""
        multi_window = _uses_multiple_windows(compressed, duration_sec, config)
        legacy_fingerprint = _legacy_analysis_lineage_fingerprint(
            legacy_config,
            task_prompts,
            effective_context,
            transcript_context,
            multi_window=multi_window,
        )
        current_fingerprint = _legacy_analysis_lineage_fingerprint(
            config,
            task_prompts,
            effective_context,
            transcript_context,
            multi_window=multi_window,
        )
        if current_fingerprint == legacy_fingerprint:
            continue

        valid_lineages: list[object] = []
        legacy_path: Path | None = None
        rerun_would_be_skipped = False
        for candidate in sorted((config.texts_dir).glob(f"{parts[0]}_*.json")):
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError):
                continue
            if not isinstance(data, dict) or data.get("source_file") != original.name:
                continue
            lineage = data.get("_lineage")
            valid_lineages.append(lineage)
            if lineage is None or lineage == current_fingerprint:
                rerun_would_be_skipped = True
            if lineage == legacy_fingerprint and legacy_path is None:
                legacy_path = candidate

        if rerun_would_be_skipped or legacy_path is None:
            continue
        if all(lineage == legacy_fingerprint for lineage in valid_lineages):
            risky.append(legacy_path)
    return risky


def _file_size(path: Path) -> int:
    try:
        return max(0, path.stat().st_size)
    except OSError:
        return 0


def _analysis_duration_sec(config: AppConfig, compressed: Path, original: Path) -> float:
    meta = VideoMeta.read(compressed)
    if meta is not None and meta.target_duration_sec > 0:
        return meta.target_duration_sec
    try:
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
    except (OSError, TypeError, ValueError):
        return 0.0
    for candidate in (compressed, original):
        try:
            duration_sec = get_duration_sec(candidate, ffprobe)
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            continue
        if duration_sec > 0:
            return duration_sec
    return 0.0


def _legacy_audio_voiceover_count(
    config: AppConfig,
    analysis_paths: list[Path],
    *,
    files: list[str] | None,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> int:
    if not analysis_paths:
        return 0
    selected = _selection_from_files(files) if files is not None else None
    template = _load_script_template(config)
    count = 0
    for analysis_path in analysis_paths:
        if selected is not None and not _matches_selected_artifact(analysis_path, selected):
            continue
        try:
            data = json.loads(analysis_path.read_text(encoding="utf-8"))
            existing = json.loads(
                (config.scripts_dir / f"{analysis_path.stem}_voiceover.json").read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError, UnicodeError):
            continue
        if not isinstance(data, dict) or not isinstance(existing, dict):
            continue
        lineage = existing.get("_lineage")
        if not isinstance(lineage, str) or not lineage:
            continue
        data["index"] = data.get("index", analysis_path.stem[:3])
        current = _voiceover_lineage_fingerprint(
            config,
            data,
            template,
            task_prompts,
            context_override,
        )
        if lineage == current:
            count += 1
    return count


def _legacy_audio_compress_count(config: AppConfig, *, files: list[str] | None) -> int:
    if not config.compressed_dir.is_dir():
        return 0
    selected = _selection_from_files(files) if files is not None else None
    _, source_videos = _source_input_info(config)
    source_videos = _filter_stem_paths(source_videos, selected)
    source_keys = {_path_key(source): source for source in source_videos}
    if not source_keys:
        return 0

    risky_sources: set[str] = set()
    for compressed in sorted(config.compressed_dir.iterdir()):
        if not compressed.is_file() or compressed.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        meta = VideoMeta.read(compressed)
        if meta is None or meta.compress_settings.get("remove_audio") is not False:
            continue
        source_key = _path_key(meta.source_path)
        if source_key in source_keys:
            risky_sources.add(source_key)
    return len(risky_sources)


def _analysis_artifact_overwrite_paths(
    config: AppConfig,
    *,
    force: bool,
    files: list[str] | None,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> list[Path]:
    """Return protected analysis JSON/TXT files this analyze run would replace."""
    if not config.compressed_dir.is_dir() or not config.texts_dir.is_dir():
        return []
    protected_indices = {
        candidate.stem.split("_", 1)[0]
        for candidate in config.texts_dir.glob("*.json")
        if candidate.stem.split("_", 1)[0].isdigit() and artifact_requires_overwrite_confirmation(candidate)
    }
    if not protected_indices:
        return []

    selected = _selection_from_files(files) if files is not None else None
    project_selection = _project_selection_stems(config)
    selected_paths = project_selection.paths if project_selection is not None else None
    stem_cache = _build_stem_to_path(config.project_dir)
    compressed_videos = sorted(
        path for path in config.compressed_dir.iterdir() if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    risky: list[Path] = []
    for compressed in prefer_canonical_compressed(compressed_videos):
        parts = compressed.stem.split("_", 1)
        if len(parts) != 2 or parts[0] not in protected_indices:
            continue
        original = _resolve_original(
            compressed.stem,
            stem_cache,
            config.project_dir,
            compressed,
            selected_paths,
        )
        if original is None:
            continue
        if selected is not None and not (
            _matches_selected_stem(compressed, selected, artifact=True) or _matches_selected_stem(original, selected)
        ):
            continue
        risky.extend(
            analysis_artifact_overwrite_required(
                config,
                compressed,
                original,
                parts[0],
                overwrite=force,
                context_override=context_override,
                task_prompts=task_prompts,
                duration_sec=_analysis_duration_sec(config, compressed, original),
            )
        )
    return risky


def artifact_overwrite_paths(
    config: AppConfig,
    steps: Iterable[str],
    *,
    force: bool,
    use_transcripts: bool,
    files: list[str] | None = None,
    day_label: str = "day1",
    all_days: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> list[Path]:
    """Return user-edited artifacts a run would replace."""
    selected_steps = set(steps)
    artifact_config = copy.deepcopy(config)
    artifact_config.plan.use_transcripts = use_transcripts
    day_labels = _preview_day_labels(artifact_config, day_label, all_days)
    paths: list[Path] = []
    if "analyze" in selected_steps:
        paths.extend(
            _analysis_artifact_overwrite_paths(
                artifact_config,
                force=force,
                files=files,
                context_override=context_override,
                task_prompts=task_prompts,
            )
        )
    if "plan" in selected_steps:
        for scoped_day in day_labels:
            if not plan_artifact_overwrite_required(
                artifact_config,
                scoped_day,
                files,
                overwrite=force,
                context_override=context_override,
                task_prompts=task_prompts,
            ):
                continue
            safe_day = plan_day_stem(scoped_day)
            paths.extend(
                path
                for path in (
                    artifact_config.plans_dir / f"{safe_day}_plan.json",
                    artifact_config.plans_dir / f"{safe_day}_plan.md",
                )
                if path.is_file()
            )
    if "voiceover" in selected_steps:
        paths.extend(
            voiceover_artifact_overwrite_required(
                artifact_config,
                files,
                overwrite=force,
                context_override=context_override,
                task_prompts=task_prompts,
            )
        )
    if "plan_voiceover" in selected_steps:
        for scoped_day in day_labels:
            paths.extend(
                plan_voiceover_artifact_overwrite_required(
                    artifact_config,
                    scoped_day,
                    overwrite=force,
                    context_override=context_override,
                    task_prompts=task_prompts,
                )
            )
    unique: dict[Path, Path] = {}
    for path in paths:
        unique.setdefault(path.resolve(), path)
    return list(unique.values())


def _starts_from_compressed(steps: list[str]) -> bool:
    return bool(steps) and steps[0] in {"analyze", "transcribe"}


def _preview_day_labels(config: AppConfig, day_label: str, all_days: bool) -> list[str]:
    if not all_days:
        return [day_label]
    return discover_day_labels(config) or [day_label]


def _source_input_info(config: AppConfig) -> tuple[dict[str, Any], list[Path]]:
    raw_project_input = getattr(config, "input", None)
    project_input = Path(raw_project_input) if raw_project_input else None
    if project_input and project_input.is_file():
        videos = [project_input] if _is_video(project_input) else []
        return {"mode": "file", "path": str(project_input), "count": len(videos)}, videos

    from clio.tasks._video_loader import source_videos

    videos = source_videos(config)
    project_dir = config.project_dir
    return {
        "mode": "videos",
        "path": str(project_dir) if project_dir else "",
        "count": len(videos),
    }, videos


def _compressed_input_info(config: AppConfig) -> tuple[dict[str, Any], list[Path]]:
    compressed_dir = config.compressed_dir
    videos = _video_files(compressed_dir, recursive=False)
    return {"mode": "compressed", "path": str(compressed_dir), "count": len(videos)}, videos


def _step_preview(
    config: AppConfig,
    step: str,
    *,
    source_videos: list[Path],
    all_recursive_source_videos: list[Path],
    compressed_videos: list[Path],
    analysis_jsons: list[Path],
    all_analysis_jsons: list[Path],
    index: ArtifactIndex,
    force: bool,
    use_transcripts: bool,
    day_labels: list[str],
    files: list[str] | None,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, Any]:
    if step == "compress":
        return _compress_step(config, source_videos, force=force)
    if step == "analyze":
        return _analyze_step(
            config,
            compressed_videos,
            all_recursive_source_videos,
            force=force,
            use_transcripts=use_transcripts,
            context_override=context_override,
            task_prompts=task_prompts,
        )
    if step == "transcribe":
        if not use_transcripts or not config.whisper.enabled:
            return {**_warning_step(step, t("api.preview_transcribe_disabled")), "disabled": True}
        return _transcribe_step(config, compressed_videos, all_recursive_source_videos, force=force, index=index)
    if step == "consistency":
        return _consistency_step(
            config,
            all_analysis_jsons,
            day_labels=day_labels,
            force=force,
            files=files,
            context_override=context_override,
            task_prompts=task_prompts,
        )
    if step == "voiceover":
        return _voiceover_step(
            config, analysis_jsons, force=force, context_override=context_override, task_prompts=task_prompts
        )
    if step == "plan":
        return _plan_step(
            config,
            all_analysis_jsons,
            day_labels=day_labels,
            files=files,
            force=force,
            context_override=context_override,
            task_prompts=task_prompts,
        )
    if step == "plan_voiceover":
        return _plan_voiceover_step(
            config,
            all_analysis_jsons,
            day_labels=day_labels,
            force=force,
            context_override=context_override,
            task_prompts=task_prompts,
        )
    if step == "label":
        return _label_step(config, analysis_jsons, force=force)
    return _unknown_step(step)


def _compress_step(config: AppConfig, source_videos: list[Path], *, force: bool) -> dict[str, Any]:
    total = len(source_videos)
    if force:
        return _step("compress", total=total, will_run=total, will_skip=0)

    try:
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
        existing, _ = _scan_existing_compressed(config, ffprobe, quiet=True)
        skipped = sum(_find_reusable(source, existing, config, ffprobe)[1] is not None for source in source_videos)
    except (OSError, RuntimeError):
        # Unverifiable output must remain pending; the start preflight explains missing binaries.
        skipped = 0
    return _step("compress", total=total, will_run=total - skipped, will_skip=skipped)


def _analyze_step(
    config: AppConfig,
    compressed_videos: list[Path],
    source_videos: list[Path],
    *,
    force: bool,
    use_transcripts: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, Any]:
    project_selection = _project_selection_stems(config)
    selected_paths = project_selection.paths if project_selection is not None else None
    pairs = _analysis_input_pairs(
        config,
        compressed_videos,
        source_videos,
        _build_stem_to_path(config.project_dir),
        selected_paths,
    )
    total = len(pairs)
    skipped = 0
    running: list[tuple[Path, Path, float]] = []
    for compressed, original in pairs:
        duration_sec = _analysis_duration_sec(config, compressed, original)
        if force:
            running.append((compressed, original, duration_sec))
            continue
        status = analysis_cache_status(
            config,
            compressed,
            original,
            compressed.stem.split("_", 1)[0],
            overwrite=False,
            context_override=context_override,
            task_prompts=task_prompts,
            duration_sec=duration_sec,
            use_transcripts=use_transcripts,
        )
        if status.accepted:
            skipped += 1
        else:
            running.append((compressed, original, duration_sec))
    usage = estimate_analyze_usage(
        config,
        (
            AnalyzeInput(
                duration_sec=duration_sec,
                file_bytes=_file_size(compressed),
                legacy_split=is_legacy_split_path(compressed),
            )
            for compressed, _original, duration_sec in running
        ),
    )
    return _step("analyze", total=total, will_run=len(running), will_skip=skipped, usage=usage)


def _transcribe_step(
    config: AppConfig,
    compressed_videos: list[Path],
    source_videos: list[Path],
    *,
    force: bool,
    index: ArtifactIndex,
) -> dict[str, Any]:
    source_names = {source.stem.lower(): source.name for source in source_videos}
    inputs = _resolvable_analysis_inputs(compressed_videos, source_names)
    total = len(inputs)
    if force:
        return _step("transcribe", total=total, will_run=total, will_skip=0)

    skipped = sum(1 for compressed in inputs if (g := index.lookup(compressed_stem=compressed.stem)) and g.transcript)
    return _step("transcribe", total=total, will_run=total - skipped, will_skip=skipped)


def _consistency_step(
    config: AppConfig,
    analysis_jsons: list[Path],
    *,
    day_labels: list[str],
    force: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
    files: list[str] | None,
) -> dict[str, Any]:
    if not analysis_jsons:
        return _warning_step("consistency", t("api.preview_missing_analyze_consistency"))
    total = len(day_labels)
    available_days = discover_day_labels(config)
    running_days: list[str] = []
    blocking_warnings: list[str] = []
    for scoped_day in day_labels:
        out_json, out_md = entity_consistency_paths(config, scoped_day)
        clips, _, _ = _collect_plan_clips(
            config, scoped_day, files, filter_by_day=True, cancel_event=None, report=False
        )
        if files is None and not clips:
            scope_warning = _no_analyzed_media_for_day_message(config, scoped_day, available=available_days)
            if scope_warning is not None:
                blocking_warnings.append(scope_warning)
                continue
        if (
            force
            or files is not None
            or load_entity_consistency(config, scoped_day, clips, task_prompts, context_override) is None
        ):
            running_days.append(scoped_day)
    usage = estimate_usage(config, (consistency_call() for _ in running_days))
    return _step(
        "consistency",
        total=total,
        will_run=len(running_days),
        will_skip=total - len(running_days),
        warnings=blocking_warnings,
        blocking=bool(blocking_warnings),
        usage=usage,
    )


def _voiceover_step(
    config: AppConfig,
    analysis_jsons: list[Path],
    *,
    force: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, Any]:
    total = len(analysis_jsons)
    if total == 0:
        return _warning_step("voiceover", t("api.preview_missing_analyze_voiceover"))
    running = []
    template = _load_script_template(config)
    for analysis_json in analysis_jsons:
        try:
            data = json.loads(analysis_json.read_text(encoding="utf-8"))
            data["index"] = data.get("index", analysis_json.stem[:3])
            lineage = _voiceover_lineage_fingerprint(config, data, template, task_prompts, context_override)
            reusable = read_reusable_artifact(
                config.scripts_dir / f"{analysis_json.stem}_voiceover.json", lineage, allow_legacy=True
            )
        except (OSError, UnicodeError, ValueError, TypeError, AttributeError):
            reusable = None
        if force or reusable is None:
            running.append(analysis_json)
    usage = estimate_usage(config, (voiceover_call(config) for _ in running))
    return _step("voiceover", total=total, will_run=len(running), will_skip=total - len(running), usage=usage)


def _plan_step(
    config: AppConfig,
    analysis_jsons: list[Path],
    *,
    day_labels: list[str],
    files: list[str] | None,
    force: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, Any]:
    if not analysis_jsons:
        return _warning_step("plan", t("api.preview_missing_analyze_plan"))
    total = len(day_labels)
    filter_by_day = _resolve_filter_by_day(config, files, None)
    available_days = discover_day_labels(config)
    running_days: list[str] = []
    blocking_warnings: list[str] = []
    for scoped_day in day_labels:
        safe_day = plan_day_stem(scoped_day)
        plan_json = config.plans_dir / f"{safe_day}_plan.json"
        plan_md = config.plans_dir / f"{safe_day}_plan.md"
        clips, _, _ = _collect_plan_clips(
            config,
            scoped_day,
            files,
            filter_by_day=filter_by_day,
            cancel_event=None,
            report=False,
        )
        if filter_by_day and not clips:
            scope_warning = _no_analyzed_media_for_day_message(config, scoped_day, available=available_days)
            if scope_warning is not None:
                blocking_warnings.append(scope_warning)
                continue
        lineage = _plan_lineage_fingerprint(
            config, clips, day_label=scoped_day, task_prompts=task_prompts, context_override=context_override
        )
        if (
            force
            or files is not None
            or not plan_md.is_file()
            or read_reusable_artifact(plan_json, lineage, allow_legacy=True) is None
        ):
            running_days.append(scoped_day)
    calls = [call for _ in running_days for call in plan_calls()]
    usage = estimate_usage(config, calls)
    return _step(
        "plan",
        total=total,
        will_run=len(running_days),
        will_skip=total - len(running_days),
        warnings=blocking_warnings,
        blocking=bool(blocking_warnings),
        usage=usage,
    )


def _plan_voiceover_step(
    config: AppConfig,
    analysis_jsons: list[Path],
    *,
    day_labels: list[str],
    force: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, Any]:
    planned_labels = []
    for scoped_day in day_labels:
        safe_day = plan_day_stem(scoped_day)
        if _valid_plan_object(config.plans_dir / f"{safe_day}_plan.json"):
            planned_labels.append(scoped_day)
    if not planned_labels:
        return _warning_step("plan_voiceover", t("api.preview_missing_plan_voiceover_plan"))
    if not analysis_jsons:
        return _warning_step("plan_voiceover", t("api.preview_missing_plan_voiceover_analysis"))

    total = len(planned_labels)
    running_days: list[str] = []
    for scoped_day in planned_labels:
        safe_day = plan_day_stem(scoped_day)
        out_json = config.scripts_dir / f"{safe_day}_plan_voiceover.json"
        out_md = config.scripts_dir / f"{safe_day}_plan_voiceover.md"
        plan = json.loads((config.plans_dir / f"{safe_day}_plan.json").read_text(encoding="utf-8"))
        _, _, inputs = _load_plan_voiceover_inputs(config, plan)
        lineage = _plan_voiceover_lineage_fingerprint(
            config, plan, inputs, _load_script_template(config), task_prompts, context_override
        )
        if force or not out_md.is_file() or read_reusable_artifact(out_json, lineage) is None:
            running_days.append(scoped_day)
    planned_calls = sum(
        _plan_sequence_length(config.plans_dir / f"{plan_day_stem(scoped_day)}_plan.json")
        for scoped_day in running_days
    )
    usage = estimate_usage(config, (plan_voiceover_call(config) for _ in range(planned_calls)))
    return _step(
        "plan_voiceover",
        total=total,
        will_run=len(running_days),
        will_skip=total - len(running_days),
        usage=usage,
    )


def _label_has_compressed_input(config: AppConfig, analysis_json: Path) -> bool:
    """Mirror run_label_videos input resolution for preview counts.

    Malformed or unreadable analysis should remain runnable in the preview so the
    runtime surfaces its concrete error instead of the row being silently reusable.
    """
    try:
        data = json.loads(analysis_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return True
    if not isinstance(data, dict):
        return True
    index = _label_index(data, analysis_json, config.naming.index_width)
    compressed = _find_compressed_video(config.compressed_dir, index)
    return compressed is not None and compressed.exists()


def _label_step(config: AppConfig, analysis_jsons: list[Path], *, force: bool) -> dict[str, Any]:
    total = len(analysis_jsons)
    if total == 0:
        return _warning_step("label", t("api.preview_missing_analyze_label"))
    labeled_dir = config.paths.output_dir / "labeled"

    def will_skip(analysis_json: Path) -> bool:
        if not _label_has_compressed_input(config, analysis_json):
            return True
        if force or not config.analyze.skip_existing:
            return False
        return (labeled_dir / f"{analysis_json.stem}_labeled.mp4").exists()

    skipped = sum(1 for analysis_json in analysis_jsons if will_skip(analysis_json))
    return _step("label", total=total, will_run=total - skipped, will_skip=skipped)


def _warning_step(step: str, warning: str) -> dict[str, Any]:
    return _step(step, total=0, will_run=0, will_skip=0, warnings=[warning])


def _unknown_step(step: str) -> dict[str, Any]:
    return _step(step, total=0, will_run=0, will_skip=0, warnings=[t("api.preview_unknown_step", {"step": step})])


def _step(
    name: str,
    *,
    total: int,
    will_run: int,
    will_skip: int,
    warnings: list[str] | None = None,
    usage: dict[str, Any] | None = None,
    blocking: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": _step_label(name),
        "total": total,
        "will_run": will_run,
        "will_skip": will_skip,
        "warnings": warnings or [],
        **({"blocking": True} if blocking else {}),
        **not_applicable_usage(),
        **(usage or {}),
    }


def _analysis_jsons(config: AppConfig) -> list[Path]:
    texts_dirs = _find_texts_dirs(config.texts_dir)
    return sorted(path for texts_dir in texts_dirs for path in texts_dir.iterdir() if path.suffix.lower() == ".json")


def _find_texts_dirs(texts_dir: Path) -> list[Path]:
    if texts_dir.is_dir():
        return [texts_dir]
    output_dir = texts_dir.parent
    if not output_dir.is_dir():
        return []
    return sorted(path for path in output_dir.rglob(texts_dir.name) if path.is_dir())


def _filter_stem_paths(paths: list[Path], selected: MediaSelection | None, *, artifact: bool = False) -> list[Path]:
    if selected is None:
        return paths
    return [path for path in paths if _matches_selected_stem(path, selected, artifact=artifact)]


def _filter_artifact_paths(paths: list[Path], selected: MediaSelection | None) -> list[Path]:
    if selected is None:
        return paths
    return [path for path in paths if _matches_selected_artifact(path, selected)]


def _video_files(path: Path, *, recursive: bool) -> list[Path]:
    if not path.is_dir():
        return []
    candidates = path.rglob("*") if recursive else path.iterdir()
    return sorted(item for item in candidates if item.is_file() and _is_video(item))


def _resolvable_analysis_inputs(compressed_videos: list[Path], source_names: dict[str, str]) -> list[Path]:
    inputs = []
    for compressed in compressed_videos:
        prefix, stem_part = _indexed_stem_parts(compressed.stem)
        if not prefix:
            continue
        original_stem = _original_stem_for_compressed_part(stem_part)
        if original_stem.lower() in source_names:
            inputs.append(compressed)
    return inputs


def _valid_json_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return True


def _valid_plan_object(path: Path) -> bool:
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except (OSError, UnicodeError, ValueError):
        return False


def _plan_sequence_length(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(data, dict) or not isinstance(data.get("sequence"), list):
        return 0
    return sum(1 for segment in data["sequence"] if isinstance(segment, dict))


def _indexed_stem_parts(stem: str) -> tuple[str, str]:
    if "_" not in stem:
        return "", stem
    prefix, stem_part = stem.split("_", 1)
    if not prefix.isdigit():
        return "", stem
    return prefix, stem_part


def _original_stem_for_compressed_part(stem_part: str) -> str:
    return SEGMENT_SUFFIX_RE.sub("", stem_part)


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS

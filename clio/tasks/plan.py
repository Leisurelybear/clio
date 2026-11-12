"""Planning task - generate daily vlog editing plan."""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import threading
from pathlib import Path
from typing import Any

from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import plan_daily_vlog
from clio.artifact_guard import (
    ArtifactOverwriteConfirmationRequired,
    artifact_requires_overwrite_confirmation,
    backup_artifacts,
    clear_user_edited,
)
from clio.config import AppConfig
from clio.cut_snap import snap_plan_ranges
from clio.gpmf import load_telemetry_summary
from clio.i18n import t
from clio.identity import SEGMENT_SUFFIX_RE, legacy_segment_offset_sec, load_identity
from clio.log import timed
from clio.plan_input import build_plan_clips
from clio.processing_state import ProcessingState
from clio.progress import ProgressReporter
from clio.prompt_overrides import resolve_prompt_template
from clio.prompts import (
    PLAN_OUTLINE_CONTEXT,
    PLAN_OUTLINE_PROMPT,
    PLAN_PROMPT,
    PLAN_REVIEW_PROMPT,
    TRANSCRIPT_CONTEXT,
    TRANSITION_REVIEW_PROMPT,
    VISUAL_TRANSITION_REVIEW_VERSION,
)
from clio.schema import add_schema_version
from clio.tasks._helpers import (
    _filename_capture_time,
    _matches_selected_artifact,
    _probe_creation_time_text,
    _project_selection_stems,
    _selection_from_files,
    _task_lineage_settings,
)
from clio.tasks.consistency import load_entity_consistency
from clio.utils import format_index, plan_day_stem, resolve_binary, write_json_atomic, write_text_atomic
from clio.vmeta import VideoMeta

_CAPTURE_TIME_KEYS = ("capture_time", "recorded_at", "creation_time", "date_time")


class PlanOverwriteConfirmationRequired(RuntimeError):
    """Raised when a selection-scoped plan would replace an existing day plan."""


def plan_overwrite_confirmation_required(config: AppConfig, day_label: str, files: list[str] | None) -> bool:
    """Return whether files= would replace an existing day plan."""
    if files is None:
        return False
    safe_day = plan_day_stem(day_label)
    return (config.plans_dir / f"{safe_day}_plan.json").exists() or (config.plans_dir / f"{safe_day}_plan.md").exists()


def plan_artifact_overwrite_required(
    config: AppConfig,
    day_label: str,
    files: list[str] | None = None,
    *,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> bool:
    """Return whether a run would replace a user-edited plan."""
    safe_day = plan_day_stem(day_label)
    out_json = config.plans_dir / f"{safe_day}_plan.json"
    out_md = config.plans_dir / f"{safe_day}_plan.md"
    if not artifact_requires_overwrite_confirmation(out_json):
        return False
    if files is not None:
        clips, _, _ = _collect_plan_clips(
            config,
            day_label,
            files,
            filter_by_day=False,
            cancel_event=None,
            report=False,
        )
        return bool(clips)
    if overwrite or not config.analyze.skip_existing or not out_json.is_file() or not out_md.is_file():
        return True
    existing, error = _read_json_object(out_json)
    if error is not None or existing is None:
        return True
    clips, _, _ = _collect_plan_clips(
        config,
        day_label,
        None,
        filter_by_day=False,
        cancel_event=None,
        report=False,
    )
    if not clips:
        return False
    current = _plan_lineage_fingerprint(
        config,
        clips,
        day_label=day_label,
        task_prompts=task_prompts,
        context_override=context_override,
    )
    stored = existing.get("_lineage")
    return stored is not None and stored != current


def _positive_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _clip_duration_sec(config: AppConfig, data: dict[str, Any], identity: Any) -> float | None:
    """Resolve a usable source/segment duration without requiring ffprobe."""
    segment_duration = _positive_number(getattr(identity, "segment_duration_sec", None))
    if segment_duration is not None:
        return round(segment_duration, 3)

    for key in ("duration_sec", "source_duration_sec", "target_duration_sec"):
        duration = _positive_number(data.get(key))
        if duration is not None:
            return round(duration, 3)

    compressed_name = Path(str(data.get("compressed_file") or "")).name
    if not compressed_name:
        return None
    try:
        metadata = VideoMeta.read(config.compressed_dir / compressed_name)
    except (KeyError, OSError, TypeError, ValueError):
        return None
    if metadata is None:
        return None
    duration = _positive_number(metadata.source_duration_sec) or _positive_number(metadata.target_duration_sec)
    return round(duration, 3) if duration is not None else None


def _capture_time(
    data: dict[str, Any],
    source_stem: str,
    source_path: str = "",
    ffprobe: str = "",
    gpmf_capture_time: str | None = None,
) -> str:
    raw_metadata = data.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    for key in _CAPTURE_TIME_KEYS:
        for values in (data, metadata):
            value = values.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    if gpmf_capture_time and gpmf_capture_time.strip():
        return gpmf_capture_time.strip()

    if source_path and ffprobe and Path(source_path).is_file():
        probed = _probe_creation_time_text(Path(source_path), ffprobe)
        if probed:
            return probed

    captured = _filename_capture_time(source_stem)
    if captured is not None:
        return captured.isoformat(sep=" ", timespec="seconds")
    return ""


def _read_voiceover(config: AppConfig, analysis_path: Path) -> str:
    path = config.scripts_dir / f"{analysis_path.stem}_voiceover.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    value = data.get("voiceover") if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) else ""


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None, t("task.plan.json_unreadable")
    if not isinstance(data, dict):
        return None, t("task.plan.json_not_object")
    return data, None


def _report_skipped_inputs(skipped: list[tuple[str, str]]) -> None:
    if not skipped:
        return
    preview = ", ".join(f"{name} ({reason})" for name, reason in skipped[:5])
    suffix = t("task.plan.skipped_more", {"count": len(skipped)}) if len(skipped) > 5 else ""
    print(t("task.plan.skipped_files", {"count": len(skipped), "preview": preview, "suffix": suffix}))


def _unusable_inputs_error(skipped: list[tuple[str, str]]) -> RuntimeError:
    names = ", ".join(name for name, _ in skipped[:5])
    suffix = t("task.plan.unusable_more") if len(skipped) > 5 else ""
    return RuntimeError(t("task.plan.unusable_inputs", {"names": names, "suffix": suffix}))


def _analysis_day_label(data: dict[str, Any]) -> str:
    raw = data.get("day_label") or data.get("day") or data.get("dayLabel") or "day1"
    label = str(raw).strip()
    return label or "day1"


def _source_inputs_from_clips(clips: list[dict]) -> list[dict[str, Any]]:
    """Build authoritative input-pool provenance for a generated plan."""
    rows: list[dict[str, Any]] = []
    for clip in clips:
        try:
            segment_offset_sec = float(clip.get("segment_offset_sec", 0.0) or 0.0)
        except (TypeError, ValueError):
            segment_offset_sec = 0.0
        rows.append(
            {
                "index": str(clip.get("index") or ""),
                "source_stem": str(clip.get("source_stem") or ""),
                "match_stem": str(clip.get("match_stem") or ""),
                "segment_offset_sec": segment_offset_sec,
            }
        )
    return rows


def _plan_lineage_fingerprint(
    config: AppConfig,
    clips: list[dict],
    day_label: str = "day1",
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
    consistency_report: dict[str, Any] | None = None,
) -> str:
    """Fingerprint everything that can change a cached plan result.

    Changing any planning-stage prompt, the task provider/model, the clip
    inputs, or the transcripts toggle invalidates the skip_existing cache so
    users never silently keep a plan generated under an older lineage.
    """
    from clio.analyze import _prompt_context_parts

    if consistency_report is None:
        consistency_report = load_entity_consistency(
            config,
            day_label,
            clips,
            task_prompts,
            context_override,
        )
    prompts: dict[str, str] = {}
    for prompt_name, builtin_template in (
        ("vlog_plan", PLAN_PROMPT),
        ("plan_outline", PLAN_OUTLINE_PROMPT),
        ("plan_review", PLAN_REVIEW_PROMPT),
        ("transition_review", TRANSITION_REVIEW_PROMPT),
    ):
        try:
            prompts[prompt_name] = resolve_prompt_template(
                prompt_name, builtin_template, config, task_prompts=task_prompts
            )
        except (OSError, UnicodeError):
            prompts[prompt_name] = builtin_template
    prompts["plan_outline_context"] = PLAN_OUTLINE_CONTEXT
    transcripts_enabled = bool(getattr(config.plan, "use_transcripts", False)) and bool(
        getattr(config.whisper, "enabled", False)
    )
    selection = _project_selection_stems(config)
    transcript_lineage: list[dict[str, Any]] = []
    transcript_prompt = ""
    transcripts_dir = getattr(config, "transcripts_dir", None)
    if transcripts_enabled and isinstance(transcripts_dir, Path) and transcripts_dir.is_dir():
        transcript_prompt = resolve_prompt_template(
            "transcript_context", TRANSCRIPT_CONTEXT, config, task_prompts=task_prompts
        )
        for transcript in sorted(transcripts_dir.glob("*_transcript.json")):
            if selection is not None and not _matches_selected_artifact(transcript, selection):
                continue
            try:
                digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
                transcript_lineage.append({"name": transcript.name, "sha256": digest})
            except OSError:
                continue

    payload = json.dumps(
        {
            "task": _task_lineage_settings(config, "vlog_plan"),
            "day_label": day_label,
            "prompts": prompts,
            "use_transcripts": transcripts_enabled,
            "max_clips_per_day": getattr(config.plan, "max_clips_per_day", 12),
            "target_duration_sec": getattr(config.plan, "target_duration_sec", 180),
            "max_timeline_rows_per_clip": getattr(config.plan, "max_timeline_rows_per_clip", 8),
            "max_key_moments_per_clip": getattr(config.plan, "max_key_moments_per_clip", 6),
            "visual_transition_review": bool(getattr(config.plan, "visual_transition_review", False)),
            "transition_review_version": VISUAL_TRANSITION_REVIEW_VERSION,
            "transition_review_prompt": prompts["transition_review"],
            "transition_review_task": _task_lineage_settings(config, "video_analyze"),
            "max_transcript_segments_per_clip": (
                getattr(config.whisper, "max_segments_per_clip", 5) if transcripts_enabled else None
            ),
            "transcript_prompt": transcript_prompt,
            # Only projected fields affect the prompt. Detailed timeline rows
            # omitted by the budget must not invalidate an otherwise valid plan.
            "clips": build_plan_clips(clips, config),
            "transcripts": transcript_lineage,
            "entity_consistency": consistency_report,
            "context": _prompt_context_parts(config, context_override),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _discover_day_labels(config: AppConfig) -> list[str]:
    labels, _ = _discover_day_labels_with_errors(config)
    return labels


def _discover_day_labels_with_errors(config: AppConfig) -> tuple[list[str], list[tuple[str, str]]]:
    labels: set[str] = set()
    skipped: list[tuple[str, str]] = []
    selection = _project_selection_stems(config)
    for json_file in sorted(config.texts_dir.glob("*.json")):
        if selection is not None and not _matches_selected_artifact(json_file, selection):
            continue
        data, error = _read_json_object(json_file)
        if error is not None:
            skipped.append((json_file.name, error))
            continue
        if data is None:
            skipped.append((json_file.name, t("task.plan.json_not_object")))
            continue
        labels.add(_analysis_day_label(data))
    return sorted(labels), skipped


def _collect_plan_clips(
    config: AppConfig,
    day_label: str,
    files: list[str] | None,
    *,
    filter_by_day: bool,
    cancel_event: threading.Event | None,
    report: bool = True,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]], bool]:
    selected = _selection_from_files(files) if files is not None else None
    project_selection = _project_selection_stems(config)
    if report and selected is not None:
        print(t("task.plan.filter_selected", {"count": len(selected)}))

    clips: list[dict[str, Any]] = []
    skipped_inputs: list[tuple[str, str]] = []
    for json_file in sorted(config.texts_dir.glob("*.json")):
        if cancel_event and cancel_event.is_set():
            return clips, skipped_inputs, True
        if selected is not None and not _matches_selected_artifact(json_file, selected):
            continue
        if project_selection is not None and not _matches_selected_artifact(json_file, project_selection):
            continue
        data, error = _read_json_object(json_file)
        if error is not None:
            skipped_inputs.append((json_file.name, error))
            continue
        if data is None:
            skipped_inputs.append((json_file.name, t("task.plan.json_not_object")))
            continue
        if filter_by_day and _analysis_day_label(data) != day_label:
            continue
        raw_idx = data.get("index")
        if raw_idx is None:
            raw_idx = json_file.stem[:3]
        try:
            idx = int(raw_idx)
        except (ValueError, TypeError):
            if report:
                print(t("task.plan.invalid_index", {"index": raw_idx, "name": json_file.name}))
            skipped_inputs.append((json_file.name, t("task.plan.index_invalid_reason")))
            continue
        identity = load_identity(data)
        if identity is not None:
            source_stem = identity.original_stem
            match_stem = identity.compressed_stem
            segment_offset = legacy_segment_offset_sec(identity)
        else:
            source_stem = Path(data.get("source_file", "")).stem or json_file.stem
            match_stem = source_stem
            segment_offset = 0.0
        capture_source = str(getattr(identity, "original_path", "") or data.get("source_file", "") or "")
        compressed_source = str(getattr(identity, "compressed_path", "") or data.get("compressed_file", "") or "")
        ffprobe = ""
        if capture_source and Path(capture_source).is_file():
            with contextlib.suppress(OSError):
                ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
        telemetry = load_telemetry_summary(Path(capture_source)) if capture_source else None
        clip: dict[str, Any] = {
            "index": format_index(idx, config.naming.index_width),
            "title": data.get("title", ""),
            "summary": data.get("summary", ""),
            "location": data.get("location", ""),
            "duration_sec": _clip_duration_sec(config, data, identity),
            "capture_time": _capture_time(
                data,
                source_stem,
                capture_source,
                ffprobe,
                telemetry.capture_time if telemetry is not None else None,
            ),
            "mood": data.get("mood", ""),
            "voiceover": _read_voiceover(config, json_file),
            "timeline": data.get("timeline", []),
            "highlights": data.get("highlights", []),
            "key_moments": data.get("key_moments", []),
            "suggested_use": data.get("suggested_use", ""),
            "source_stem": source_stem,
            "match_stem": match_stem,
            "segment_offset_sec": segment_offset,
            "_original_path": capture_source,
            "_compressed_path": compressed_source,
        }
        if telemetry is not None and telemetry.latitude is not None and telemetry.longitude is not None:
            clip["gps"] = {"latitude": telemetry.latitude, "longitude": telemetry.longitude}
        clips.append(clip)
    return clips, skipped_inputs, False


def run_plan_vlog(
    config: AppConfig,
    day_label: str = "day1",
    tracker: ProgressReporter | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    confirm_plan_overwrite: bool = False,
    confirm_artifact_overwrite: bool = False,
    context_override: str | None = None,
    filter_by_day: bool = False,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    config.plans_dir.mkdir(parents=True, exist_ok=True)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    safe_day = plan_day_stem(day_label)

    out_json = config.plans_dir / f"{safe_day}_plan.json"
    out_md = config.plans_dir / f"{safe_day}_plan.md"
    if not confirm_plan_overwrite and plan_overwrite_confirmation_required(config, day_label, files):
        raise PlanOverwriteConfirmationRequired(t("task.plan.overwrite_confirmation_required", {"day": day_label}))

    clips, skipped_inputs, cancelled = _collect_plan_clips(
        config,
        day_label,
        files,
        filter_by_day=filter_by_day,
        cancel_event=cancel_event,
    )
    if cancelled:
        print(t("task.plan.cancelled"))
        return None

    _report_skipped_inputs(skipped_inputs)
    if not clips:
        if skipped_inputs:
            raise _unusable_inputs_error(skipped_inputs)
        print(t("task.plan.no_usable_media"))
        return None

    consistency_report = load_entity_consistency(
        config,
        day_label,
        clips,
        task_prompts,
        context_override,
    )

    # files= means a selection-scoped plan; never short-circuit with a prior full plan (I1).
    if files is None and not overwrite and config.analyze.skip_existing and out_json.exists() and out_md.exists():
        try:
            existing = json.loads(out_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError):
            print(t("task.plan.replan_corrupt", {"day": day_label}))
        else:
            if not isinstance(existing, dict):
                print(t("task.plan.replan_corrupt", {"day": day_label}))
            else:
                stored = existing.get("_lineage")
                current = _plan_lineage_fingerprint(
                    config,
                    clips,
                    day_label=day_label,
                    task_prompts=task_prompts,
                    context_override=context_override,
                )
                if stored == current:
                    print(t("task.plan.skip_existing", {"day": day_label}))
                    return existing
                print(t("task.plan.replan_lineage", {"day": day_label}))
                if stored is None:
                    # Legacy cache without lineage: accept it (best effort) by stamping
                    # a fresh marker so only genuinely-new inputs invalidate it.
                    existing["_lineage"] = current
                    write_json_atomic(out_json, existing)
                    print(t("task.plan.skip_existing", {"day": day_label}))
                    return existing
                # Mismatched/newer lineage: recompute below.

    protected_overwrite = artifact_requires_overwrite_confirmation(out_json)
    if protected_overwrite and not confirm_artifact_overwrite:
        raise ArtifactOverwriteConfirmationRequired(
            t("task.artifact.overwrite_confirmation_required", {"items": out_json.name}),
            [out_json, out_md],
        )

    project_selection = _project_selection_stems(config)
    transcripts_map: dict[str, dict] = {}
    trans_dir = config.transcripts_dir
    if trans_dir.is_dir() and config.whisper.enabled and config.plan.use_transcripts:
        for tf in sorted(trans_dir.glob("*_transcript.json")):
            if project_selection is not None and not _matches_selected_artifact(tf, project_selection):
                continue
            data, error = _read_json_object(tf)
            if error is not None or data is None:
                continue
            identity = load_identity(data)
            if identity is not None:
                stem = identity.compressed_stem
            else:
                stem = data.get("source_stem", "")
                if not isinstance(stem, str):
                    continue
                if "_" in stem:
                    stem = stem.split("_", 1)[1]
                stem = SEGMENT_SUFFIX_RE.sub("", stem)
            if stem:
                transcripts_map[stem.lower()] = data
    if config.plan.use_transcripts and not transcripts_map:
        print(t("task.plan.transcripts_missing"))
        print(t("task.plan.transcripts_missing_hint"))

    plan_clips = build_plan_clips(clips, config)
    timeline_rows = sum(len(clip.get("timeline", [])) for clip in plan_clips)
    projected_chars = len(json.dumps(plan_clips, ensure_ascii=False, separators=(",", ":")))

    if tracker:
        tracker.update(phase="plan", total=1, current=0, message=t("task.plan.progress_message", {"day": day_label}))
    with timed(t("task.plan.timed_label", {"day": day_label, "count": len(clips)})):
        print(t("task.plan.material_count", {"day": day_label, "count": len(clips)}))
        print(
            t(
                "task.plan.budget_projection",
                {
                    "clips": len(plan_clips),
                    "rows": timeline_rows,
                    "chars": projected_chars,
                },
            )
        )
        plan = plan_daily_vlog(
            plan_clips,
            config,
            day_label,
            transcripts_map=transcripts_map,
            use_transcripts=config.plan.use_transcripts,
            token_store=token_store,
            context_override=context_override,
            task_prompts=task_prompts,
            full_clips=clips,
            entity_consistency=consistency_report,
            cancel_event=cancel_event,
        )
    snapped_count = snap_plan_ranges(plan, clips, transcripts_map)
    if snapped_count:
        print(t("task.plan.snap_summary", {"count": snapped_count}))
    from clio.plan_model import Plan

    plan_obj = Plan.from_dict(plan)
    if snapped_count:
        plan_obj.recalculate_total_estimated_sec()
    plan = plan_obj.to_dict()
    plan["source_inputs"] = _source_inputs_from_clips(clips)
    if config.plan.use_transcripts:
        plan["_transcripts_missing"] = not transcripts_map
    plan = add_schema_version(plan)
    clear_user_edited(plan)
    plan["_lineage"] = _plan_lineage_fingerprint(
        config,
        clips,
        day_label=day_label,
        task_prompts=task_prompts,
        context_override=context_override,
        consistency_report=consistency_report,
    )
    if protected_overwrite:
        backup_artifacts([out_json, out_md])
    write_json_atomic(out_json, plan)
    if tracker:
        tracker.log(t("task.plan.log_done", {"day": day_label}))

    lines = [
        f"# {plan.get('day_title', day_label)}",
        "",
        t("plan.md_theme", {"theme": plan.get("theme", "")}),
        t("plan.md_estimated", {"seconds": plan.get("total_estimated_sec", "")}),
        "",
    ]
    source_inputs = plan.get("source_inputs") or []
    if source_inputs:
        lines.append(t("plan.md_source_section"))
        lines.append("")
        for entry in source_inputs:
            idx = entry.get("index", "?")
            stem = entry.get("source_stem", "")
            lines.append(f"- `{idx}` {stem}")
        lines.append("")
    lines.append(t("plan.md_sequence_section"))
    for item in plan.get("sequence", []):
        lines.extend(
            [
                f"### {item.get('index', '?')} {item.get('title', '')}",
                t("plan.md_reason", {"reason": item.get("reason", "")}),
                t("plan.md_timeline", {"timeline": item.get("use_timeline", "")}),
                t("plan.md_voiceover", {"hint": item.get("voiceover_hint", "")}),
                "",
            ]
        )
    lines.extend(
        [
            t("plan.md_opening_section"),
            plan.get("opening_tip", ""),
            "",
            t("plan.md_ending_section"),
            plan.get("ending_tip", ""),
        ]
    )
    write_text_atomic(out_md, "\n".join(lines))
    print(f"  -> {out_md.name}")

    state = ProcessingState(config.paths.output_dir)
    for clip in clips:
        source_stem = clip.get("source_stem", "")
        if source_stem:
            state.mark(source_stem, "plan", "done")
    return plan


def run_plan_all_days(
    config: AppConfig,
    tracker: ProgressReporter | None = None,
    cancel_event: threading.Event | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    labels, skipped_inputs = _discover_day_labels_with_errors(config)
    if not labels:
        if skipped_inputs:
            _report_skipped_inputs(skipped_inputs)
            raise _unusable_inputs_error(skipped_inputs)
        print(t("task.plan.no_analysis"))
        return None

    summary: dict[str, Any] = {"days": []}
    for day_label in labels:
        if cancel_event and cancel_event.is_set():
            print(t("task.plan.all_days_cancelled"))
            break
        plan = run_plan_vlog(
            config,
            day_label=day_label,
            tracker=tracker,
            cancel_event=cancel_event,
            overwrite=overwrite,
            context_override=context_override,
            filter_by_day=True,
            task_prompts=task_prompts,
        )
        if plan is None:
            continue
        sequence = plan.get("sequence", [])
        summary["days"].append(
            {
                "day_label": day_label,
                "day_title": plan.get("day_title", day_label),
                "theme": plan.get("theme", ""),
                "clip_count": len(sequence) if isinstance(sequence, list) else 0,
                "total_estimated_sec": plan.get("total_estimated_sec", 0),
                "plan_file": f"{plan_day_stem(day_label)}_plan.json",
            }
        )

    if not summary["days"]:
        return None
    add_schema_version(summary)
    config.plans_dir.mkdir(parents=True, exist_ok=True)
    out_json = config.plans_dir / "trip_plan.json"
    write_json_atomic(out_json, summary)
    print(f"  -> {out_json.name}")
    return summary

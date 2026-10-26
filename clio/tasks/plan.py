"""Planning task - generate daily vlog editing plan."""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import plan_daily_vlog
from clio.config import AppConfig
from clio.i18n import t
from clio.identity import SEGMENT_SUFFIX_RE, legacy_segment_offset_sec, load_identity
from clio.log import timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.prompt_overrides import resolve_prompt_template
from clio.prompts import PLAN_PROMPT
from clio.schema import add_schema_version
from clio.tasks._helpers import _matches_selected_artifact, _selected_stems
from clio.utils import format_index, plan_day_stem, write_json_atomic, write_text_atomic


def _read_analysis_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
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


def _analysis_day_label(data: dict) -> str:
    raw = data.get("day_label") or data.get("day") or data.get("dayLabel") or "day1"
    label = str(raw).strip()
    return label or "day1"


def _source_inputs_from_clips(clips: list[dict]) -> list[dict[str, str]]:
    """Build authoritative input-pool provenance for a generated plan."""
    return [
        {
            "index": str(c.get("index") or ""),
            "source_stem": str(c.get("source_stem") or ""),
        }
        for c in clips
    ]


def _plan_lineage_fingerprint(
    config: AppConfig,
    clips: list[dict],
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> str:
    """Fingerprint everything that can change a cached plan result.

    Changing the vlog_plan prompt, its provider/model, the clip inputs, or the
    transcripts toggle invalidates the skip_existing cache so users never
    silently keep a plan generated under an older lineage.
    """
    from clio.analyze import _prompt_context_parts

    try:
        task = config.ai.tasks.get("vlog_plan")
        provider = getattr(task, "provider", "") if task else ""
        model = getattr(task, "model", "") if task else ""
    except Exception:
        provider, model = "", ""
    try:
        prompt = resolve_prompt_template("vlog_plan", PLAN_PROMPT, config, task_prompts=task_prompts)
    except Exception:
        prompt = PLAN_PROMPT
    transcript_lineage: list[dict[str, Any]] = []
    transcripts_dir = getattr(config, "transcripts_dir", None)
    if (
        getattr(config.plan, "use_transcripts", False)
        and getattr(config.whisper, "enabled", False)
        and isinstance(transcripts_dir, Path)
        and transcripts_dir.is_dir()
    ):
        for transcript in sorted(transcripts_dir.glob("*_transcript.json")):
            try:
                digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
                transcript_lineage.append({"name": transcript.name, "sha256": digest})
            except OSError:
                continue

    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "use_transcripts": getattr(config.plan, "use_transcripts", False),
            "max_tokens": getattr(config.ai, "max_tokens", None),
            # Planning depends on the complete analysis, not only its title.
            # Include the content-bearing fields so an improved analysis
            # invalidates a cached plan.
            "clips": [
                {
                    "index": c.get("index", ""),
                    "source_stem": c.get("source_stem", ""),
                    "match_stem": c.get("match_stem", ""),
                    "segment_offset_sec": c.get("segment_offset_sec", 0.0),
                    "title": c.get("title", ""),
                    "summary": c.get("summary", ""),
                    "location": c.get("location", ""),
                    "timeline": c.get("timeline", []),
                    "highlights": c.get("highlights", []),
                    "suggested_use": c.get("suggested_use", ""),
                }
                for c in clips
            ],
            "transcripts": transcript_lineage,
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
    for json_file in sorted(config.texts_dir.glob("*.json")):
        data, error = _read_analysis_json(json_file)
        if error is not None:
            skipped.append((json_file.name, error))
            continue
        labels.add(_analysis_day_label(data))
    return sorted(labels), skipped


def run_plan_vlog(
    config: AppConfig,
    day_label: str = "day1",
    tracker: ProgressTracker | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    filter_by_day: bool = False,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    config.plans_dir.mkdir(parents=True, exist_ok=True)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    safe_day = plan_day_stem(day_label)

    selected = _selected_stems(files) if files is not None else None
    if selected is not None:
        print(t("task.plan.filter_selected", {"count": len(selected)}))

    out_json = config.plans_dir / f"{safe_day}_plan.json"
    out_md = config.plans_dir / f"{safe_day}_plan.md"

    clips = []
    skipped_inputs: list[tuple[str, str]] = []
    for json_file in sorted(config.texts_dir.glob("*.json")):
        if cancel_event and cancel_event.is_set():
            print(t("task.plan.cancelled"))
            return
        if selected is not None and not _matches_selected_artifact(json_file, selected):
            continue
        data, error = _read_analysis_json(json_file)
        if error is not None:
            skipped_inputs.append((json_file.name, error))
            continue
        if filter_by_day and _analysis_day_label(data) != day_label:
            continue
        raw_idx = data.get("index")
        if raw_idx is None:
            raw_idx = json_file.stem[:3]
        try:
            idx = int(raw_idx)
        except (ValueError, TypeError):
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
        clips.append(
            {
                "index": format_index(idx, config.naming.index_width),
                "title": data.get("title", ""),
                "summary": data.get("summary", ""),
                "location": data.get("location", ""),
                "timeline": data.get("timeline", []),
                "highlights": data.get("highlights", []),
                "suggested_use": data.get("suggested_use", ""),
                "source_stem": source_stem,
                "match_stem": match_stem,
                "segment_offset_sec": segment_offset,
            }
        )

    _report_skipped_inputs(skipped_inputs)
    if not clips:
        if skipped_inputs:
            raise _unusable_inputs_error(skipped_inputs)
        print(t("task.plan.no_usable_media"))
        return None

    # files= means a selection-scoped plan; never short-circuit with a prior full plan (I1).
    if files is None and not overwrite and config.analyze.skip_existing and out_json.exists() and out_md.exists():
        try:
            existing = json.loads(out_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(t("task.plan.replan_corrupt", {"day": day_label}))
        else:
            stored = existing.get("_lineage")
            current = _plan_lineage_fingerprint(config, clips, task_prompts, context_override)
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

    transcripts_map: dict[str, dict] = {}
    trans_dir = config.transcripts_dir
    if trans_dir.is_dir() and config.whisper.enabled and config.plan.use_transcripts:
        for tf in sorted(trans_dir.glob("*_transcript.json")):
            try:
                data = json.loads(tf.read_text(encoding="utf-8"))
                identity = load_identity(data)
                if identity is not None:
                    stem = identity.compressed_stem
                else:
                    stem = data.get("source_stem", "")
                    if "_" in stem:
                        stem = stem.split("_", 1)[1]
                    stem = SEGMENT_SUFFIX_RE.sub("", stem)
                if stem:
                    transcripts_map[stem.lower()] = data
            except (json.JSONDecodeError, KeyError):
                continue
    if config.plan.use_transcripts and not transcripts_map:
        print(t("task.plan.transcripts_missing"))
        print(t("task.plan.transcripts_missing_hint"))

    if tracker:
        tracker.update(phase="plan", total=1, current=0, message=t("task.plan.progress_message", {"day": day_label}))
    with timed(t("task.plan.timed_label", {"day": day_label, "count": len(clips)})):
        print(t("task.plan.material_count", {"day": day_label, "count": len(clips)}))
        plan = plan_daily_vlog(
            clips,
            config,
            day_label,
            transcripts_map=transcripts_map,
            use_transcripts=config.plan.use_transcripts,
            token_store=token_store,
            context_override=context_override,
            task_prompts=task_prompts,
        )
    from clio.plan_model import Plan

    plan_obj = Plan.from_dict(plan)
    plan = plan_obj.to_dict()
    plan["source_inputs"] = _source_inputs_from_clips(clips)
    if config.plan.use_transcripts:
        plan["_transcripts_missing"] = not transcripts_map
    plan = add_schema_version(plan)
    plan["_lineage"] = _plan_lineage_fingerprint(config, clips, task_prompts, context_override)
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
    tracker: ProgressTracker | None = None,
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

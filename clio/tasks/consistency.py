"""Generate a same-day entity and duplicate-scene consistency report."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path
from typing import Any

from clio.ai.base import TaskName
from clio.ai.factory import get_task_provider, provider_use
from clio.ai.structured import ENTITY_CONSISTENCY_OUTPUT
from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import (
    _call_ai,
    _prompt_context_parts,
    _system_context,
    _task_temperature,
    _usage_meta,
    _wrap_with_context,
    extract_json,
)
from clio.config import AppConfig
from clio.cut import _to_seconds
from clio.i18n import t
from clio.log import timed
from clio.plan_input import build_plan_clips
from clio.prompt_overrides import format_prompt_template, resolve_prompt_template
from clio.prompts import ENTITY_CONSISTENCY_PROMPT
from clio.schema import ARTIFACT_SCHEMA_VERSION, add_schema_version
from clio.tasks._helpers import _task_lineage_settings
from clio.utils import plan_day_stem, write_json_atomic, write_text_atomic

_MAX_ENTITIES = 100
_MAX_DUPLICATE_GROUPS = 50
_MAX_ALIASES = 20
_MAX_ENTITY_CLIPS = 200
_MAX_GROUP_CLIPS = 50
_MAX_NAME_CHARS = 120
_MAX_KIND_CHARS = 40
_MAX_REASON_CHARS = 500
_DURATION_TOLERANCE_SEC = 0.05


def entity_consistency_paths(config: AppConfig, day_label: str) -> tuple[Path, Path]:
    safe_day = plan_day_stem(day_label)
    return (
        config.plans_dir / f"{safe_day}_entity_consistency.json",
        config.plans_dir / f"{safe_day}_entity_consistency.md",
    )


def _invalid(detail: str) -> ValueError:
    return ValueError(t("task.consistency.invalid_output", {"detail": detail}))


def _bounded_text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise _invalid(t("task.consistency.invalid_field", {"field": field}))
    text = value.strip()
    if not text:
        raise _invalid(t("task.consistency.empty_field", {"field": field}))
    return text[:limit]


def _index_key(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _clip_index_map(clips: list[dict[str, Any]]) -> tuple[dict[str, str], dict[int, str | None]]:
    exact: dict[str, str] = {}
    numeric: dict[int, str | None] = {}
    for clip in clips:
        canonical = _index_key(clip.get("index"))
        if not canonical:
            continue
        exact[canonical] = canonical
        if canonical.isdigit():
            number = int(canonical)
            if number in numeric and numeric[number] != canonical:
                numeric[number] = None
            else:
                numeric[number] = canonical
    return exact, numeric


def _clip_index(value: Any, exact: dict[str, str], numeric: dict[int, str | None]) -> str:
    raw = _index_key(value)
    if not raw:
        raise _invalid(t("task.consistency.missing_index"))
    if raw in exact:
        return exact[raw]
    if raw.isdigit():
        canonical = numeric.get(int(raw))
        if canonical:
            return canonical
    raise _invalid(t("task.consistency.unknown_index", {"index": raw}))


def _clip_durations(clips: list[dict[str, Any]]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for clip in clips:
        index = _index_key(clip.get("index"))
        value = clip.get("duration_sec")
        if not index or value is None or isinstance(value, bool):
            continue
        try:
            duration = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(duration) and duration > 0:
            durations[index] = duration
    return durations


def _normalized_clip_refs(
    value: Any,
    *,
    field: str,
    exact: dict[str, str],
    numeric: dict[int, str | None],
    limit: int,
) -> list[str]:
    if not isinstance(value, list):
        raise _invalid(t("task.consistency.invalid_list", {"field": field}))
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        index = _clip_index(raw, exact, numeric)
        if index not in seen:
            result.append(index)
            seen.add(index)
        if len(result) >= limit:
            break
    if not result:
        raise _invalid(t("task.consistency.empty_list", {"field": field}))
    return result


def _normalize_aliases(value: Any, canonical_name: str) -> list[str]:
    if not isinstance(value, list):
        raise _invalid(t("task.consistency.invalid_list", {"field": "aliases"}))
    aliases: list[str] = []
    seen = {canonical_name.casefold()}
    for raw in value:
        alias = _bounded_text(raw, "aliases", _MAX_NAME_CHARS)
        key = alias.casefold()
        if key in seen:
            continue
        aliases.append(alias)
        seen.add(key)
        if len(aliases) >= _MAX_ALIASES:
            break
    return aliases


def _normalize_entities(value: Any, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _invalid(t("task.consistency.invalid_list", {"field": "entities"}))
    exact, numeric = _clip_index_map(clips)
    merged: dict[str, dict[str, Any]] = {}
    for item in value[:_MAX_ENTITIES]:
        if not isinstance(item, dict):
            raise _invalid(t("task.consistency.invalid_item", {"field": "entities"}))
        canonical_name = _bounded_text(item.get("canonical_name"), "canonical_name", _MAX_NAME_CHARS)
        kind = _bounded_text(item.get("kind"), "kind", _MAX_KIND_CHARS)
        aliases = _normalize_aliases(item.get("aliases"), canonical_name)
        entity_clips = _normalized_clip_refs(
            item.get("clips"),
            field="entity clips",
            exact=exact,
            numeric=numeric,
            limit=_MAX_ENTITY_CLIPS,
        )
        key = canonical_name.casefold()
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                "canonical_name": canonical_name,
                "kind": kind,
                "aliases": aliases,
                "clips": entity_clips,
            }
            continue
        alias_seen = {existing["canonical_name"].casefold(), *(alias.casefold() for alias in existing["aliases"])}
        for alias in aliases:
            alias_key = alias.casefold()
            if alias_key not in alias_seen and len(existing["aliases"]) < _MAX_ALIASES:
                existing["aliases"].append(alias)
                alias_seen.add(alias_key)
        clip_seen = set(existing["clips"])
        for index in entity_clips:
            if index not in clip_seen and len(existing["clips"]) < _MAX_ENTITY_CLIPS:
                existing["clips"].append(index)
                clip_seen.add(index)
    return list(merged.values())


def _timestamp(value: Any, field: str) -> tuple[str, float]:
    if not isinstance(value, str) or not value.strip():
        raise _invalid(t("task.consistency.invalid_timestamp", {"field": field}))
    text = value.strip()
    try:
        seconds = _to_seconds(text)
    except (TypeError, ValueError) as exc:
        raise _invalid(t("task.consistency.invalid_timestamp", {"field": field})) from exc
    return text, seconds


def _normalize_duplicate_scenes(value: Any, clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _invalid(t("task.consistency.invalid_list", {"field": "duplicate_scenes"}))
    exact, numeric = _clip_index_map(clips)
    durations = _clip_durations(clips)
    groups: list[dict[str, Any]] = []
    for item in value[:_MAX_DUPLICATE_GROUPS]:
        if not isinstance(item, dict):
            raise _invalid(t("task.consistency.invalid_item", {"field": "duplicate_scenes"}))
        canonical_name = _bounded_text(item.get("canonical_name"), "canonical_name", _MAX_NAME_CHARS)
        reason = _bounded_text(item.get("reason"), "reason", _MAX_REASON_CHARS)
        raw_clips = item.get("clips")
        if not isinstance(raw_clips, list):
            raise _invalid(t("task.consistency.invalid_list", {"field": "duplicate scene clips"}))
        normalized_clips: list[dict[str, str]] = []
        seen_indexes: set[str] = set()
        for raw_clip in raw_clips[:_MAX_GROUP_CLIPS]:
            if not isinstance(raw_clip, dict):
                raise _invalid(t("task.consistency.invalid_item", {"field": "duplicate scene clips"}))
            index = _clip_index(raw_clip.get("index"), exact, numeric)
            if index in seen_indexes:
                raise _invalid(t("task.consistency.duplicate_clip_index", {"index": index}))
            start_text, start = _timestamp(raw_clip.get("start"), "start")
            end_text, end = _timestamp(raw_clip.get("end"), "end")
            if end <= start:
                raise _invalid(t("task.consistency.invalid_range", {"index": index}))
            duration = durations.get(index)
            if duration is not None and end > duration + _DURATION_TOLERANCE_SEC:
                raise _invalid(t("task.consistency.range_out_of_bounds", {"index": index}))
            normalized_clips.append({"index": index, "start": start_text, "end": end_text})
            seen_indexes.add(index)
        if len({clip["index"] for clip in normalized_clips}) < 2:
            raise _invalid(t("task.consistency.not_enough_clips", {"name": canonical_name}))
        groups.append({"canonical_name": canonical_name, "clips": normalized_clips, "reason": reason})
    return groups


def normalize_entity_consistency(data: Any, clips: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate and normalize an AI consistency report against known clips."""
    if not isinstance(data, dict):
        raise _invalid(t("task.consistency.invalid_root"))
    return {
        "entities": _normalize_entities(data.get("entities"), clips),
        "duplicate_scenes": _normalize_duplicate_scenes(data.get("duplicate_scenes"), clips),
    }


def _rendered_consistency_prompt(
    config: AppConfig,
    clips: list[dict[str, Any]],
    task_prompts: dict[str, str] | None,
) -> tuple[list[dict[str, Any]], str]:
    projected = build_plan_clips(clips, config)
    template = resolve_prompt_template(
        "entity_consistency",
        ENTITY_CONSISTENCY_PROMPT,
        config,
        task_prompts=task_prompts,
    )
    prompt = format_prompt_template(
        "entity_consistency",
        template,
        clips_json=json.dumps(projected, ensure_ascii=False, separators=(",", ":")),
    )
    return projected, prompt


def entity_consistency_lineage_fingerprint(
    config: AppConfig,
    clips: list[dict[str, Any]],
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> str:
    projected, prompt = _rendered_consistency_prompt(config, clips, task_prompts)
    payload = json.dumps(
        {
            "task": _task_lineage_settings(config, "refine_text"),
            "prompt": prompt,
            "clips": projected,
            "context": _prompt_context_parts(config, context_override),
        },
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_entity_consistency(
    config: AppConfig,
    day_label: str,
    clips: list[dict[str, Any]],
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> dict[str, Any] | None:
    """Return a valid current-lineage report, or None when planning must ignore it."""
    out_json, out_md = entity_consistency_paths(config, day_label)
    if not out_json.is_file() or not out_md.is_file():
        return None
    try:
        data = json.loads(out_json.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        if data.get("_schema_version") != ARTIFACT_SCHEMA_VERSION:
            return None
        if data.get("day_label") != day_label:
            return None
        current = entity_consistency_lineage_fingerprint(config, clips, task_prompts, context_override)
        if data.get("_lineage") != current:
            return None
        normalized = normalize_entity_consistency(data, clips)
        return {
            **normalized,
            "day_label": day_label,
            "_schema_version": ARTIFACT_SCHEMA_VERSION,
            "_lineage": current,
        }
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _report_markdown(report: dict[str, Any], day_label: str) -> str:
    entities = report.get("entities", [])
    duplicates = report.get("duplicate_scenes", [])
    lines = [
        f"# {t('task.consistency.md_title', {'day': day_label})}",
        "",
        t("task.consistency.md_summary", {"entities": len(entities), "duplicates": len(duplicates)}),
        "",
        t("task.consistency.md_entities_section"),
        "",
    ]
    if not entities:
        lines.append(t("task.consistency.md_no_entities"))
    for entity in entities:
        lines.extend(
            [
                f"### {entity['canonical_name']}",
                t("task.consistency.md_kind", {"kind": entity["kind"]}),
                t(
                    "task.consistency.md_aliases",
                    {"aliases": ", ".join(entity["aliases"]) or t("task.consistency.md_none")},
                ),
                t("task.consistency.md_clips", {"clips": ", ".join(entity["clips"])}),
                "",
            ]
        )
    lines.extend(["", t("task.consistency.md_duplicates_section"), ""])
    if not duplicates:
        lines.append(t("task.consistency.md_no_duplicates"))
    for group in duplicates:
        lines.append(f"### {group['canonical_name']}")
        for clip in group["clips"]:
            lines.append(
                t(
                    "task.consistency.md_duplicate_clip",
                    {"index": clip["index"], "start": clip["start"], "end": clip["end"]},
                )
            )
        lines.extend(
            [
                t("task.consistency.md_reason", {"reason": group["reason"]}),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_entity_consistency(
    config: AppConfig,
    day_label: str = "day1",
    tracker=None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Generate and persist the same-day entity/duplicate consistency report."""
    from clio.tasks.plan import _collect_plan_clips, _report_skipped_inputs, _unusable_inputs_error

    config.plans_dir.mkdir(parents=True, exist_ok=True)
    out_json, out_md = entity_consistency_paths(config, day_label)
    clips, skipped_inputs, cancelled = _collect_plan_clips(
        config,
        day_label,
        files,
        filter_by_day=True,
        cancel_event=cancel_event,
    )
    if cancelled:
        print(t("task.consistency.cancelled"))
        return None
    _report_skipped_inputs(skipped_inputs)
    if not clips:
        if skipped_inputs:
            raise _unusable_inputs_error(skipped_inputs)
        print(t("task.consistency.no_usable_media"))
        return None

    if files is None and not overwrite and config.analyze.skip_existing and out_json.is_file() and out_md.is_file():
        existing = load_entity_consistency(config, day_label, clips, task_prompts, context_override)
        if existing is not None:
            print(t("task.consistency.skip_existing", {"day": day_label}))
            return existing
        print(t("task.consistency.regen_lineage", {"day": day_label}))

    _projected, base = _rendered_consistency_prompt(config, clips, task_prompts)
    system = _system_context(config)
    prompt = _wrap_with_context(base, config, context_override=context_override, include_spec=False)
    provider, model = get_task_provider(config, TaskName.REFINE_TEXT)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))

    if tracker:
        tracker.update(
            phase="consistency",
            total=1,
            current=0,
            message=t("task.consistency.progress_message", {"day": day_label}),
        )
    with timed(t("task.consistency.timed_label", {"day": day_label, "count": len(clips)})):
        print(t("task.consistency.material_count", {"day": day_label, "count": len(clips)}))
        with provider_use(provider):
            text = _call_ai(
                t("analyze.label.entity_consistency"),
                provider.provider_id,
                model,
                prompt,
                lambda: provider.generate_text(
                    prompt,
                    model,
                    cancel_event=cancel_event,
                    temperature=_task_temperature(config, TaskName.REFINE_TEXT),
                    system=system,
                    structured_output=ENTITY_CONSISTENCY_OUTPUT,
                ),
                debug_print=config.ai.debug_print_prompt,
                token_store=token_store,
                task_name=TaskName.REFINE_TEXT,
                cancel_event=cancel_event,
                system_message=system,
                **_usage_meta(config, TaskName.REFINE_TEXT),
            )
    if cancel_event and cancel_event.is_set():
        print(t("task.consistency.cancelled"))
        return None

    try:
        normalized = normalize_entity_consistency(extract_json(text), clips)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _invalid(str(exc)) from exc
    report = add_schema_version(
        {
            **normalized,
            "day_label": day_label,
            "_lineage": entity_consistency_lineage_fingerprint(
                config,
                clips,
                task_prompts,
                context_override,
            ),
        }
    )
    write_json_atomic(out_json, report)
    write_text_atomic(out_md, _report_markdown(report, day_label))
    print(f"  -> {out_md.name}")
    if tracker:
        tracker.log(t("task.consistency.log_done", {"day": day_label}))
    return report

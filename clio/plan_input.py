"""Bounded plan-input projection for detailed analysis artifacts."""

from __future__ import annotations

import math
from typing import Any

from clio.config import AppConfig

_CAMERAS = frozenset({"fixed", "handheld", "pan", "zoom", "follow"})
_QUALITIES = frozenset({"ok", "shaky", "dark", "blocked"})


def _positive_limit(config: AppConfig, name: str, default: int) -> int:
    try:
        value = int(getattr(config.plan, name, default))
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    parts = str(value).strip().split(":")
    if len(parts) not in (2, 3):
        return None
    try:
        if len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        else:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _format_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    prefix = f"{hours:02d}:" if hours else ""
    suffix = f".{milliseconds:03d}".rstrip("0") if milliseconds else ""
    return f"{prefix}{minutes:02d}:{whole_seconds:02d}{suffix}"


def _span(item: dict[str, Any]) -> tuple[float, float] | None:
    start = _timestamp_seconds(item.get("start"))
    end = _timestamp_seconds(item.get("end"))
    if start is None:
        return None
    if end is None:
        end = start + 1.0
    if end <= start:
        return None
    return start, end


def _span_or_inf(item: dict[str, Any]) -> tuple[float, float]:
    span = _span(item)
    return span if span is not None else (math.inf, math.inf)


def _score(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(1, min(5, int(round(number))))


def _project_timeline_row(item: dict[str, Any]) -> dict[str, Any] | None:
    span = _span(item)
    if span is None:
        return None
    row: dict[str, Any] = {
        "start": _format_time(span[0]),
        "end": _format_time(span[1]),
        "description": str(item.get("description") or ""),
    }
    score = _score(item.get("score"))
    if score is not None:
        row["score"] = score
    camera = item.get("camera")
    if isinstance(camera, str) and camera in _CAMERAS:
        row["camera"] = camera
    quality = item.get("quality")
    if isinstance(quality, str) and quality in _QUALITIES:
        row["quality"] = quality
    tags = item.get("tags")
    if isinstance(tags, list):
        clean_tags = [tag.strip() for tag in tags if isinstance(tag, str) and tag.strip()]
        if clean_tags:
            row["tags"] = list(dict.fromkeys(clean_tags))
    return row


def _project_moment(item: dict[str, Any]) -> dict[str, Any] | None:
    span = _span(item)
    if span is None:
        return None
    start, end = span
    title = str(item.get("title") or item.get("description") or "").strip()
    why = str(item.get("why") or item.get("description") or "").strip()
    moment: dict[str, Any] = {
        "start": _format_time(start),
        "end": _format_time(end),
    }
    if title:
        moment["title"] = title
    if why:
        moment["why"] = why
    score = _score(item.get("score"))
    if score is not None:
        moment["score"] = score
    return moment


def _dedupe_moments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[float, float, str]] = set()
    for item in items:
        span = _span(item)
        if span is None:
            continue
        key = (*span, str(item.get("title") or item.get("why") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _cap_moments(items: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    projected = [moment for item in items if isinstance(item, dict) and (moment := _project_moment(item)) is not None]
    projected = _dedupe_moments(projected)
    if limit > 0 and len(projected) > limit:
        ranked = sorted(
            enumerate(projected),
            key=lambda pair: (
                -(pair[1].get("score") or 0),
                _span_or_inf(pair[1])[0],
                pair[0],
            ),
        )
        projected = [item for _, item in ranked[:limit]]
    return sorted(projected, key=_span_or_inf)


def _overlaps(start: float, end: float, spans: list[tuple[float, float]]) -> bool:
    return any(max(start, left) < min(end, right) for left, right in spans)


def _select_timeline_rows(
    rows: list[dict[str, Any]], limit: int, priority_spans: list[tuple[float, float]]
) -> list[dict[str, Any]]:
    parsed: list[tuple[int, dict[str, Any], float, float]] = []
    for position, row in enumerate(rows):
        span = _span(row)
        if span is not None:
            parsed.append((position, row, span[0], span[1]))
    parsed.sort(key=lambda item: (item[2], item[3], item[0]))
    if limit <= 0 or len(parsed) <= limit:
        return [item[1] for item in parsed]

    if limit == 1:
        midpoint = (parsed[0][2] + parsed[-1][3]) / 2
        chosen = max(
            parsed,
            key=lambda item: (
                _overlaps(item[2], item[3], priority_spans),
                item[1].get("score") or 0,
                item[1].get("quality") in (None, "ok"),
                -abs(((item[2] + item[3]) / 2) - midpoint),
                -item[2],
            ),
        )
        return [chosen[1]]

    selected_positions = {0, len(parsed) - 1}
    total_start = parsed[0][2]
    total_end = parsed[-1][3]
    bucket_width = max((total_end - total_start) / limit, 0.001)
    for bucket in range(limit):
        bucket_start = total_start + bucket * bucket_width
        bucket_end = total_end if bucket == limit - 1 else bucket_start + bucket_width
        if any(
            bucket_start <= ((parsed[pos][2] + parsed[pos][3]) / 2) <= bucket_end and pos in selected_positions
            for pos in selected_positions
        ):
            continue
        candidates = [
            pos
            for pos in range(len(parsed))
            if pos not in selected_positions and bucket_start <= ((parsed[pos][2] + parsed[pos][3]) / 2) <= bucket_end
        ]
        if not candidates:
            candidates = [pos for pos in range(len(parsed)) if pos not in selected_positions]
        if not candidates:
            break
        center = (bucket_start + bucket_end) / 2
        best = max(
            candidates,
            key=lambda pos: (
                _overlaps(parsed[pos][2], parsed[pos][3], priority_spans),
                parsed[pos][1].get("score") or 0,
                parsed[pos][1].get("quality") in (None, "ok"),
                -abs(((parsed[pos][2] + parsed[pos][3]) / 2) - center),
                -parsed[pos][2],
            ),
        )
        selected_positions.add(best)

    if len(selected_positions) < limit:
        remaining = [pos for pos in range(len(parsed)) if pos not in selected_positions]
        remaining.sort(
            key=lambda pos: (
                -_overlaps(parsed[pos][2], parsed[pos][3], priority_spans),
                -(parsed[pos][1].get("score") or 0),
                -(parsed[pos][1].get("quality") in (None, "ok")),
                parsed[pos][2],
            )
        )
        selected_positions.update(remaining[: limit - len(selected_positions)])

    return [parsed[pos][1] for pos in sorted(selected_positions)]


def _clip_duration_sec(clip: dict[str, Any]) -> float:
    duration = _timestamp_seconds(clip.get("duration_sec"))
    if duration is not None and duration > 0:
        return round(duration, 3)

    windows = clip.get("analyze_windows")
    if isinstance(windows, list):
        ends = [
            end
            for item in windows
            if isinstance(item, dict)
            and isinstance((end := _timestamp_seconds(item.get("end_sec"))), float)
            and end > 0
        ]
        if ends:
            return round(max(ends), 3)

    timeline = clip.get("timeline")
    if isinstance(timeline, list):
        ends = []
        for item in timeline:
            if not isinstance(item, dict):
                continue
            span = _span(item)
            if span is not None:
                ends.append(span[1])
        if ends:
            return round(max(ends), 3)

    duration = _timestamp_seconds(clip.get("duration_sec"))
    return round(duration, 3) if duration is not None and duration > 0 else 0.0


def plan_candidate_limit(config: AppConfig) -> int:
    return _positive_limit(config, "max_candidate_clips", 60)


def should_shortlist_plan_clips(clips: list[dict[str, Any]], config: AppConfig) -> bool:
    limit = plan_candidate_limit(config)
    return limit > 0 and len(clips) > limit


def build_plan_shortlist_cards(clips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the compact card payload used by the first-stage candidate selector."""
    cards: list[dict[str, Any]] = []
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        moments = clip.get("key_moments")
        cards.append(
            {
                "index": clip.get("index", ""),
                "title": clip.get("title", ""),
                "summary": clip.get("summary", ""),
                "location": clip.get("location", ""),
                "duration_sec": _clip_duration_sec(clip),
                "key_moments": [item for item in moments[:3] if isinstance(item, dict)]
                if isinstance(moments, list)
                else [],
            }
        )
    return cards


def _candidate_rank(clip: dict[str, Any], position: int) -> tuple[float, float, int]:
    moments = clip.get("key_moments")
    scores = (
        [
            float(score)
            for item in moments
            if isinstance(item, dict)
            and not isinstance((score := item.get("score")), bool)
            and isinstance(score, (int, float))
            and math.isfinite(float(score))
        ]
        if isinstance(moments, list)
        else []
    )
    duration = _clip_duration_sec(clip)
    return (-max(scores, default=0.0), -duration, position)


def _fallback_shortlist(clips: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    ranked = sorted(enumerate(clips), key=lambda pair: _candidate_rank(pair[1], pair[0]))
    return [clip for _, clip in ranked[:limit]]


def resolve_plan_candidates(clips: list[dict[str, Any]], keep: Any, limit: int) -> tuple[list[dict[str, Any]], str]:
    """Resolve model-selected indices, falling back deterministically on invalid output."""
    if limit <= 0:
        return list(clips), "disabled"
    if len(clips) <= limit:
        return list(clips), "not_needed"

    indices: list[str] = []
    if isinstance(keep, list):
        for value in keep:
            if not isinstance(value, str):
                return _fallback_shortlist(clips, limit), "fallback"
            index = value.strip()
            if not index:
                return _fallback_shortlist(clips, limit), "fallback"
            indices.append(index)
    else:
        return _fallback_shortlist(clips, limit), "fallback"

    if not indices or len(indices) > limit or len(set(indices)) != len(indices):
        return _fallback_shortlist(clips, limit), "fallback"

    by_index = {str(clip.get("index", "")): clip for clip in clips if isinstance(clip, dict)}
    if any(index not in by_index for index in indices):
        return _fallback_shortlist(clips, limit), "fallback"

    selected = set(indices)
    return [clip for clip in clips if isinstance(clip, dict) and str(clip.get("index", "")) in selected], "model"


def build_plan_clips(clips: list[dict[str, Any]], config: AppConfig) -> list[dict[str, Any]]:
    """Project full analysis clips into a deterministic, bounded plan input."""
    row_limit = _positive_limit(config, "max_timeline_rows_per_clip", 8)
    moment_limit = _positive_limit(config, "max_key_moments_per_clip", 6)
    projected: list[dict[str, Any]] = []

    for clip in clips:
        if not isinstance(clip, dict):
            continue
        highlights = _cap_moments(clip.get("highlights"), moment_limit)
        key_moments = _cap_moments(clip.get("key_moments"), moment_limit)
        if not key_moments:
            key_moments = highlights

        priority_spans = [span for item in [*key_moments, *highlights] if (span := _span(item)) is not None]
        raw_timeline = clip.get("timeline")
        raw_rows = [item for item in raw_timeline if isinstance(item, dict)] if isinstance(raw_timeline, list) else []
        projected_rows = [row for item in raw_rows if (row := _project_timeline_row(item)) is not None]
        timeline = _select_timeline_rows(projected_rows, row_limit, priority_spans)

        item: dict[str, Any] = {
            "index": clip.get("index", ""),
            "title": clip.get("title", ""),
            "summary": clip.get("summary", ""),
            "location": clip.get("location", ""),
            "mood": clip.get("mood", ""),
            "suggested_use": clip.get("suggested_use", ""),
            "duration_sec": _clip_duration_sec(clip),
            "capture_time": clip.get("capture_time", ""),
            "voiceover": clip.get("voiceover", ""),
            "highlights": highlights,
            "key_moments": key_moments,
            "timeline": timeline,
            "source_stem": clip.get("source_stem", ""),
            "match_stem": clip.get("match_stem", ""),
            "segment_offset_sec": clip.get("segment_offset_sec", 0.0),
        }
        if clip.get("gps") is not None:
            item["gps"] = clip["gps"]
        projected.append(item)
    return projected

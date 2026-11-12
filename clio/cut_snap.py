"""Snap generated plan cut points to visual and speech boundaries."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from clio.cut import _to_seconds, parse_time_range

SNAP_MAX_DISTANCE_SEC = 1.5
MIN_SNAP_DURATION_SEC = 0.5


@dataclass(frozen=True)
class SnapBoundaries:
    """Candidate local timestamps for one source clip."""

    shots: tuple[float, ...] = ()
    sentence_ends: tuple[float, ...] = ()


@dataclass(frozen=True)
class SnappedRange:
    start: float
    end: float
    start_snapped: bool = False
    end_snapped: bool = False


def _finite_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        seconds = float(_to_seconds(str(value))) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _nearest_boundary(
    value: float,
    candidates: Iterable[float],
    *,
    max_distance_sec: float,
    preferred: frozenset[float],
) -> float | None:
    best: tuple[float, int, float] | None = None
    for candidate in candidates:
        distance = abs(candidate - value)
        if distance > max_distance_sec:
            continue
        key = (distance, 0 if candidate in preferred else 1, candidate)
        if best is None or key < best:
            best = key
    return None if best is None else best[2]


def _unique_non_negative(values: Iterable[float]) -> frozenset[float]:
    return frozenset(value for value in values if math.isfinite(value) and value >= 0)


def snap_range(
    start: float,
    end: float,
    boundaries: SnapBoundaries,
    *,
    max_distance_sec: float = SNAP_MAX_DISTANCE_SEC,
    min_duration_sec: float = MIN_SNAP_DURATION_SEC,
) -> SnappedRange:
    """Move range endpoints to nearby boundaries without creating a short clip."""
    shots = _unique_non_negative(boundaries.shots)
    sentence_ends = _unique_non_negative(boundaries.sentence_ends)
    start_candidates = shots | sentence_ends
    end_candidates = shots | sentence_ends
    snapped_start = _nearest_boundary(
        start,
        start_candidates,
        max_distance_sec=max_distance_sec,
        preferred=shots,
    )
    snapped_end = _nearest_boundary(
        end,
        end_candidates,
        max_distance_sec=max_distance_sec,
        preferred=sentence_ends,
    )
    result_start = start if snapped_start is None else snapped_start
    result_end = end if snapped_end is None else snapped_end
    if result_end - result_start < min_duration_sec:
        return SnappedRange(start=start, end=end)
    return SnappedRange(
        start=result_start,
        end=result_end,
        start_snapped=abs(result_start - start) > 1e-9,
        end_snapped=abs(result_end - end) > 1e-9,
    )


def _format_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    prefix = f"{hours:02d}:" if hours else ""
    suffix = f".{milliseconds:03d}".rstrip("0") if milliseconds else ""
    return f"{prefix}{minutes:02d}:{whole_seconds:02d}{suffix}"


def _normalize_index(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(int(raw))
    except (TypeError, ValueError):
        return raw.casefold()


def _timeline_boundaries(clip: dict[str, Any]) -> frozenset[float]:
    boundaries: list[float] = []
    timeline = clip.get("timeline")
    if not isinstance(timeline, list):
        return frozenset()
    for item in timeline:
        if not isinstance(item, dict):
            continue
        for field in ("start", "end"):
            seconds = _finite_seconds(item.get(field))
            if seconds is not None:
                boundaries.append(seconds)
    return _unique_non_negative(boundaries)


def _sentence_ends(clip: dict[str, Any], transcript: dict[str, Any] | None) -> frozenset[float]:
    if not isinstance(transcript, dict):
        return frozenset()
    raw_segments = transcript.get("segments")
    if not isinstance(raw_segments, list):
        return frozenset()
    try:
        offset = float(clip.get("segment_offset_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        offset = 0.0
    endings: list[float] = []
    for segment in raw_segments:
        if not isinstance(segment, dict) or not str(segment.get("text") or "").strip():
            continue
        end = _finite_seconds(segment.get("end"))
        if end is not None and end - offset >= 0:
            endings.append(end - offset)
    return _unique_non_negative(endings)


def build_boundaries(
    clips: Iterable[dict[str, Any]],
    transcripts_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, SnapBoundaries]:
    """Build local shot/sentence boundaries keyed by plan source index."""
    transcripts = transcripts_map or {}
    result: dict[str, SnapBoundaries] = {}
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        key = _normalize_index(clip.get("index"))
        if not key:
            continue
        match_stem = str(clip.get("match_stem") or clip.get("source_stem") or "").strip().casefold()
        transcript = transcripts.get(match_stem) if match_stem else None
        result[key] = SnapBoundaries(
            shots=tuple(sorted(_timeline_boundaries(clip))),
            sentence_ends=tuple(sorted(_sentence_ends(clip, transcript))),
        )
    return result


def snap_plan_ranges(
    plan: dict[str, Any],
    clips: Iterable[dict[str, Any]],
    transcripts_map: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Snap ``use_timeline`` ranges in a generated plan and return changed segments."""
    sequence = plan.get("sequence")
    if not isinstance(sequence, list):
        return 0
    boundaries = build_boundaries(clips, transcripts_map)
    changed = 0
    for segment in sequence:
        if not isinstance(segment, dict):
            continue
        boundaries_for_clip = boundaries.get(_normalize_index(segment.get("index")))
        timeline = str(segment.get("use_timeline") or "").strip()
        if boundaries_for_clip is None or not timeline:
            continue
        try:
            start, end = parse_time_range(timeline)
        except ValueError:
            continue
        snapped = snap_range(start, end, boundaries_for_clip)
        if not (snapped.start_snapped or snapped.end_snapped):
            continue
        segment["use_timeline"] = f"{_format_time(snapped.start)}-{_format_time(snapped.end)}"
        changed += 1
    return changed

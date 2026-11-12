"""Export/cut readiness checks for Plan (error vs warning tiers)."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTS
from clio.cut import parse_time_range
from clio.i18n import t
from clio.identity import load_identity
from clio.plan_model import Plan, PlanIssue
from clio.tasks._helpers import _matches_selected_artifact, _matches_selected_stem, _project_selection_stems
from clio.utils import format_index, get_duration_sec, resolve_binary
from clio.vmeta import VideoMeta


@dataclass
class ReadinessResult:
    errors: list[PlanIssue] = field(default_factory=list)
    warnings: list[PlanIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


def expand_index_keys(raw: Any, *, index_width: int = 3) -> set[str]:
    """All membership forms for a plan/media index (raw, int, zero-padded).

    Plan sequence uses format_index → "001"; texts often store index as int → "1".
    Exact string equality between those forms produced false index_missing errors.
    """
    if raw is None:
        return set()
    s = str(raw).strip()
    if not s:
        return set()
    keys = {s}
    if s.isdigit():
        n = int(s)
        keys.add(str(n))
        width = max(1, int(index_width or 3))
        keys.add(format_index(n, width))
        # Also admit common pad widths so "01" matches "001" / "1"
        for w in (2, 3, 4):
            keys.add(format_index(n, w))
    return keys


def index_in_set(idx: str, candidates: set[str] | None, *, index_width: int = 3) -> bool:
    if not candidates:
        return False
    return bool(expand_index_keys(idx, index_width=index_width) & candidates)


def _index_width_from_cfg(cfg: Any) -> int:
    naming = getattr(cfg, "naming", None)
    width = getattr(naming, "index_width", 3) if naming is not None else 3
    try:
        return max(1, int(width or 3))
    except (TypeError, ValueError):
        return 3


def _positive_duration(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and duration > 0 else None


def _duration_for_index(
    durations: Mapping[str, float] | None,
    index: str,
    *,
    index_width: int,
) -> float | None:
    if not durations:
        return None
    for key in expand_index_keys(index, index_width=index_width):
        duration = _positive_duration(durations.get(key))
        if duration is not None:
            return duration
    return None


def _normalized_media_key(index: str) -> str:
    value = index.strip().lower()
    return str(int(value)) if value.isdigit() else value


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return None
    if any(not math.isfinite(part) or part < 0 for part in values):
        return None
    if len(values) == 3:
        hours, minutes, seconds = values
        if minutes >= 60 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds
    if len(values) == 2:
        minutes, seconds = values
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    return values[0]


def _merge_timeline_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 0.05:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def _range_covered(intervals: list[tuple[float, float]], start: float, end: float) -> bool:
    cursor = start
    for interval_start, interval_end in intervals:
        if interval_end <= cursor + 0.05:
            continue
        if interval_start > cursor + 0.05:
            return False
        cursor = max(cursor, interval_end)
        if cursor >= end - 0.05:
            return True
    return False


def _largest_timeline_gap(intervals: list[tuple[float, float]]) -> float:
    return max(
        (start - previous_end for (_, previous_end), (start, _) in zip(intervals, intervals[1:], strict=False)),
        default=0.0,
    )


def collect_project_analysis_timelines(cfg: Any) -> dict[str, list[tuple[float, float]]]:
    """Return analyzed timeline coverage keyed by normalized media index."""
    texts = getattr(cfg, "texts_dir", None)
    if not isinstance(texts, Path) or not texts.is_dir():
        return {}

    selection = _project_selection_stems(cfg)
    collected: dict[str, list[tuple[float, float]]] = {}
    try:
        candidates = sorted(texts.glob("*.json"))
    except OSError:
        return {}

    for path in candidates:
        if selection is not None and not _matches_selected_artifact(path, selection):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        raw_index = data.get("index")
        if raw_index is None:
            identity = load_identity(data)
            raw_index = identity.index if identity is not None else None
        if raw_index is None or not str(raw_index).strip():
            continue

        key = _normalized_media_key(str(raw_index))
        parsed: list[tuple[float, float]] = []
        timeline = data.get("timeline")
        if isinstance(timeline, list):
            for entry in timeline:
                if not isinstance(entry, dict):
                    continue
                start = _timestamp_seconds(entry.get("start"))
                end = _timestamp_seconds(entry.get("end"))
                if start is not None and end is not None and end > start:
                    parsed.append((start, end))
                elif start is not None:
                    parsed.append((start, start))
        collected.setdefault(key, []).extend(parsed)

    return {key: _merge_timeline_intervals(intervals) for key, intervals in collected.items()}


def check_plan_export_readiness(
    plan: Plan,
    *,
    known_indices: set[str] | None = None,
    offline_indices: set[str] | None = None,
    media_durations: Mapping[str, float] | None = None,
    analysis_timelines: Mapping[str, list[tuple[float, float]]] | None = None,
    source: str = "compressed",
    index_width: int = 3,
) -> ReadinessResult:
    source = str(source or "compressed").strip().lower()
    result = ReadinessResult()
    if not plan.sequence:
        result.errors.append(
            PlanIssue(level="error", code="sequence_empty", message=t("plan.readiness.sequence_empty"))
        )
        return result

    try:
        tes = float(plan.total_estimated_sec)
    except (TypeError, ValueError):
        tes = 180
    if tes < 30:
        result.warnings.append(
            PlanIssue(
                level="warning", code="duration_short", message=t("plan.readiness.duration_short", {"seconds": tes})
            )
        )
    if tes > 1800:
        result.warnings.append(
            PlanIssue(
                level="warning", code="duration_long", message=t("plan.readiness.duration_long", {"seconds": tes})
            )
        )

    # Distinguish "not scanned" (None) from "scanned and found nothing" (empty
    # set). An empty discovery means every referenced index is missing media; a
    # None discovery means we simply don't know yet and must not guess.
    known: set[str] | None
    if known_indices is None:
        known = None
    else:
        known = set()
        for x in known_indices:
            known.update(expand_index_keys(x, index_width=index_width))

    offline: set[str] = set()
    if offline_indices:
        for x in offline_indices:
            offline.update(expand_index_keys(x, index_width=index_width))

    ranges: dict[str, list[tuple[int, float, float]]] = {}
    selected_seconds = 0.0
    valid_selected_ranges = 0
    quality_checks = analysis_timelines is not None
    analysis_by_key = (
        {_normalized_media_key(key): intervals for key, intervals in analysis_timelines.items()}
        if analysis_timelines is not None
        else {}
    )
    reported_source_warnings: set[tuple[str, str]] = set()

    def append_source_warning(issue_code: str, media_key: str, issue: PlanIssue) -> None:
        marker = (issue_code, media_key)
        if marker in reported_source_warnings:
            return
        reported_source_warnings.add(marker)
        result.warnings.append(issue)

    for i, seg in enumerate(plan.sequence):
        idx = (seg.index or "").strip()
        if not idx:
            result.errors.append(
                PlanIssue(
                    level="error",
                    code="index_empty",
                    message=t("plan.readiness.index_empty", {"index": i + 1}),
                    segment_index=i,
                )
            )
        elif known is not None and not index_in_set(idx, known, index_width=index_width):
            result.errors.append(
                PlanIssue(
                    level="error",
                    code="index_missing",
                    message=t("plan.readiness.index_missing", {"index": i + 1, "media": idx}),
                    segment_index=i,
                )
            )
        elif index_in_set(idx, offline, index_width=index_width):
            offline_level = "error" if source == "original" else "warning"
            issue = PlanIssue(
                level=offline_level,
                code="video_offline",
                message=t("plan.readiness.video_offline", {"index": i + 1, "media": idx}),
                segment_index=i,
            )
            if offline_level == "error":
                result.errors.append(issue)
            else:
                result.warnings.append(issue)

        tl = (seg.use_timeline or "").strip()
        if not tl:
            result.warnings.append(
                PlanIssue(
                    level="warning",
                    code="timeline_empty",
                    message=t("plan.readiness.timeline_empty", {"index": i + 1}),
                    segment_index=i,
                )
            )
        else:
            try:
                start, end = parse_time_range(tl)
            except ValueError as e:
                result.errors.append(
                    PlanIssue(
                        level="error",
                        code="timeline_invalid",
                        message=t("plan.readiness.timeline_invalid", {"index": i + 1, "detail": e}),
                        segment_index=i,
                    )
                )
            else:
                selected_seconds += max(0.0, end - start)
                valid_selected_ranges += 1
                duration = _duration_for_index(media_durations, idx, index_width=index_width)
                if duration is not None and end > duration + 0.05:
                    result.errors.append(
                        PlanIssue(
                            level="error",
                            code="timeline_out_of_bounds",
                            message=t(
                                "plan.readiness.timeline_out_of_bounds",
                                {"index": i + 1, "end": end, "duration": duration},
                            ),
                            segment_index=i,
                        )
                    )

                media_key = _normalized_media_key(idx)
                if quality_checks and media_key:
                    intervals = analysis_by_key.get(media_key)
                    if intervals is None:
                        append_source_warning(
                            "analysis_timeline_missing",
                            media_key,
                            PlanIssue(
                                level="warning",
                                code="analysis_timeline_missing",
                                message=t(
                                    "plan.readiness.analysis_timeline_missing",
                                    {"index": i + 1, "media": idx},
                                ),
                                segment_index=i,
                            ),
                        )
                    elif not intervals:
                        append_source_warning(
                            "analysis_timeline_empty",
                            media_key,
                            PlanIssue(
                                level="warning",
                                code="analysis_timeline_empty",
                                message=t(
                                    "plan.readiness.analysis_timeline_empty",
                                    {"index": i + 1, "media": idx},
                                ),
                                segment_index=i,
                            ),
                        )
                    else:
                        analysis_end = max(interval_end for _, interval_end in intervals)
                        if duration is not None and analysis_end > duration + 0.05:
                            append_source_warning(
                                "analysis_timeline_out_of_bounds",
                                media_key,
                                PlanIssue(
                                    level="warning",
                                    code="analysis_timeline_out_of_bounds",
                                    message=t(
                                        "plan.readiness.analysis_timeline_out_of_bounds",
                                        {"media": idx, "end": round(analysis_end, 2), "duration": duration},
                                    ),
                                    segment_index=i,
                                ),
                            )
                        gap = _largest_timeline_gap(intervals)
                        if gap > 60:
                            append_source_warning(
                                "analysis_timeline_gap",
                                media_key,
                                PlanIssue(
                                    level="warning",
                                    code="analysis_timeline_gap",
                                    message=t(
                                        "plan.readiness.analysis_timeline_gap",
                                        {"media": idx, "gap": round(gap, 2)},
                                    ),
                                    segment_index=i,
                                ),
                            )
                        if not _range_covered(intervals, start, end):
                            result.warnings.append(
                                PlanIssue(
                                    level="warning",
                                    code="timeline_outside_analysis",
                                    message=t(
                                        "plan.readiness.timeline_outside_analysis",
                                        {
                                            "index": i + 1,
                                            "media": idx,
                                            "start": round(start, 2),
                                            "end": round(end, 2),
                                        },
                                    ),
                                    segment_index=i,
                                )
                            )

                for previous_position, previous_start, previous_end in ranges.get(media_key, []):
                    if start < previous_end - 0.05 and end > previous_start + 0.05:
                        result.warnings.append(
                            PlanIssue(
                                level="warning",
                                code="timeline_overlap",
                                message=t(
                                    "plan.readiness.timeline_overlap",
                                    {"index": i + 1, "previous": previous_position + 1, "media": idx},
                                ),
                                segment_index=i,
                            )
                        )
                ranges.setdefault(media_key, []).append((i, start, end))

        if not (seg.title or "").strip():
            result.warnings.append(
                PlanIssue(
                    level="warning",
                    code="title_empty",
                    message=t("plan.readiness.title_empty", {"index": i + 1}),
                    segment_index=i,
                )
            )
        if not (seg.reason or "").strip():
            result.warnings.append(
                PlanIssue(
                    level="warning",
                    code="reason_empty",
                    message=t("plan.readiness.reason_empty", {"index": i + 1}),
                    segment_index=i,
                )
            )
        if quality_checks and not (seg.voiceover_hint or "").strip():
            result.warnings.append(
                PlanIssue(
                    level="warning",
                    code="voiceover_hint_empty",
                    message=t("plan.readiness.voiceover_hint_empty", {"index": i + 1}),
                    segment_index=i,
                )
            )

    if quality_checks and valid_selected_ranges:
        difference = abs(tes - selected_seconds)
        allowed_difference = max(10.0, selected_seconds * 0.25)
        if difference > allowed_difference:
            result.warnings.append(
                PlanIssue(
                    level="warning",
                    code="duration_mismatch",
                    message=t(
                        "plan.readiness.duration_mismatch",
                        {"estimated": tes, "selected": round(selected_seconds, 2)},
                    ),
                )
            )

    return result


def _register_stem_indices(
    stem_to_indices: dict[str, set[str]],
    stem: str | None,
    idx_keys: set[str],
) -> None:
    if not stem or not idx_keys:
        return
    key = stem.strip().lower()
    if not key:
        return
    stem_to_indices.setdefault(key, set()).update(idx_keys)


def collect_project_indices(cfg: Any) -> tuple[set[str], set[str]]:
    """Build (known_indices, offline_indices) from compressed + texts + videos.json.

    known/offline sets include expanded index key forms ("1", "001", …).
    offline_indices are plan indexes whose original media is listed in videos.json
    but currently missing on disk (mapped via texts media_identity / source_file
    or compressed filename stems).
    """
    width = _index_width_from_cfg(cfg)
    selection = _project_selection_stems(cfg)
    known: set[str] = set()
    offline: set[str] = set()
    stem_to_indices: dict[str, set[str]] = {}

    comp = getattr(cfg, "compressed_dir", None)
    if isinstance(comp, Path) and comp.is_dir():
        try:
            for p in comp.iterdir():
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                    if selection is not None and not _matches_selected_stem(p, selection, artifact=True):
                        continue
                    stem = p.stem
                    idx = stem.split("_", 1)[0]
                    if idx:
                        keys = expand_index_keys(idx, index_width=width)
                        known.update(keys)
                        # Map original-like suffix after index prefix: 001_GL010683 → GL010683
                        rest = stem.split("_", 1)[1] if "_" in stem else ""
                        if rest:
                            # Drop legacy _segNN tail for stem matching
                            base = rest
                            for marker in ("_seg", "_part", "_pt", "_chunk"):
                                pos = base.lower().rfind(marker)
                                if pos > 0 and base[pos + len(marker) :].isdigit():
                                    base = base[:pos]
                                    break
                            _register_stem_indices(stem_to_indices, base, keys)
        except OSError:
            pass

    texts = getattr(cfg, "texts_dir", None)
    if isinstance(texts, Path) and texts.is_dir():
        try:
            for jf in texts.glob("*.json"):
                if selection is not None and not _matches_selected_artifact(jf, selection):
                    continue
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                raw_idx = data.get("index")
                if raw_idx is None:
                    continue
                keys = expand_index_keys(raw_idx, index_width=width)
                known.update(keys)
                identity = load_identity(data)
                if identity is not None:
                    _register_stem_indices(stem_to_indices, identity.original_stem, keys)
                    _register_stem_indices(stem_to_indices, identity.compressed_stem, keys)
                    if identity.index:
                        known.update(expand_index_keys(identity.index, index_width=width))
                source = data.get("source_file") or data.get("compressed_file")
                if source:
                    _register_stem_indices(stem_to_indices, Path(str(source)).stem, keys)
        except OSError:
            pass

    project_dir = getattr(cfg, "project_dir", None)
    if project_dir is not None:
        try:
            from clio.tasks._video_loader import load_selected_videos

            for p in load_selected_videos(project_dir):
                if p.is_file():
                    continue
                # Offline: map path stem → plan indices
                for stem_candidate in (p.stem, Path(str(p)).stem):
                    mapped = stem_to_indices.get(stem_candidate.lower())
                    if mapped:
                        offline.update(mapped)
                        break
        except OSError:
            pass

    return known, offline


def collect_project_media_durations(cfg: Any) -> dict[str, float]:
    """Return compressed clip durations keyed by plan index.

    Plan timelines are expressed in compressed-clip time for both cut sources;
    ``source=original`` only changes how an offline original is reported and
    how cutting resolves the file. Prefer .vmeta because it is cheap, then
    probe only when older artifacts have no trustworthy target duration.
    """
    compressed_dir = getattr(cfg, "compressed_dir", None)
    if not isinstance(compressed_dir, Path) or not compressed_dir.is_dir():
        return {}

    selection = _project_selection_stems(cfg)
    durations: dict[str, float] = {}
    ffprobe = ""
    try:
        for path in compressed_dir.iterdir():
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
                continue
            if selection is not None and not _matches_selected_stem(path, selection, artifact=True):
                continue
            index = path.stem.split("_", 1)[0].strip()
            if not index:
                continue

            meta = VideoMeta.read(path)
            duration = _positive_duration(meta.target_duration_sec) if meta is not None else None
            if duration is None:
                if not ffprobe:
                    try:
                        ffprobe = resolve_binary(getattr(getattr(cfg, "paths", None), "ffprobe", ""), "ffprobe")
                    except (FileNotFoundError, OSError):
                        break
                try:
                    duration = _positive_duration(get_duration_sec(path, ffprobe))
                except (FileNotFoundError, OSError, RuntimeError, ValueError):
                    duration = None
            if duration is not None:
                durations[index] = max(duration, durations.get(index, 0.0))
    except OSError:
        pass
    return durations


def readiness_block_payload(result: ReadinessResult, *, force: bool) -> dict[str, Any] | None:
    """If export/cut should be blocked, return JSON body (caller sends 400). Else None."""
    if result.errors:
        return {
            "ok": False,
            "error": result.errors[0].message,
            "issues": result.to_dict(),
        }
    if result.warnings and not force:
        return {
            "ok": False,
            "error": t("plan.readiness.needs_force"),
            "issues": result.to_dict(),
            "needs_force": True,
        }
    return None

"""Logical analyze windows: geometry, time shift, merge, and legacy slice helpers.

Physical video split is deprecated. Long clips are analyzed as sliding
windows inside analyze only; see
docs/superpowers/specs/2026-07-18-remove-physical-split-design.md.
"""

from __future__ import annotations

import contextlib
import copy
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import pairwise
from math import ceil, isfinite
from pathlib import Path
from typing import Any

from clio.i18n import t
from clio.utils import get_duration_sec, write_json_atomic

MAX_ANALYZE_WINDOWS = 1000


@dataclass(frozen=True)
class AnalyzeWindow:
    index: int
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def build_analyze_windows(
    duration_sec: float,
    window_max_min: int,
    overlap_sec: int,
) -> list[AnalyzeWindow]:
    if duration_sec <= 0:
        return [AnalyzeWindow(0, 0.0, 0.0)]
    window_max = max(1, int(window_max_min)) * 60
    overlap = max(0, int(overlap_sec))
    if overlap >= window_max:
        overlap = max(0, window_max - 1)
    if duration_sec <= window_max:
        return [AnalyzeWindow(0, 0.0, float(duration_sec))]
    step = window_max - overlap
    window_count = 1 + ceil((duration_sec - window_max) / step)
    if window_count > MAX_ANALYZE_WINDOWS:
        raise ValueError(
            t(
                "analyze.too_many_windows",
                {"count": window_count, "max": MAX_ANALYZE_WINDOWS},
            )
        )
    windows: list[AnalyzeWindow] = []
    start = 0.0
    i = 0
    while start < duration_sec:
        end = min(start + window_max, duration_sec)
        windows.append(AnalyzeWindow(i, float(start), float(end)))
        if end >= duration_sec:
            break
        start += step
        i += 1
    return windows


_TIME_KEYS = ("start", "end", "time", "t", "timestamp", "cover_timestamp")


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


def _format_ts(sec: float, original: Any) -> Any:
    if isinstance(original, bool):
        return sec
    if isinstance(original, int) and not isinstance(original, bool):
        return int(round(sec))
    if isinstance(original, float):
        return float(sec)
    if isinstance(original, str) and ":" in original:
        parts = original.strip().split(":")
        precision = len(parts[-1].partition(".")[2])
        scale = 10**precision
        shifted_units = int(round(sec * scale))
        hours, remainder = divmod(shifted_units, 3600 * scale)
        minutes, remainder = divmod(remainder, 60 * scale)
        seconds, fraction = divmod(remainder, scale)
        seconds_text = f"{seconds:02d}"
        if precision:
            seconds_text += f".{fraction:0{precision}d}"
        if len(parts) == 3 or hours:
            return f"{hours:02d}:{minutes:02d}:{seconds_text}"
        total_minutes = shifted_units // (60 * scale)
        return f"{total_minutes:02d}:{seconds_text}"
    return sec


def shift_analysis_times(analysis: dict, offset_sec: float) -> dict:
    data = copy.deepcopy(analysis)
    if not offset_sec:
        return data

    def shift_obj(obj: Any) -> None:
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k in _TIME_KEYS:
                    parsed = _parse_ts(v)
                    if parsed is not None:
                        obj[k] = _format_ts(parsed + offset_sec, v)
                else:
                    shift_obj(v)
        elif isinstance(obj, list):
            for item in obj:
                shift_obj(item)

    shift_obj(data)
    return data


def _timeline_list(analysis: dict) -> list:
    tl = analysis.get("timeline")
    return list(tl) if isinstance(tl, list) else []


@dataclass(frozen=True)
class TimelineCoverage:
    rows: int
    coverage_ratio: float
    max_gap_sec: float


def measure_timeline_coverage(analysis: dict, duration_sec: float) -> TimelineCoverage:
    """Measure valid timeline coverage without changing the analysis output."""
    timeline = _timeline_list(analysis)
    rows = len(timeline)
    if not isfinite(duration_sec) or duration_sec <= 0:
        return TimelineCoverage(rows=rows, coverage_ratio=0.0, max_gap_sec=0.0)

    intervals: list[tuple[float, float]] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        start = _parse_ts(item.get("start"))
        end = _parse_ts(item.get("end"))
        if start is None or end is None or not isfinite(start) or not isfinite(end):
            continue
        clipped_start = max(0.0, start)
        clipped_end = min(duration_sec, end)
        if clipped_end > clipped_start:
            intervals.append((clipped_start, clipped_end))

    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    covered_sec = sum(end - start for start, end in merged)
    gaps: list[float] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor + 0.05:
            gaps.append(start - cursor)
        cursor = max(cursor, end)
    if cursor < duration_sec - 0.05:
        gaps.append(duration_sec - cursor)
    return TimelineCoverage(
        rows=rows,
        coverage_ratio=min(1.0, covered_sec / duration_sec),
        max_gap_sec=max(gaps, default=duration_sec),
    )


def _item_start(item: dict) -> float:
    for k in ("start", "time", "t", "timestamp"):
        if k in item:
            p = _parse_ts(item[k])
            if p is not None:
                return p
    return 0.0


def _item_text(item: dict) -> str:
    for k in ("text", "description", "title", "event"):
        if isinstance(item.get(k), str):
            return item[k]
    return ""


def _normalized_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold())


def _similar_item_text(left: dict, right: dict) -> bool:
    left_text = _normalized_text(_item_text(left))
    right_text = _normalized_text(_item_text(right))
    if not left_text or not right_text:
        return False
    shorter, longer = sorted((left_text, right_text), key=len)
    if len(shorter) >= 4 and shorter in longer:
        return True
    return SequenceMatcher(None, left_text, right_text).ratio() >= 0.72


def _item_score(item: dict) -> int:
    value = item.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(1, min(5, int(round(value))))


def _item_completeness(item: dict) -> int:
    fields = ("start", "end", "description", "title", "why", "score", "camera", "quality", "tags")
    return sum(1 for field in fields if item.get(field) not in (None, "", []))


def _merged_tags(left: dict, right: dict) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for source in (left, right):
        tags = source.get("tags")
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if not isinstance(tag, str):
                continue
            key = tag.casefold()
            if tag and key not in seen:
                merged.append(tag)
                seen.add(key)
    return merged


def _merge_duplicate_item(left: dict, right: dict) -> dict:
    left_rank = (_item_completeness(left), _item_score(left), len(_item_text(left)))
    right_rank = (_item_completeness(right), _item_score(right), len(_item_text(right)))
    chosen, other = (right, left) if right_rank > left_rank else (left, right)
    merged = copy.deepcopy(chosen)
    tags = _merged_tags(chosen, other)
    if tags:
        merged["tags"] = tags
    return merged


def merge_window_analyses(
    windows: list[tuple[AnalyzeWindow, dict]],
    overlap_sec: int,
) -> dict:
    if not windows:
        return {"title": "", "summary": "", "timeline": []}
    if len(windows) == 1:
        out = copy.deepcopy(windows[0][1])
        w = windows[0][0]
        out["analyze_windows"] = [
            {
                "i": w.index,
                "start_sec": w.start_sec,
                "end_sec": w.end_sec,
                "overlap_sec": overlap_sec,
                "status": "ok",
            }
        ]
        return out

    base = copy.deepcopy(windows[0][1])
    summaries: list[str] = []
    highlights: list[Any] = []
    locations: list[str] = []
    timeline: list[dict] = []
    key_moments: list[dict] = []

    for _w, raw in windows:
        if isinstance(raw.get("summary"), str) and raw["summary"]:
            summaries.append(raw["summary"])
        if isinstance(raw.get("highlights"), list):
            highlights.extend(raw["highlights"])
        loc = raw.get("location")
        if isinstance(loc, str) and loc and loc != "未知":
            locations.append(loc)
        for item in _timeline_list(raw):
            if isinstance(item, dict):
                timeline.append(copy.deepcopy(item))
        raw_moments = raw.get("key_moments")
        if isinstance(raw_moments, list):
            key_moments.extend(copy.deepcopy(item) for item in raw_moments if isinstance(item, dict))

    timeline.sort(key=_item_start)

    # Only dedupe inside adjacent-window overlap bands (spec), not globally.
    zones: list[tuple[float, float]] = []
    ordered_ws = [w for w, _ in windows]
    for a, b in pairwise(ordered_ws):
        lo = max(a.start_sec, b.start_sec)
        hi = min(a.end_sec, b.end_sec)
        if hi > lo:
            zones.append((lo, hi))

    def _in_overlap(t: float) -> bool:
        return any(lo <= t <= hi for lo, hi in zones)

    deduped: list[dict] = []
    for item in timeline:
        t_item = _item_start(item)
        duplicate_index = None
        for index in range(len(deduped) - 1, -1, -1):
            previous = deduped[index]
            t_previous = _item_start(previous)
            if t_item - t_previous > 5:
                break
            if _in_overlap(t_item) and _in_overlap(t_previous) and _similar_item_text(item, previous):
                duplicate_index = index
                break
        if duplicate_index is not None:
            deduped[duplicate_index] = _merge_duplicate_item(deduped[duplicate_index], item)
            continue
        deduped.append(item)

    key_moments.sort(key=_item_start)
    deduped_moments: list[dict] = []
    for moment in key_moments:
        t_item = _item_start(moment)
        duplicate_index = None
        for index in range(len(deduped_moments) - 1, -1, -1):
            previous = deduped_moments[index]
            t_previous = _item_start(previous)
            if t_item - t_previous > 5:
                break
            if _in_overlap(t_item) and _in_overlap(t_previous) and _similar_item_text(moment, previous):
                duplicate_index = index
                break
        if duplicate_index is not None:
            deduped_moments[duplicate_index] = _merge_duplicate_item(deduped_moments[duplicate_index], moment)
            continue
        deduped_moments.append(moment)

    base["timeline"] = deduped
    base["key_moments"] = deduped_moments
    base["summary"] = "\n".join(summaries)
    seen: set[Any] = set()
    uniq_h: list[Any] = []
    for h in highlights:
        key = h if not isinstance(h, dict) else str(h)
        if key in seen:
            continue
        seen.add(key)
        uniq_h.append(h)
    base["highlights"] = uniq_h
    if locations:
        uniq_loc = list(dict.fromkeys(locations))
        base["location"] = uniq_loc[0] if len(uniq_loc) == 1 else " / ".join(uniq_loc)
    base["analyze_windows"] = [
        {
            "i": w.index,
            "start_sec": w.start_sec,
            "end_sec": w.end_sec,
            "overlap_sec": overlap_sec,
            "status": "ok",
        }
        for w, _ in windows
    ]
    return base


WINDOW_CACHE_VERSION = 2


def window_cache_dir(output_dir: Path, stem: str) -> Path:
    return output_dir / ".analyze_windows_cache" / stem


def _window_cache_path(cache_dir: Path, window: AnalyzeWindow) -> Path:
    return cache_dir / f"w{window.index:03d}_{int(window.start_sec)}-{int(window.end_sec)}.json"


def save_window_cache(
    cache_dir: Path,
    *,
    window: AnalyzeWindow,
    lineage: str,
    source_size: int,
    source_mtime_ns: int,
    analysis: dict,
) -> Path:
    """Persist one completed window so a failed run can resume without re-paying."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _window_cache_path(cache_dir, window)
    write_json_atomic(
        path,
        {
            "version": WINDOW_CACHE_VERSION,
            "lineage": lineage,
            "window": {"index": window.index, "start_sec": window.start_sec, "end_sec": window.end_sec},
            "source_size": source_size,
            "source_mtime_ns": source_mtime_ns,
            "analysis": analysis,
        },
    )
    return path


def load_window_cache(
    cache_dir: Path,
    *,
    window: AnalyzeWindow,
    lineage: str,
    source_size: int,
    source_mtime_ns: int,
) -> dict | None:
    path = _window_cache_path(cache_dir, window)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("version") != WINDOW_CACHE_VERSION:
        return None
    if data.get("lineage") != lineage:
        return None
    if data.get("source_size") != source_size or data.get("source_mtime_ns") != source_mtime_ns:
        return None
    cached_window = data.get("window")
    if not isinstance(cached_window, dict):
        return None
    if (
        cached_window.get("index") != window.index
        or cached_window.get("start_sec") != window.start_sec
        or cached_window.get("end_sec") != window.end_sec
    ):
        return None
    analysis = data.get("analysis")
    return analysis if isinstance(analysis, dict) else None


def cleanup_window_cache_dir(cache_dir: Path) -> None:
    """Drop cached windows after the whole clip is committed to texts/."""
    shutil.rmtree(cache_dir, ignore_errors=True)


def slice_window_video(
    *,
    source: Path,
    window: AnalyzeWindow,
    dest_dir: Path,
    ffmpeg: str,
    ffprobe: str | None = None,
    run_ffmpeg: Callable[..., Any] | None = None,
    cancel_event: Any | None = None,
) -> Path:
    """Cut a temp slice from an already-compressed file for one analyze window.

    Prefer stream-copy; on failure or empty output, re-encode lightly so
    keyframe-misaligned cuts still produce a valid Gemini upload.
    """
    from clio.utils import run_ffmpeg as _default_run_ffmpeg

    runner = run_ffmpeg or _default_run_ffmpeg
    dest_dir.mkdir(parents=True, exist_ok=True)
    start = max(0.0, float(window.start_sec))
    dur = max(0.01, float(window.duration_sec))
    out = dest_dir / f"{source.stem}_w{window.index:02d}_{int(start)}-{int(start + dur)}.mp4"

    def _run(args: list[str]) -> None:
        # run_ffmpeg accepts cancel_event; custom test doubles may not.
        try:
            runner(args, ffmpeg, cancel_event=cancel_event)
        except TypeError:
            runner(args, ffmpeg)

    max_bytes = 200 * 1024 * 1024

    def _has_output() -> bool:
        return out.is_file() and out.stat().st_size > 0

    def _valid_output(*, check_duration: bool) -> bool:
        if not _has_output() or out.stat().st_size > max_bytes:
            return False
        if not check_duration or ffprobe is None:
            return True
        try:
            actual_duration = get_duration_sec(out, ffprobe)
        except (OSError, RuntimeError, ValueError):
            return False
        return abs(actual_duration - dur) <= 0.5

    copy_args = [
        "-ss",
        str(start),
        "-i",
        str(source),
        "-t",
        str(dur),
        "-c",
        "copy",
        "-y",
        str(out),
    ]
    try:
        _run(copy_args)
        if _valid_output(check_duration=True):
            return out
        # empty or oversized copy → fall through to re-encode / shrink
        out.unlink(missing_ok=True)
    except InterruptedError:
        # User cancel must not fall through to re-encode (C1).
        out.unlink(missing_ok=True)
        raise
    except (OSError, RuntimeError, ValueError):
        out.unlink(missing_ok=True)

    reencode_args = [
        "-ss",
        str(start),
        "-i",
        str(source),
        "-t",
        str(dur),
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "28",
        "-an",
        "-y",
        str(out),
    ]
    _run(reencode_args)
    if not _has_output():
        raise RuntimeError(t("analyze.window_slice_empty", {"name": out.name}))
    if out.stat().st_size > max_bytes:
        out.unlink(missing_ok=True)
        shrink_args = [
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(dur),
            "-vf",
            "scale=min(480\\,iw):-2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "32",
            "-an",
            "-y",
            str(out),
        ]
        _run(shrink_args)
        if not _has_output():
            raise RuntimeError(t("analyze.window_slice_empty", {"name": out.name}))
        if out.stat().st_size > max_bytes:
            raise RuntimeError(
                t(
                    "analyze.window_slice_too_large",
                    {"size": f"{out.stat().st_size / 1024 / 1024:.0f}"},
                )
            )
    if not _valid_output(check_duration=True):
        out.unlink(missing_ok=True)
        raise RuntimeError(t("analyze.window_slice_duration", {"name": out.name}))
    return out


def cleanup_analyze_windows_dir(dest_dir: Path) -> None:
    if not dest_dir.is_dir():
        return
    for p in dest_dir.glob("*_w*.mp4"):
        with contextlib.suppress(OSError):
            p.unlink(missing_ok=True)

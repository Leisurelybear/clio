"""Logical analyze windows: geometry, time shift, merge, temp slices.

Physical video split is deprecated. Long clips are analyzed as sliding
windows inside analyze only; see
docs/superpowers/specs/2026-07-18-remove-physical-split-design.md.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from math import ceil
from pathlib import Path
from typing import Any

from clio.i18n import t
from clio.utils import get_duration_sec

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
        sec_i = int(round(sec))
        m, s = divmod(sec_i, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
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

    timeline.sort(key=_item_start)

    # Only dedupe inside adjacent-window overlap bands (spec), not globally.
    zones: list[tuple[float, float]] = []
    ordered_ws = [w for w, _ in windows]
    for a, b in zip(ordered_ws, ordered_ws[1:]):
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
            if len(_item_text(item)) > len(_item_text(deduped[duplicate_index])):
                deduped[duplicate_index] = item
            continue
        deduped.append(item)

    base["timeline"] = deduped
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
        except Exception:
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
    except Exception:
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
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass

"""Scene-boundary detection and alignment for video analysis."""

from __future__ import annotations

import math
import re
import subprocess
import threading
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from clio.i18n import t
from clio.shutdown import register_process, unregister_process
from clio.utils import popen_subprocess, resolve_binary

SCENE_DETECT_VERSION = 1
SCENE_THRESHOLD = 0.35
SCENE_SAMPLE_FPS = 4.0
MIN_SCENE_GAP_SEC = 0.5
SCENE_ALIGN_TOLERANCE_SEC = 1.0
_SCENE_TIMEOUT_SEC = 120.0
_EPSILON = 1e-6
_PTS_TIME_RE = re.compile(r"\bpts_time:\s*(-?\d+(?:\.\d+)?)")


def _finite_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        parts = raw.split(":")
        try:
            if len(parts) == 2:
                value = int(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 3:
                value = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            else:
                value = float(raw)
        except ValueError:
            return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if math.isfinite(seconds) else None


def normalize_scene_boundaries(values: Iterable[Any], duration_sec: float) -> list[float]:
    """Clamp, sort, and deduplicate scene boundaries, including clip endpoints."""
    duration = _finite_seconds(duration_sec)
    if duration is None or duration <= 0:
        return []
    normalized: list[float] = [0.0, duration]
    for value in values:
        seconds = _finite_seconds(value)
        if seconds is None:
            continue
        normalized.append(max(0.0, min(duration, seconds)))
    normalized.sort()
    unique: list[float] = []
    for seconds in normalized:
        if not unique or seconds - unique[-1] > 1e-3:
            unique.append(seconds)
    unique[0] = 0.0
    unique[-1] = duration
    return unique


def parse_scene_timestamps(stderr: str, duration_sec: float) -> list[float]:
    """Extract ffmpeg showinfo timestamps from stderr."""
    return normalize_scene_boundaries(
        (match.group(1) for match in _PTS_TIME_RE.finditer(stderr)),
        duration_sec,
    )


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def detect_scene_boundaries(
    video_path: str | Path,
    duration_sec: float,
    ffmpeg: str,
    *,
    threshold: float = SCENE_THRESHOLD,
    fps: float = SCENE_SAMPLE_FPS,
    min_scene_gap_sec: float = MIN_SCENE_GAP_SEC,
    cancel_event: threading.Event | None = None,
    timeout_sec: float = _SCENE_TIMEOUT_SEC,
) -> list[float]:
    """Detect visual scene changes without making analysis depend on success."""
    duration = _finite_seconds(duration_sec)
    if duration is None or duration <= 0:
        return []
    try:
        binary = resolve_binary(ffmpeg, "ffmpeg") if ffmpeg else resolve_binary("", "ffmpeg")
    except (OSError, RuntimeError):
        return []

    filter_chain = f"fps={max(0.1, float(fps))},select='gt(scene,{max(0.0, min(1.0, float(threshold)))})',showinfo"
    cmd = [
        binary,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-i",
        str(video_path),
        "-an",
        "-vf",
        filter_chain,
        "-f",
        "null",
        "-",
    ]
    lines: list[str] = []
    try:
        process = popen_subprocess(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return []

    def _reader() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            lines.append(line)

    reader = threading.Thread(target=_reader, daemon=True)
    register_process(process)
    reader.start()
    started = time.monotonic()
    try:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(process)
                raise InterruptedError(t("utils.ffmpeg.cancelled"))
            if timeout_sec > 0 and time.monotonic() - started > timeout_sec:
                _terminate_process(process)
                return []
            try:
                process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        reader.join(timeout=5)
        if process.returncode != 0:
            return []
        boundaries = parse_scene_timestamps("".join(lines), duration)
        if min_scene_gap_sec > 0:
            filtered = [boundaries[0]]
            for seconds in boundaries[1:-1]:
                if seconds - filtered[-1] >= min_scene_gap_sec:
                    filtered.append(seconds)
            if duration - filtered[-1] < min_scene_gap_sec and len(filtered) > 1:
                filtered.pop()
            filtered.append(duration)
            boundaries = normalize_scene_boundaries(filtered, duration)
        return boundaries
    except InterruptedError:
        raise
    except (OSError, subprocess.SubprocessError, ValueError):
        _terminate_process(process)
        return []
    finally:
        unregister_process(process)


def slice_scene_boundaries(boundaries: Sequence[float], start_sec: float, end_sec: float) -> list[float]:
    """Convert global boundaries to a window-local list."""
    start = _finite_seconds(start_sec)
    end = _finite_seconds(end_sec)
    if start is None or end is None or end <= start or not boundaries:
        return []
    duration = end - start
    local = []
    for value in boundaries:
        seconds = _finite_seconds(value)
        if seconds is not None and start - _EPSILON <= seconds <= end + _EPSILON:
            local.append(max(0.0, min(duration, seconds - start)))
    return normalize_scene_boundaries(local, duration)


def scene_ranges(boundaries: Sequence[float]) -> list[tuple[float, float]]:
    values = [value for value in (_finite_seconds(boundary) for boundary in boundaries) if value is not None]
    values = sorted(set(values))
    return [(start, end) for start, end in zip(values, values[1:], strict=False) if end - start > _EPSILON]


def format_scene_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    prefix = f"{hours:02d}:" if hours else ""
    suffix = f".{milliseconds:03d}".rstrip("0") if milliseconds else ""
    return f"{prefix}{minutes:02d}:{whole_seconds:02d}{suffix}"


def _format_like(seconds: float, original: Any) -> Any:
    if isinstance(original, int) and not isinstance(original, bool):
        return int(round(seconds))
    if isinstance(original, float):
        return float(seconds)
    return format_scene_timestamp(seconds)


def _nearest_boundary(seconds: float, boundaries: Sequence[float]) -> float | None:
    if not boundaries:
        return None
    nearest = min(boundaries, key=lambda value: abs(value - seconds))
    return nearest if abs(nearest - seconds) <= SCENE_ALIGN_TOLERANCE_SEC else None


def align_shots_to_boundaries(analysis: dict[str, Any], boundaries: Sequence[float]) -> dict[str, Any]:
    """Snap model timestamps that are already close to detected scene cuts."""
    normalized = normalize_scene_boundaries(boundaries, max(boundaries, default=0.0))
    if len(normalized) < 2:
        return analysis
    timeline = analysis.get("timeline")
    if not isinstance(timeline, list):
        return analysis
    for item in timeline:
        if not isinstance(item, dict):
            continue
        start = _finite_seconds(item.get("start"))
        end = _finite_seconds(item.get("end"))
        if start is None or end is None:
            continue
        snapped_start = _nearest_boundary(start, normalized)
        snapped_end = _nearest_boundary(end, normalized)
        if snapped_start is not None and snapped_start < end:
            item["start"] = _format_like(snapped_start, item.get("start"))
        if snapped_end is not None and snapped_end > start:
            item["end"] = _format_like(snapped_end, item.get("end"))
    return analysis

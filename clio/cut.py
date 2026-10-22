from __future__ import annotations

import math
import threading
from pathlib import Path

from clio.i18n import t
from clio.log import format_duration, timed
from clio.utils import run_ffmpeg


def parse_time_range(range_str: str) -> tuple[float, float]:
    """将 '00:00-00:20' 格式解析为 (start_sec, end_sec)。"""
    parts = range_str.split("-", 1)
    if len(parts) != 2:
        raise ValueError(t("cut.parse.range_format", {"range": range_str}))
    start = _to_seconds(parts[0].strip())
    end = _to_seconds(parts[1].strip())
    if end <= start:
        raise ValueError(t("cut.parse.end_before_start", {"end": parts[1].strip(), "start": parts[0].strip()}))
    return start, end


def _to_seconds(s: str) -> float:
    """将 'HH:MM:SS' 或 'MM:SS' 或秒数转为 float 秒。"""
    s = s.strip()
    if not s:
        return 0.0
    chunks = s.split(":")
    if len(chunks) > 3:
        raise ValueError(t("cut.parse.too_many_parts", {"value": s}))
    try:
        values = [float(chunk) for chunk in chunks]
    except ValueError as e:
        raise ValueError(t("cut.parse.invalid_number", {"value": s, "detail": e})) from e
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError(t("cut.parse.negative", {"value": s}))
    if len(values) == 3:
        hours, minutes, seconds = values
        if minutes >= 60 or seconds >= 60:
            raise ValueError(t("cut.parse.minute_second_range", {"value": s}))
        return hours * 3600 + minutes * 60 + seconds
    if len(values) == 2:
        minutes, seconds = values
        if seconds >= 60:
            raise ValueError(t("cut.parse.second_range", {"value": s}))
        return minutes * 60 + seconds
    return values[0]


def cut_one(
    video_path: Path,
    output_path: Path,
    start_sec: float,
    end_sec: float,
    ffmpeg: str,
    reencode: bool = False,
    cancel_event: threading.Event | None = None,
) -> Path:
    """用 ffmpeg 从 video_path 中裁剪 [start_sec, end_sec] 区间到 output_path。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration_sec = end_sec - start_sec
    if duration_sec <= 0:
        raise ValueError(t("cut.invalid_range", {"start": start_sec, "end": end_sec}))
    label = t(
        "cut.timed_label",
        {"name": video_path.name, "start": format_duration(start_sec), "end": format_duration(end_sec)},
    )

    args = [
        "-ss",
        str(start_sec),
        "-i",
        str(video_path),
        "-t",
        str(duration_sec),
    ]
    if reencode:
        args.extend(["-c:v", "libx264", "-crf", "23"])
    else:
        args.extend(["-c", "copy"])
    args.extend(["-y", str(output_path)])

    with timed(label):
        run_ffmpeg(args, ffmpeg, cancel_event=cancel_event)
    return output_path

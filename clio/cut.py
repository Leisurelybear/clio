from __future__ import annotations

import math
import threading
from pathlib import Path

from clio.log import format_duration, timed
from clio.utils import run_ffmpeg


def parse_time_range(range_str: str) -> tuple[float, float]:
    """将 '00:00-00:20' 格式解析为 (start_sec, end_sec)。"""
    parts = range_str.split("-", 1)
    if len(parts) != 2:
        raise ValueError(f"无法解析时间范围: {range_str}")
    start = _to_seconds(parts[0].strip())
    end = _to_seconds(parts[1].strip())
    if end <= start:
        raise ValueError(f"end ({parts[1].strip()}) 必须大于 start ({parts[0].strip()})")
    return start, end


def _to_seconds(s: str) -> float:
    """将 'HH:MM:SS' 或 'MM:SS' 或秒数转为 float 秒。"""
    s = s.strip()
    if not s:
        return 0.0
    chunks = s.split(":")
    if len(chunks) > 3:
        raise ValueError(f"无法解析时间字符串 '{s}': 分量过多")
    try:
        values = [float(chunk) for chunk in chunks]
    except ValueError as e:
        raise ValueError(f"无法解析时间字符串 '{s}': {e}") from e
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError(f"无法解析时间字符串 '{s}': 时间必须是非负有限数")
    if len(values) == 3:
        hours, minutes, seconds = values
        if minutes >= 60 or seconds >= 60:
            raise ValueError(f"无法解析时间字符串 '{s}': 分钟和秒必须小于 60")
        return hours * 3600 + minutes * 60 + seconds
    if len(values) == 2:
        minutes, seconds = values
        if seconds >= 60:
            raise ValueError(f"无法解析时间字符串 '{s}': 秒必须小于 60")
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
        raise ValueError(f"无效裁剪范围: {start_sec}s >= {end_sec}s")
    label = f"裁剪 {video_path.name} ({format_duration(start_sec)}-{format_duration(end_sec)})"

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

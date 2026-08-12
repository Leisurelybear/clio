from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from clio.config import AppConfig
from clio.log import format_size, timed
from clio.utils import get_duration_sec, resolve_binary, run_ffmpeg, run_subprocess

ProgressCB = Callable[[float, float], None]  # (current_sec, total_sec)


def _get_audio_bitrate(input_path: Path, ffprobe: str) -> int:
    """探测音频流实际码率，失败时返回 128kbps 默认值。"""
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=bit_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_path),
    ]
    try:
        result = run_subprocess(cmd, capture_output=True, text=True, check=True)
        raw = result.stdout.strip()
        if raw and raw != "N/A":
            return int(raw)
    except Exception:
        pass
    return 128_000


def compress_video(
    input_path: Path,
    output_path: Path,
    config: AppConfig,
    progress_callback: ProgressCB | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """压缩视频：去声音、降分辨率，尽量接近目标大小。"""
    ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
    ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = get_duration_sec(input_path, ffprobe)
    _cb = progress_callback

    def _on_ffmpeg_progress(sec: float) -> None:
        if _cb:
            _cb(sec, duration)

    if duration <= 0:
        raise ValueError(f"无法读取视频时长: {input_path}")

    cfg = config.compress
    vf = f"scale=min({cfg.max_width}\\,iw):-2,fps={cfg.fps}"

    args = ["-i", str(input_path), "-vf", vf, "-c:v", cfg.codec]

    if cfg.remove_audio:
        args.append("-an")

    if cfg.target_size_mb > 0:
        target_bits = cfg.target_size_mb * 8 * 1024 * 1024
        if not cfg.remove_audio:
            audio_bitrate = _get_audio_bitrate(input_path, ffprobe)
            target_bits -= int(audio_bitrate * duration * 1.05)  # 预留音频码率 + 5% 余量
        video_bitrate = max(int(target_bits / duration * 0.95), 100_000)
        args.extend(
            [
                "-b:v",
                str(video_bitrate),
                "-maxrate",
                str(video_bitrate),
                "-bufsize",
                str(video_bitrate * 2),
            ]
        )
    else:
        args.extend(["-crf", str(cfg.crf)])

    # Write to an exclusive temp file in the same directory, then atomically
    # replace the target only after the encode succeeds (GAP-P1-07). A failed
    # or cancelled encode must never destroy the last valid compressed output.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".part", dir=str(output_path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    args.extend(["-y", str(tmp)])
    orig_size = input_path.stat().st_size
    cmd_preview = f"ffmpeg {' '.join(args)}"
    print(f"  ffmpeg: {cmd_preview}")
    try:
        with timed(f"压缩 {input_path.name} -> {output_path.name}"):
            run_ffmpeg(args, ffmpeg, progress_callback=_on_ffmpeg_progress, cancel_event=cancel_event)
        if get_duration_sec(tmp, ffprobe) <= 0:
            raise ValueError(f"压缩输出无法读取时长: {output_path.name}")
        os.replace(tmp, output_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    new_size = output_path.stat().st_size
    ratio = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
    print(
        f"  体积: {format_size(orig_size)} -> {format_size(new_size)}"
        f"（压缩 {ratio:.0f}%，目标 {cfg.target_size_mb:.0f} MB）"
    )
    return output_path

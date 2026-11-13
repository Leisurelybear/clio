from __future__ import annotations

import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from clio.config import AppConfig
from clio.i18n import t
from clio.log import format_size, timed
from clio.utils import get_duration_sec, probe_media_details, resolve_binary, run_ffmpeg

ProgressCB = Callable[[float, float], None]  # (current_sec, total_sec)
MIN_VIDEO_BITRATE = 800_000
AUDIO_BITRATE = 64_000
ANALYSIS_WIDTH_MIN = 960
ANALYSIS_WIDTH_MAX = 1280
ANALYSIS_FPS_MIN = 2
ANALYSIS_FPS_MAX = 5
_HDR_COLOR_TRANSFERS = frozenset({"arib-std-b67", "smpte2084"})


@dataclass(frozen=True)
class AnalysisCompressAdvice:
    field: Literal["max_width", "fps"]
    current: int | float
    recommended_min: int
    recommended_max: int


def analysis_compress_advice(
    *,
    media_resolution: str,
    max_width: int,
    fps: float,
) -> tuple[AnalysisCompressAdvice, ...]:
    """Return non-mutating compression advice for the requested analysis detail."""
    resolution = media_resolution.strip().lower()
    advice: list[AnalysisCompressAdvice] = []
    if resolution == "high" and max_width < ANALYSIS_WIDTH_MAX:
        advice.append(AnalysisCompressAdvice("max_width", max_width, ANALYSIS_WIDTH_MIN, ANALYSIS_WIDTH_MAX))
    if resolution != "low" and fps < ANALYSIS_FPS_MIN:
        advice.append(AnalysisCompressAdvice("fps", fps, ANALYSIS_FPS_MIN, ANALYSIS_FPS_MAX))
    return tuple(advice)


def _is_hdr_source(input_path: Path, ffprobe: str) -> bool:
    """Return whether the source carries a PQ or HLG transfer function."""
    try:
        details = probe_media_details(input_path, ffprobe)
    except Exception:  # noqa: BLE001 - color probing must not block compression
        return False
    transfer = str(details.get("color_transfer") or "").strip().lower()
    return transfer in _HDR_COLOR_TRANSFERS


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
        raise ValueError(t("compress.duration_unreadable", {"path": input_path}))

    cfg = config.compress
    scale_and_fps = f"scale=min({cfg.max_width}\\,iw):-2,fps={cfg.fps}"
    if _is_hdr_source(input_path, ffprobe):
        # zscale converts PQ/HLG into linear light before Hable tone mapping,
        # then establishes standard BT.709 metadata for the SDR output.
        vf = (
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            f"zscale=t=bt709:m=bt709:r=tv,{scale_and_fps},format=yuv420p"
        )
        print(t("compress.hdr_tonemap", {"src": input_path.name}))
    else:
        vf = f"{scale_and_fps},format=yuv420p"

    args = [
        "-i",
        str(input_path),
        "-vf",
        vf,
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        cfg.codec,
        "-movflags",
        "+faststart",
    ]

    if cfg.remove_audio:
        args.append("-an")
    else:
        args.extend(["-ac", "1", "-c:a", "aac", "-b:a", f"{AUDIO_BITRATE // 1000}k"])

    if cfg.target_size_mb > 0:
        target_bits = cfg.target_size_mb * 8 * 1024 * 1024
        if not cfg.remove_audio:
            target_bits -= int(AUDIO_BITRATE * duration * 1.05)  # 预留单声道 AAC 音轨 + 5% 余量
        # A strict 5 MB cap makes long clips unusable for visual analysis.
        # Keep the duration-based budget for short clips, but preserve enough
        # detail for long clips instead of falling to ~100 kbps.
        video_bitrate = max(int(target_bits / duration * 0.95), MIN_VIDEO_BITRATE)
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
    # The temp file ends in ".part", so ffmpeg cannot infer the container from
    # the extension; name the muxer explicitly (compress targets the
    # faststart-capable mp4/mov family, see -movflags above).
    muxer = "mov" if output_path.suffix.lower() == ".mov" else "mp4"
    args.extend(["-f", muxer, "-y", str(tmp)])
    orig_size = input_path.stat().st_size
    cmd_preview = f"ffmpeg {' '.join(args)}"
    print(f"  ffmpeg: {cmd_preview}")
    try:
        with timed(t("compress.timed_label", {"src": input_path.name, "dst": output_path.name})):
            run_ffmpeg(args, ffmpeg, progress_callback=_on_ffmpeg_progress, cancel_event=cancel_event)
        if get_duration_sec(tmp, ffprobe) <= 0:
            raise ValueError(t("compress.output_duration_unreadable", {"name": output_path.name}))
        os.replace(tmp, output_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    new_size = output_path.stat().st_size
    ratio = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
    print(
        t(
            "compress.size_summary",
            {
                "before": format_size(orig_size),
                "after": format_size(new_size),
                "ratio": f"{ratio:.0f}",
                "target": f"{cfg.target_size_mb:.0f}",
            },
        )
    )
    return output_path

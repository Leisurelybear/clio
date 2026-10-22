"""Cover frame extraction for analyzed videos."""

from __future__ import annotations

from pathlib import Path

from clio.config import AppConfig
from clio.i18n import t
from clio.utils import resolve_binary, run_ffmpeg


def _normalize_timestamp(value: str) -> str | None:
    raw = str(value or "").strip()
    parts = raw.split(":")
    try:
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            if minutes < 0 or seconds < 0:
                return None
            return f"00:{minutes:02d}:{seconds:06.3f}"
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            if hours < 0 or minutes < 0 or seconds < 0:
                return None
            return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"
    except (TypeError, ValueError):
        return None
    return None


def extract_cover_frame(config: AppConfig, video_path: Path, analysis: dict, stem: str) -> Path | None:
    timestamp = _normalize_timestamp(str(analysis.get("cover_timestamp", "")))
    if not timestamp:
        return None

    covers_dir = config.paths.output_dir / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    out_path = covers_dir / f"{stem}.jpg"
    try:
        ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
        run_ffmpeg(
            [
                "-y",
                "-ss",
                timestamp,
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(out_path),
            ],
            ffmpeg,
        )
    except Exception as e:
        print(t("task.cover.extract_failed", {"name": video_path.name, "timestamp": timestamp, "detail": e}))
        out_path.unlink(missing_ok=True)
        return None
    return out_path

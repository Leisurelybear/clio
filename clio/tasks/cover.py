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


def extract_cover_frame(
    config: AppConfig,
    video_path: Path,
    analysis: dict,
    stem: str,
    *,
    fallback_path: Path | None = None,
) -> Path | None:
    timestamp = _normalize_timestamp(str(analysis.get("cover_timestamp", "")))
    if not timestamp:
        return None

    source = Path(video_path)
    if not source.is_file() and fallback_path is not None:
        fallback = Path(fallback_path)
        if fallback.is_file():
            source = fallback
    if not source.is_file():
        print(t("task.cover.source_missing", {"name": source.name}))
        return None

    covers_dir = config.paths.output_dir / "covers"
    covers_dir.mkdir(parents=True, exist_ok=True)
    out_path = covers_dir / f"{stem}.jpg"
    # A failed seek past EOF leaves the previous run's cover in place, so drop
    # the old file first and treat anything that does not come back as failure.
    out_path.unlink(missing_ok=True)
    try:
        ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
        run_ffmpeg(
            [
                "-y",
                "-ss",
                timestamp,
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(out_path),
            ],
            ffmpeg,
        )
    except (OSError, RuntimeError, ValueError) as e:
        print(t("task.cover.extract_failed", {"name": source.name, "timestamp": timestamp, "detail": e}))
        out_path.unlink(missing_ok=True)
        return None
    if not out_path.is_file() or out_path.stat().st_size == 0:
        print(t("task.cover.empty_frame", {"name": source.name, "timestamp": timestamp}))
        out_path.unlink(missing_ok=True)
        return None
    return out_path

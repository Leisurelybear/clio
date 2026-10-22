"""Plan preview media mapping for rendered cut clips (R-031b)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clio.cut import parse_time_range
from clio.utils import safe_basename, sanitize_name, validate_within_root


def _parse_plan_range(timeline: Any) -> tuple[float, float] | None:
    if not isinstance(timeline, str) or not timeline.strip():
        return None
    try:
        return parse_time_range(timeline)
    except ValueError:
        return None


def expected_clip_name(segment: dict[str, Any], clip_index: int) -> str:
    """Return the exact filename written by ``run_cut_all`` for a sequence entry."""
    raw_index = str(segment.get("index") or "").strip()
    if not raw_index:
        raise ValueError("segment index is required")
    index = safe_basename(raw_index, max_len=30)
    title = sanitize_name(str(segment.get("title") or "").strip(), max_len=30)
    return f"{index}_{title}_seg_{clip_index:03d}.mp4"


def _same_time(left: Any, right: float) -> bool:
    try:
        return abs(float(left) - right) <= 0.05
    except (TypeError, ValueError):
        return False


def _cut_info_matches(
    sidecar: Path,
    clip_path: Path,
    cuts_dir: Path,
    *,
    clip_index: int,
    timeline: str,
    start: float,
    end: float,
    plan_mtime: float | None,
) -> bool:
    if not sidecar.is_file():
        return _clip_is_current(clip_path, plan_mtime)
    try:
        info_path = validate_within_root(sidecar, cuts_dir)
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    info = data.get("_cut_info") if isinstance(data, dict) else None
    if info is None:
        return _clip_is_current(clip_path, plan_mtime)
    if not isinstance(info, dict):
        return False
    try:
        if int(info.get("seg_index")) != clip_index:
            return False
    except (TypeError, ValueError):
        return False
    if str(info.get("timeline") or "").strip() != timeline:
        return False
    if "plan_start_sec" in info or "plan_end_sec" in info:
        return _same_time(info.get("plan_start_sec"), start) and _same_time(info.get("plan_end_sec"), end)
    if "start_sec" in info and not _same_time(info.get("start_sec"), start):
        return _clip_is_current(clip_path, plan_mtime)
    if "end_sec" in info and not _same_time(info.get("end_sec"), end):
        return _clip_is_current(clip_path, plan_mtime)
    return True


def _clip_is_current(clip_path: Path, plan_mtime: float | None) -> bool:
    if plan_mtime is None:
        return True
    try:
        return clip_path.stat().st_mtime >= plan_mtime - 0.001
    except OSError:
        return False


def _valid_clip(
    cuts_dir: Path, segment: dict[str, Any], clip_index: int, plan_mtime: float | None = None
) -> dict[str, Any] | None:
    timeline = str(segment.get("use_timeline") or "").strip()
    parsed = _parse_plan_range(timeline)
    if parsed is None:
        return None
    start, end = parsed
    try:
        clip_name = expected_clip_name(segment, clip_index)
    except ValueError:
        return None
    candidate = cuts_dir / clip_name
    try:
        clip_path = validate_within_root(candidate, cuts_dir)
    except (OSError, ValueError):
        return None
    if clip_path.parent != cuts_dir.resolve() or not clip_path.is_file() or clip_path.suffix.lower() != ".mp4":
        return None
    if not _cut_info_matches(
        clip_path.with_suffix(".json"),
        clip_path,
        cuts_dir,
        clip_index=clip_index,
        timeline=timeline,
        start=start,
        end=end,
        plan_mtime=plan_mtime,
    ):
        return None
    try:
        stat = clip_path.stat()
    except OSError:
        return None
    return {
        "file": clip_name,
        "rev": f"{stat.st_mtime_ns}-{stat.st_size}",
        "duration_sec": round(end - start, 3),
        "start_sec": round(start, 3),
        "end_sec": round(end, 3),
    }


def build_preview_media(
    plan: dict[str, Any], cuts_dir: Path, *, day: str, plan_mtime: float | None = None
) -> dict[str, Any]:
    """Map plan sequence entries to standard cut clips without exposing paths."""
    sequence = plan.get("sequence") if isinstance(plan, dict) else None
    if not isinstance(sequence, list):
        sequence = []
    segments: list[dict[str, Any]] = []
    for offset, segment in enumerate(sequence):
        if not isinstance(segment, dict):
            segment = {}
        clip_index = offset + 1
        timeline = str(segment.get("use_timeline") or "").strip()
        parsed = _parse_plan_range(timeline)
        clip = _valid_clip(cuts_dir, segment, clip_index, plan_mtime) if cuts_dir.is_dir() else None
        segments.append(
            {
                "seg_index": offset,
                "clip_index": clip_index,
                "file": clip["file"] if clip else None,
                "rev": clip["rev"] if clip else None,
                "duration_sec": clip["duration_sec"] if clip else None,
                "start_sec": clip["start_sec"] if clip else (round(parsed[0], 3) if parsed else None),
                "end_sec": clip["end_sec"] if clip else (round(parsed[1], 3) if parsed else None),
                "source": {
                    "video_index": str(segment.get("index") or ""),
                    "timeline": timeline,
                },
            }
        )
    return {
        "ok": True,
        "day": day,
        "available": any(item["file"] for item in segments),
        "segments": segments,
    }

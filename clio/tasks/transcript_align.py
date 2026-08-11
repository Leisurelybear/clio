"""Attach Whisper transcript snippets to analysis timeline entries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clio.config import AppConfig
from clio.identity import legacy_segment_offset_sec, load_identity
from clio.tasks._helpers import _write_text_file
from clio.utils import write_json_atomic


def _parse_time_sec(value: str) -> float | None:
    parts = str(value or "").strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except (TypeError, ValueError):
        return None
    return None


def _overlap_sec(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _transcript_path_for_analysis(config: AppConfig, analysis: dict[str, Any]) -> Path | None:
    transcripts_dir = getattr(config, "transcripts_dir", None)
    if transcripts_dir is None:
        return None
    identity = load_identity(analysis)
    if identity is not None:
        candidate = transcripts_dir / f"{identity.compressed_stem}_transcript.json"
        if candidate.is_file():
            return candidate
    compressed = analysis.get("compressed_file", "")
    if compressed:
        candidate = transcripts_dir / f"{Path(compressed).stem}_transcript.json"
        if candidate.is_file():
            return candidate
    return None


def _load_transcript(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def attach_transcript_to_analysis(config: AppConfig, analysis: dict[str, Any]) -> bool:
    transcript = _load_transcript(_transcript_path_for_analysis(config, analysis))
    if not transcript:
        return False
    return attach_transcript_data(config, analysis, transcript)


def attach_transcript_data(config: AppConfig, analysis: dict[str, Any], transcript: dict[str, Any]) -> bool:
    timeline = analysis.get("timeline")
    segments = transcript.get("segments")
    if not isinstance(timeline, list) or not isinstance(segments, list) or not segments:
        return False

    identity = load_identity(analysis)
    compress_cfg = getattr(config, "compress", None)
    remove_audio = getattr(compress_cfg, "remove_audio", False)
    offset = legacy_segment_offset_sec(identity) if identity and remove_audio else 0.0
    whisper_cfg = getattr(config, "whisper", None)
    max_segments = int(getattr(whisper_cfg, "max_segments_per_clip", 5))
    changed = False

    for item in timeline:
        if not isinstance(item, dict):
            continue
        start = _parse_time_sec(item.get("start", ""))
        end = _parse_time_sec(item.get("end", ""))
        if start is None or end is None or end <= start:
            continue

        abs_start = start + offset
        abs_end = end + offset
        matched: list[dict[str, Any]] = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            try:
                seg_start = float(seg.get("start", 0.0))
                seg_end = float(seg.get("end", 0.0))
            except (TypeError, ValueError):
                continue
            overlap = _overlap_sec(abs_start, abs_end, seg_start, seg_end)
            if overlap <= 0:
                continue
            matched.append(
                {
                    "start": seg_start,
                    "end": seg_end,
                    "text": str(seg.get("text", "")).strip(),
                    "avg_logprob": seg.get("avg_logprob"),
                    "overlap_sec": round(overlap, 3),
                }
            )
        if not matched:
            if "transcript" in item:
                item.pop("transcript", None)
                item.pop("transcript_segments", None)
                changed = True
            continue
        matched.sort(key=lambda x: (x["start"], -x["overlap_sec"]))
        kept = [m for m in matched if m["text"]][:max_segments]
        if not kept:
            if "transcript" in item:
                item.pop("transcript", None)
                item.pop("transcript_segments", None)
                changed = True
            continue
        transcript_text = " ".join(m["text"] for m in kept)
        item["transcript"] = transcript_text
        item["transcript_segments"] = kept
        changed = True

    return changed


def enrich_matching_analysis_files(config: AppConfig, transcript: dict[str, Any]) -> int:
    identity = load_identity(transcript)
    compressed_stem = identity.compressed_stem if identity is not None else str(transcript.get("source_stem", ""))
    texts_dir = getattr(config, "texts_dir", None)
    if not compressed_stem or texts_dir is None or not texts_dir.is_dir():
        return 0

    updated = 0
    for json_path in sorted(texts_dir.glob("*.json")):
        try:
            analysis = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        analysis_identity = load_identity(analysis)
        analysis_stem = (
            analysis_identity.compressed_stem if analysis_identity else Path(analysis.get("compressed_file", "")).stem
        )
        if analysis_stem != compressed_stem:
            continue
        if attach_transcript_data(config, analysis, transcript):
            write_json_atomic(json_path, analysis)
            source = Path(analysis.get("source_file", ""))
            compressed = Path(analysis.get("compressed_file", ""))
            _write_text_file(json_path.with_suffix(".txt"), analysis, source, compressed)
            updated += 1
    return updated

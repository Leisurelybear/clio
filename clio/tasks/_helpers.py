"""Shared helper functions and classes for pipeline tasks."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from clio.config import AppConfig
from clio.identity import MediaIdentity
from clio.log import format_duration
from clio.utils import (
    format_index,
    probe_video_info,
    resolve_binary,
    sanitize_name,
    write_bytes_atomic,
    write_text_atomic,
)
from clio.vmeta import VideoMeta


@dataclass
class ClipRecord:
    index: int
    stem: str
    source_path: Path
    compressed_path: Path | None = None
    text_path: Path | None = None
    analysis: dict | None = None
    duration_sec: float = 0.0
    meta: VideoMeta | None = None
    identity: MediaIdentity | None = None


_INDEX_PREFIX_RE = re.compile(r"^\d+_(.+)$")
_SEGMENT_SUFFIX_RE = re.compile(r"^(.+?)_seg\d+$")
_ARTIFACT_SUFFIXES = ("_voiceover", "_transcript", "_labeled")


def _strip_artifact_suffixes(stem: str) -> str:
    changed = True
    while changed:
        changed = False
        for suffix in _ARTIFACT_SUFFIXES:
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                changed = True
                break
    return stem


def _stem_variants(stem: str, *, include_segment_base: bool = False) -> set[str]:
    base = _strip_artifact_suffixes(Path(stem).stem.lower())
    variants = {base}
    m = _INDEX_PREFIX_RE.match(base)
    if m:
        variants.add(m.group(1))
    if include_segment_base:
        for value in list(variants):
            m = _SEGMENT_SUFFIX_RE.match(value)
            if m:
                variants.add(m.group(1))
    return variants


def _selected_stems(files: list[str]) -> set[str]:
    selected: set[str] = set()
    for name in files:
        selected.update(_stem_variants(name))
    return selected


def _matches_selected_stem(path: Path, selected: set[str]) -> bool:
    return bool(_stem_variants(path.stem, include_segment_base=True) & selected)


def _matches_selected_artifact(path: Path, selected: set[str]) -> bool:
    if _matches_selected_stem(path, selected):
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    candidates: list[str] = []
    identity = data.get("media_identity")
    if isinstance(identity, dict):
        candidates.extend(
            str(value) for value in (identity.get("compressed_stem"), identity.get("original_stem")) if value
        )
    for key in ("compressed_file", "source_file", "index"):
        value = data.get(key)
        if value:
            candidates.append(str(value))
    for candidate in candidates:
        if _stem_variants(candidate, include_segment_base=True) & selected:
            return True
    return False


def _build_stem(index: int, title: str, config: AppConfig) -> str:
    idx = format_index(index, config.naming.index_width)
    return f"{idx}_{sanitize_name(title)}"


def _next_index(scan_dir: Path, index_width: int = 3) -> int:
    """Scan scan_dir for {index}_* prefixed files and return next available index."""
    if not scan_dir.is_dir():
        return 1
    max_idx = 0
    for p in sorted(scan_dir.iterdir()):
        stem = p.stem
        if "_" in stem:
            prefix = stem.split("_", 1)[0]
            if prefix.isdigit():
                idx = int(prefix)
                if idx > max_idx:
                    max_idx = idx
    return max_idx + 1


def _eta_line(label: str, i: int, total: int, name: str, completed: int, elapsed_total: float) -> str:
    """生成 `[label i/total] name（平均 X，剩余 ~Y）` 形式的进度行。"""
    if completed > 0:
        avg = elapsed_total / completed
        remaining = avg * (total - i)
        return f"[{label} {i}/{total}] {name}（平均 {format_duration(avg)}，剩余 ~{format_duration(remaining)}）"
    return f"[{label} {i}/{total}] {name}"


def _write_text_file(path: Path, analysis: dict, source: Path, compressed: Path) -> None:
    lines = [
        f"# {analysis.get('title', '未命名')}",
        "",
        f"**源文件**: {source.name}",
        f"**压缩文件**: {compressed.name}",
        "",
        "## 简介",
        analysis.get("summary", ""),
        "",
        f"**地点**: {analysis.get('location', '未知')}",
        f"**氛围**: {analysis.get('mood', '')}",
        f"**建议使用**: {analysis.get('suggested_use', '')}",
        "",
        "## 时间轴",
    ]
    for item in analysis.get("timeline", []):
        lines.append(f"- [{item.get('start', '?')} - {item.get('end', '?')}] {item.get('description', '')}")
        if item.get("transcript"):
            lines.append(f"  - 同期声: {item.get('transcript')}")
    lines.extend(["", "## 亮点"])
    for h in analysis.get("highlights", []):
        lines.append(f"- {h}")

    write_text_atomic(path, "\n".join(lines))


def _rewrite_text_file(path: Path, analysis: dict) -> None:
    """根据已存在的 analysis 重写 .txt（不需要源文件/压缩文件路径）。"""
    source_name = analysis.get("source_file", "?")
    lines = [
        f"# {analysis.get('title', '未命名')}",
        "",
        f"**源文件**: {source_name}",
        "",
        "## 简介",
        analysis.get("summary", ""),
        "",
        f"**地点**: {analysis.get('location', '未知')}",
        f"**氛围**: {analysis.get('mood', '')}",
        f"**建议使用**: {analysis.get('suggested_use', '')}",
        "",
        "## 时间轴",
    ]
    for item in analysis.get("timeline", []):
        lines.append(f"- [{item.get('start', '?')} - {item.get('end', '?')}] {item.get('description', '')}")
        if item.get("transcript"):
            lines.append(f"  - 同期声: {item.get('transcript')}")
    lines.extend(["", "## 亮点"])
    for h in analysis.get("highlights", []):
        lines.append(f"- {h}")
    if analysis.get("_changelog"):
        lines.extend(["", "## 本次 refine 改动"])
        for item in analysis["_changelog"]:
            lines.append(f"- {item}")
    write_text_atomic(path, "\n".join(lines))


def _rewrite_script_md(path: Path, script: dict) -> None:
    md = (
        f"# {script.get('title', path.stem)} 口播\n\n"
        f"{script.get('voiceover', '')}\n\n"
        f"**剪辑建议**: {script.get('edit_tip', '')}\n"
    )
    if script.get("_changelog"):
        md += "\n## 本次 refine 改动\n"
        for item in script["_changelog"]:
            md += f"- {item}\n"
    write_text_atomic(path, md)


def _get_video_info(rec: ClipRecord, ffprobe: str) -> dict:
    if rec.meta is not None:
        return {
            "duration_sec": rec.meta.source_duration_sec,
            "size_mb": round(rec.meta.source_size / 1024 / 1024, 2),
        }
    if rec.compressed_path is not None:
        m = VideoMeta.read(rec.compressed_path)
        if m is not None:
            return {
                "duration_sec": m.source_duration_sec,
                "size_mb": round(m.source_size / 1024 / 1024, 2),
            }
    return probe_video_info(rec.source_path, ffprobe) if rec.source_path.exists() else {}


def _clip_records_from_csv(path: Path) -> list[ClipRecord]:
    """Load summary.csv rows back into ClipRecords (for selection re-analyze merge)."""
    if not path.is_file():
        return []
    records: list[ClipRecord] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw_idx = (row.get("index") or "").strip()
            try:
                idx = int(raw_idx)
            except ValueError:
                continue
            stem = (row.get("stem") or "").strip()
            if not stem:
                continue
            try:
                duration_sec = float(row.get("duration_sec") or 0) or 0.0
            except ValueError:
                duration_sec = 0.0
            source = Path(row["source_file"]) if row.get("source_file") else Path()
            compressed_raw = (row.get("compressed_file") or "").strip()
            text_raw = (row.get("text_file") or "").strip()
            records.append(
                ClipRecord(
                    index=idx,
                    stem=stem,
                    source_path=source,
                    compressed_path=Path(compressed_raw) if compressed_raw else None,
                    text_path=Path(text_raw) if text_raw else None,
                    analysis={
                        "title": row.get("title", ""),
                        "summary": row.get("summary", ""),
                        "location": row.get("location", ""),
                        "mood": row.get("mood", ""),
                        "suggested_use": row.get("suggested_use", ""),
                    },
                    duration_sec=duration_sec,
                )
            )
    return records


def _merge_summary_records(existing: list[ClipRecord], new: list[ClipRecord]) -> list[ClipRecord]:
    """Replace/append by index so selection re-analyze keeps untouched rows."""
    by_index = {r.index: r for r in existing}
    for r in new:
        by_index[r.index] = r
    return sorted(by_index.values(), key=lambda r: r.index)


def _write_csv(path: Path, records: list[ClipRecord], config: AppConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
    fieldnames = [
        "index",
        "stem",
        "title",
        "summary",
        "location",
        "mood",
        "suggested_use",
        "source_file",
        "compressed_file",
        "text_file",
        "duration_sec",
        "source_size_mb",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for rec in records:
        a = rec.analysis or {}
        if not rec.duration_sec:
            info = _get_video_info(rec, ffprobe)
            duration_sec = info.get("duration_sec", "")
            source_size_mb = info.get("size_mb", "")
        else:
            duration_sec = rec.duration_sec
            source_size_mb = ""
        writer.writerow(
            {
                "index": format_index(rec.index, config.naming.index_width),
                "stem": rec.stem,
                "title": a.get("title", ""),
                "summary": a.get("summary", ""),
                "location": a.get("location", ""),
                "mood": a.get("mood", ""),
                "suggested_use": a.get("suggested_use", ""),
                "source_file": str(rec.source_path),
                "compressed_file": str(rec.compressed_path) if rec.compressed_path else "",
                "text_file": str(rec.text_path) if rec.text_path else "",
                "duration_sec": duration_sec,
                "source_size_mb": source_size_mb,
            }
        )
    write_bytes_atomic(path, buf.getvalue().encode("utf-8-sig"))

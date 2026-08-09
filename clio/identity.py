from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from clio.vmeta import VideoIndex, VideoMeta

# Align with clio/ui/routes/videos.py segment aliases
SEGMENT_SUFFIX_RE = re.compile(r"(?i)_(?:seg|part|pt|chunk)(\d+)$")


def is_legacy_split_stem(stem: str) -> bool:
    return SEGMENT_SUFFIX_RE.search(stem) is not None


def _indexed_source_stem(compressed_stem: str) -> str:
    if "_" not in compressed_stem:
        return compressed_stem
    prefix, source_stem = compressed_stem.split("_", 1)
    return source_stem if prefix.isdigit() else compressed_stem


def _vindex_contains(vindex: VideoIndex | None, compressed_path: Path) -> bool:
    return vindex is not None and any(segment.filename == compressed_path.name for segment in vindex.segments)


def is_legacy_split_path(compressed_path: Path) -> bool:
    meta = VideoMeta.read(compressed_path)
    if meta is not None:
        return meta.split_info is not None or meta.is_split_segment

    indexed_stem = _indexed_source_stem(compressed_path.stem)
    exact_vindex = VideoIndex.read(indexed_stem, compressed_path.parent)
    if _vindex_contains(exact_vindex, compressed_path):
        assert exact_vindex is not None
        return exact_vindex.is_split and len(exact_vindex.segments) > 1

    original_stem = SEGMENT_SUFFIX_RE.sub("", indexed_stem)
    legacy_vindex = VideoIndex.read(original_stem, compressed_path.parent)
    if _vindex_contains(legacy_vindex, compressed_path):
        assert legacy_vindex is not None
        return legacy_vindex.is_split and len(legacy_vindex.segments) > 1
    return is_legacy_split_stem(compressed_path.stem)


def is_legacy_split_identity(identity: MediaIdentity | None) -> bool:
    if identity is None:
        return False
    if identity.segment_index is not None:
        return True
    if abs(float(identity.segment_offset_sec or 0.0)) > 1e-6:
        return True
    return False


def legacy_segment_offset_sec(identity: MediaIdentity | None) -> float:
    if not is_legacy_split_identity(identity):
        return 0.0
    assert identity is not None
    return float(identity.segment_offset_sec or 0.0)


@dataclass
class MediaIdentity:
    original_stem: str
    original_path: str
    compressed_stem: str
    compressed_path: str
    index: str
    segment_index: int | None = None
    segment_offset_sec: float = 0.0
    segment_duration_sec: float | None = None


def _identity_to_dict(identity: MediaIdentity) -> dict[str, Any]:
    """Serialize MediaIdentity to a plain dict for JSON embedding."""
    return asdict(identity)


def _extract_original_stem(compressed_stem: str) -> str:
    """Extract original stem from a compressed file stem.

    Handles '001_GL010683' -> 'GL010683', '001_GL010683_seg01' -> 'GL010683',
    aliases _part/_pt/_chunk, and 'GL010683' -> 'GL010683'.
    """
    stem = SEGMENT_SUFFIX_RE.sub("", compressed_stem)
    if "_" not in stem:
        return stem
    _, orig_stem = stem.split("_", 1)
    return orig_stem


def _find_original_by_stem(original_stem: str, project_dir: Path | None = None) -> Path | None:
    """Find an original video file by stem from videos.json (or project_dir scan fallback)."""
    from clio.tasks._video_loader import load_selected_videos

    exts = (".mp4", ".mov", ".mkv", ".avi", ".mts", ".m2ts", ".m4v", ".webm", ".lrv")
    selected = load_selected_videos(project_dir)
    for p in selected:
        if p.stem.lower() == original_stem.lower() and p.suffix.lower() in exts:
            return p.resolve()
    # Legacy collocated layout: original videos live in project_dir itself
    if project_dir is not None and project_dir.is_dir() and not selected:
        for ext in exts:
            candidate = project_dir / f"{original_stem}{ext}"
            if candidate.is_file():
                return candidate.resolve()
        try:
            for p in project_dir.iterdir():
                if p.is_file() and p.suffix.lower() in exts and p.stem.lower() == original_stem.lower():
                    return p.resolve()
        except OSError:
            pass
    return None


def resolve_identity(
    compressed_path: Path,
    index: str | Path,
    project_dir: Path | str | None = None,
) -> MediaIdentity:
    """Build MediaIdentity from .vmeta sidecar first, fall back to filename parsing.

    Preferred:
        resolve_identity(compressed, "001", project_dir=proj)

    Legacy (still accepted for older call sites/tests):
        resolve_identity(compressed, input_dir_path, "001")
    """
    if isinstance(index, Path):
        # legacy: (compressed, input_dir, index_string_as_third_positional)
        proj: Path | None = index
        idx = project_dir if isinstance(project_dir, str) else str(project_dir or "")
    else:
        idx = str(index)
        if isinstance(project_dir, Path):
            proj = project_dir
        elif isinstance(project_dir, str) and project_dir:
            # unlikely: project_dir passed as string path
            try:
                cand = Path(project_dir)
                proj = cand if cand.is_dir() else None
            except Exception:
                proj = None
        else:
            proj = None

    compressed_stem = compressed_path.stem
    compressed_resolved = compressed_path.resolve()

    # Priority 1: Read .vmeta sidecar
    meta = VideoMeta.read(compressed_path)
    if meta is not None:
        si = meta.split_info
        return MediaIdentity(
            original_stem=si.original_stem if si else Path(meta.source_path).stem,
            original_path=meta.source_path,
            compressed_stem=compressed_stem,
            compressed_path=str(compressed_resolved),
            index=idx,
            segment_index=si.segment_index if si else None,
            segment_offset_sec=si.offset_sec if si else 0.0,
            segment_duration_sec=si.segment_duration_sec if si else None,
        )

    # Priority 2: Try the exact source stem before legacy suffix stripping.
    indexed_stem = _indexed_source_stem(compressed_stem)
    original_stem = indexed_stem
    vindex = VideoIndex.read(original_stem, compressed_path.parent)
    if not _vindex_contains(vindex, compressed_path):
        original_stem = SEGMENT_SUFFIX_RE.sub("", indexed_stem)
        vindex = VideoIndex.read(original_stem, compressed_path.parent)
    # A candidate index is only usable when it actually lists this file.
    if not _vindex_contains(vindex, compressed_path):
        vindex = None
    if vindex is not None:
        seg_num = None
        offset_sec = 0.0
        seg_dur = None
        m = SEGMENT_SUFFIX_RE.search(compressed_stem) if vindex.is_split and len(vindex.segments) > 1 else None
        if m is not None:
            seg_num = int(m.group(1))
            for s in vindex.segments:
                if s.segment_number == seg_num:
                    offset_sec = s.offset_sec
                    seg_dur = s.duration_sec
                    break
        return MediaIdentity(
            original_stem=original_stem,
            original_path=vindex.source_path,
            compressed_stem=compressed_stem,
            compressed_path=str(compressed_resolved),
            index=idx,
            segment_index=seg_num,
            segment_offset_sec=offset_sec,
            segment_duration_sec=seg_dur,
        )

    # Priority 3: Filename fallback via videos.json / project_dir.
    exact_path = _find_original_by_stem(indexed_stem, proj)
    if exact_path is not None:
        return MediaIdentity(
            original_stem=indexed_stem,
            original_path=str(exact_path),
            compressed_stem=compressed_stem,
            compressed_path=str(compressed_resolved),
            index=idx,
        )

    original_stem = SEGMENT_SUFFIX_RE.sub("", indexed_stem)
    orig_path = _find_original_by_stem(original_stem, proj)
    seg_num = None
    m = SEGMENT_SUFFIX_RE.search(compressed_stem)
    if m:
        seg_num = int(m.group(1))
    return MediaIdentity(
        original_stem=original_stem,
        original_path=str(orig_path) if orig_path else "",
        compressed_stem=compressed_stem,
        compressed_path=str(compressed_resolved),
        index=idx,
        segment_index=seg_num,
        segment_offset_sec=0.0,
        segment_duration_sec=None,
    )


def _identity_source_stem(compressed_path: Path) -> str:
    meta = VideoMeta.read(compressed_path)
    if meta is not None:
        return Path(meta.source_path).stem
    indexed_stem = _indexed_source_stem(compressed_path.stem)
    exact_vindex = VideoIndex.read(indexed_stem, compressed_path.parent)
    if _vindex_contains(exact_vindex, compressed_path):
        assert exact_vindex is not None
        return exact_vindex.source_stem
    return SEGMENT_SUFFIX_RE.sub("", indexed_stem)


def _canonical_source_stem(compressed_path: Path) -> str | None:
    meta = VideoMeta.read(compressed_path)
    if meta is not None:
        if meta.split_info is None and not meta.is_split_segment:
            return Path(meta.source_path).stem
        return None
    indexed_stem = _indexed_source_stem(compressed_path.stem)
    vindex = VideoIndex.read(indexed_stem, compressed_path.parent)
    if _vindex_contains(vindex, compressed_path) and vindex is not None and not vindex.is_split:
        return vindex.source_stem
    return None


def prefer_canonical_compressed(paths: list[Path]) -> list[Path]:
    canonical_sources = {
        source_stem.casefold() for path in paths if (source_stem := _canonical_source_stem(path)) is not None
    }
    return [
        path
        for path in paths
        if not is_legacy_split_path(path) or _identity_source_stem(path).casefold() not in canonical_sources
    ]


def load_identity(data: dict) -> MediaIdentity | None:
    """Extract MediaIdentity from a v2 JSON artifact.

    Returns None for v1 (pre-identity) artifacts.
    """
    raw = data.get("media_identity")
    if raw is None or not isinstance(raw, dict):
        return None
    try:
        return MediaIdentity(
            original_stem=str(raw["original_stem"]),
            original_path=str(raw.get("original_path", "")),
            compressed_stem=str(raw["compressed_stem"]),
            compressed_path=str(raw.get("compressed_path", "")),
            index=str(raw["index"]),
            segment_index=raw.get("segment_index"),
            segment_offset_sec=float(raw.get("segment_offset_sec", 0.0)),
            segment_duration_sec=raw.get("segment_duration_sec"),
        )
    except (KeyError, TypeError, ValueError):
        return None

"""Reindex task — rebuild .vindex and .vmeta sidecar files from existing compressed videos."""

from __future__ import annotations

import time as _time
from collections import defaultdict
from pathlib import Path

from clio._constants import VIDEO_EXTS
from clio.config import AppConfig
from clio.i18n import t
from clio.identity import SEGMENT_SUFFIX_RE
from clio.utils import get_duration_sec, resolve_binary
from clio.vmeta import SegmentEntry, VideoIndex, VideoMeta


def _find_original_for_stem(stem: str, project_dir: Path | None = None) -> Path | None:
    """Try to find original video by stem via videos.json (or project_dir scan)."""
    from clio.tasks._video_loader import load_selected_videos

    selected = load_selected_videos(project_dir)
    for p in selected:
        if p.stem.lower() == stem.lower() and p.suffix.lower() in VIDEO_EXTS:
            return p
    if project_dir is not None and project_dir.is_dir() and not selected:
        for ext in VIDEO_EXTS:
            candidate = project_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
        try:
            for p in project_dir.rglob("*"):
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p.stem.lower() == stem.lower():
                    return p
        except OSError:
            pass
    return None


def _group_stem(path: Path, project_dir: Path | None) -> str:
    """Resolve the source stem without mistaking natural ``_partNN`` names for splits."""
    if "_" not in path.stem:
        return path.stem
    _, source_stem = path.stem.split("_", 1)
    meta = VideoMeta.read(path)
    if meta is not None:
        source = meta.source_path_obj()
        if source.is_file():
            return source.stem
    exact = _find_original_for_stem(source_stem, project_dir)
    if exact is not None:
        return exact.stem
    return SEGMENT_SUFFIX_RE.sub("", source_stem)


def auto_reindex_if_needed(config: AppConfig, ffprobe: str | None = None) -> bool:
    """检测 compressed_dir 是否有压缩视频缺少 .vindex，自动补全。

    返回 True 表示执行了 reindex，False 表示不需要或无法补全。
    """
    compressed_dir = config.compressed_dir
    if not compressed_dir.is_dir():
        return False

    t_scan = _time.perf_counter()
    # 找所有带数字前缀的压缩视频
    groups: dict[str, list[Path]] = {}
    for p in sorted(compressed_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        if "_" not in p.stem:
            continue
        prefix, stem_part = p.stem.split("_", 1)
        if not prefix.isdigit():
            continue
        orig_stem = _group_stem(p, config.project_dir)
        groups.setdefault(orig_stem, []).append(p)

    # 检查哪些没有 .vindex
    missing = [s for s in groups if not VideoIndex.read(s, compressed_dir)]
    scan_ms = (_time.perf_counter() - t_scan) * 1000
    if not missing:
        if groups:
            print(f"[startup] video indexes ok ({len(groups)} stems in {compressed_dir.name}, {scan_ms:.0f}ms)")
        return False

    # 显示重建提示（不清屏，避免在服务/CI 场景丢失日志）
    _W = 60
    print()
    print("=" * _W)
    print(t("task.reindex.rebuilding").center(_W))
    print(t("task.reindex.missing_header", {"missing": len(missing), "total": len(groups)}).center(_W))
    print(t("task.reindex.scan_time", {"ms": f"{scan_ms:.0f}"}).center(_W))
    print("=" * _W)
    print()
    t_rebuild = _time.perf_counter()
    run_reindex(config, ffprobe)
    rebuild_s = _time.perf_counter() - t_rebuild
    print()
    print("=" * _W)
    print(t("task.reindex.rebuild_done", {"seconds": f"{rebuild_s:.1f}"}).center(_W))
    print("=" * _W)
    # Brief pause so the banner is readable in interactive terminals
    _time.sleep(0.4)
    return True


def run_reindex(config: AppConfig, ffprobe: str | None = None) -> int:
    """Scan compressed_dir and rebuild .vindex files.

    Also writes .vmeta for any compressed file missing it.
    Returns count of .vindex files written.
    """
    if ffprobe is None:
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")

    compressed_dir = config.compressed_dir
    if not compressed_dir.is_dir():
        print(t("task.reindex.compressed_dir_missing", {"path": compressed_dir}))
        return 0

    # Group compressed files by original stem (strip _segNN suffix)
    groups: dict[str, list[Path]] = defaultdict(list)

    for p in sorted(compressed_dir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        if "_" not in p.stem:
            continue
        prefix, stem_part = p.stem.split("_", 1)
        if not prefix.isdigit():
            continue
        orig_stem = _group_stem(p, config.project_dir)
        groups[orig_stem].append(p)

    written_index = 0

    for orig_stem, comp_files in groups.items():
        # Try to find original source path
        source_path = None
        for p in comp_files:
            meta = VideoMeta.read(p)
            if meta is not None:
                src = meta.source_path_obj()
                if src.is_file():
                    source_path = src
                    break

        if source_path is None:
            source_path = _find_original_for_stem(orig_stem, config.project_dir)

        if source_path is None:
            print(t("task.reindex.skip_no_source", {"name": orig_stem}))
            continue

        # Sort compressed files by prefix index
        comp_files.sort(key=lambda p: int(p.stem.split("_", 1)[0]))

        seg_entries: list[SegmentEntry] = []
        source_dur = 0.0
        try:
            source_dur = get_duration_sec(source_path, ffprobe)
        except Exception:
            pass

        for cp in comp_files:
            idx_str = cp.stem.split("_", 1)[0]
            meta = VideoMeta.read(cp)
            if meta is not None and not (
                source_path.resolve() == meta.source_path_obj().resolve() and not meta.is_stale(source_path, cp)
            ):
                # Stale or mismatched sidecar: rebuild it from scratch below.
                meta = None
            if meta is not None and meta.split_info:
                # Verified legacy split slice (same source, fresh metadata):
                # trust its declared offsets, do not re-derive from position.
                si = meta.split_info
                seg_entries.append(
                    SegmentEntry(
                        index=idx_str,
                        filename=cp.name,
                        offset_sec=si.offset_sec,
                        duration_sec=meta.target_duration_sec,
                        segment_number=si.segment_index,
                        total_segments=si.total_segments,
                    )
                )
                continue
            # No verified split identity: a whole-file (or unverifiable) clip.
            # Never fabricate average offsets across same-stem files.
            tgt_dur = meta.target_duration_sec if meta is not None else 0.0
            if meta is None:
                try:
                    tgt_dur = get_duration_sec(cp, ffprobe)
                except Exception:
                    pass
                meta = VideoMeta.build(
                    source=source_path,
                    target=cp,
                    source_duration=source_dur,
                    target_duration=tgt_dur,
                )
                meta.write(cp)
            seg_entries.append(
                SegmentEntry(
                    index=idx_str,
                    filename=cp.name,
                    offset_sec=0.0,
                    duration_sec=tgt_dur,
                    segment_number=1,
                    total_segments=1,
                )
            )

        vindex = VideoIndex.build(
            source=source_path,
            source_duration=source_dur,
            segments=seg_entries,
        )
        vindex.write(compressed_dir)
        written_index += 1
        print(t("task.reindex.ok", {"name": orig_stem, "count": len(comp_files)}))

    print(t("task.reindex.done", {"count": written_index}))
    return written_index

"""Compression task — compress source videos (one file per original)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTS
from clio.compress import compress_video
from clio.config import AppConfig
from clio.log import timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.tasks._helpers import ClipRecord, _eta_line, _matches_selected_stem, _next_index, _selected_stems
from clio.utils import format_index, get_duration_sec, resolve_binary
from clio.vmeta import VMETA_EXT, SegmentEntry, VideoIndex, VideoMeta


def _safe_duration(path: Path, ffprobe: str) -> float:
    try:
        return get_duration_sec(path, ffprobe)
    except Exception:
        return 0.0


def _compress_settings_fingerprint(config: AppConfig) -> dict:
    return {
        "max_width": config.compress.max_width,
        "fps": config.compress.fps,
        "target_size_mb": config.compress.target_size_mb,
    }


def _meta_matches_settings(meta: VideoMeta, config: AppConfig) -> bool:
    """True when the stored compress settings still match the current config.

    A legacy vmeta without dropped settings is treated as matching (keeps old
    skip-behavior); any present key that differs forces a re-compress.
    """
    stored = meta.compress_settings or {}
    return all(stored.get(k, v) == v for k, v in _compress_settings_fingerprint(config).items())


def _meta_source_key(stem_part: str, meta: VideoMeta | None) -> str:
    """Distinct cache key for one compressed file's source identity.

    Prefer the resolved source path recorded in the .vmeta sidecar so videos
    that share a basename resolve to different entries; fall back to the
    bare basename only when no sidecar exists (legacy files).
    """
    if meta and meta.source_path:
        return f"src:{meta.source_path}"
    return f"stem:{stem_part}"


def _find_reusable(
    source: Path,
    existing_map: dict[str, list[tuple[int, Path, VideoMeta | None]]],
    config: AppConfig,
    ffprobe: str,
) -> tuple[int | None, Path | None, VideoMeta | None, str | None]:
    """Look up an existing compressed file for `source`.

    Iterates every candidate sharing the key so a stale file scanned first on
    disk can never hide a fresh sibling (iterdir order is not guaranteed).
    Returns (index, path, meta, reason) when a fresh, settings-matching output
    exists (reason is None), otherwise (None, None, None, reason) so the caller
    re-compresses, with the reason from the first rejected candidate.
    """
    resolved = str(source.resolve())
    first_reason: str | None = None
    for key in (f"src:{resolved}", f"stem:{source.stem}"):
        for use_idx, use_out, meta in existing_map.get(key, ()):
            reason: str | None = None
            if meta is not None:
                if meta.is_stale(source, use_out):
                    reason = f"源文件已变更，忽略旧压缩 {use_out.name}"
                elif not _meta_matches_settings(meta, config):
                    reason = f"压缩参数已变更，忽略旧压缩 {use_out.name}"
            # No sidecar: legacy file, only trust it if ffprobe can still read it.
            if reason is None:
                try:
                    if get_duration_sec(use_out, ffprobe) <= 0:
                        reason = f"{use_out.name} 无法读取时长，忽略旧压缩"
                except Exception:
                    reason = f"{use_out.name} 无法读取时长，忽略旧压缩"
            if reason is None:
                return use_idx, use_out, meta, None
            if first_reason is None:
                first_reason = reason
    return None, None, None, first_reason


def _prune_stale_siblings(
    source: Path,
    committed: Path,
    existing_map: dict[str, list[tuple[int, Path, VideoMeta | None]]],
    config: AppConfig,
    force: bool = False,
) -> None:
    """Drop stale same-source compressed files once a fresh one is committed.

    Mirrors analyze.py: a stale candidate with the same cache key must not
    shadow the fresh output on re-run (setdefault used to keep whichever the OS
    iterated first, so stale-first order re-encoded every run and the
    compressed dir grew without bound).

    `force=True` (overwrite mode) additionally drops every other candidate that
    provably belongs to the same source, keeping exactly one compressed output
    per source. Candidates without a sidecar (legacy) or recording a different
    source are never removed, so a same-basename video stays untouched.
    """
    resolved = str(source.resolve())
    refreshed: dict[str, list[tuple[int, Path, VideoMeta | None]]] = {}
    for key in (f"src:{resolved}", f"stem:{source.stem}"):
        for idx, path, meta in existing_map.get(key, ()):
            if path.resolve() == committed.resolve():
                refreshed.setdefault(key, []).append((idx, path, meta))
                continue
            if force:
                same_source = (
                    meta is not None
                    and meta.source_path is not None
                    and Path(meta.source_path).resolve() == Path(resolved)
                )
                if same_source:
                    print(f"[清理] 移除旧压缩 {path.name}（overwrite 已重新生成）")
                    path.unlink(missing_ok=True)
                    path.with_suffix(VMETA_EXT).unlink(missing_ok=True)
                    continue
            elif meta is not None and (meta.is_stale(source, path) or not _meta_matches_settings(meta, config)):
                print(f"[清理] 移除旧压缩 {path.name}（已被新压缩替代）")
                path.unlink(missing_ok=True)
                path.with_suffix(VMETA_EXT).unlink(missing_ok=True)
                continue
            refreshed.setdefault(key, []).append((idx, path, meta))
    existing_map.update(refreshed)


def _find_existing_slot(
    source: Path,
    existing_map: dict[str, list[tuple[int, Path, VideoMeta | None]]],
) -> tuple[int, Path] | None:
    """Return the current (index, output) for `source`, if any.

    Used by overwrite mode so a re-compress lands on the SAME slot instead of
    allocating a new index each run (which grew the dir with duplicate files).
    Resolves the deterministic earliest index for a stable name.
    """
    resolved = str(source.resolve())
    for key in (f"src:{resolved}", f"stem:{source.stem}"):
        candidates = existing_map.get(key)
        if candidates:
            idx, out, _ = min(candidates, key=lambda c: c[0])
            return idx, out
    return None


def _write_vindex(records: list[ClipRecord], config: AppConfig, ffprobe: str) -> None:
    from collections import defaultdict

    compressed_dir = config.compressed_dir
    groups: dict[Path, list[ClipRecord]] = defaultdict(list)
    for rec in records:
        if rec.compressed_path is not None:
            groups[rec.source_path].append(rec)

    for original, recs in groups.items():
        seg_entries: list[SegmentEntry] = []
        source_dur = 0.0
        for rec in recs:
            if rec.meta is None or rec.compressed_path is None:
                continue
            source_dur = rec.meta.source_duration_sec
            # New compresses are never physical segments.
            seg_entries.append(
                SegmentEntry(
                    index=format_index(rec.index, config.naming.index_width),
                    filename=rec.compressed_path.name,
                    offset_sec=0.0,
                    duration_sec=rec.meta.target_duration_sec,
                    segment_number=1,
                    total_segments=1,
                )
            )

        if not seg_entries:
            continue

        vindex = VideoIndex.build(
            source=original,
            source_duration=source_dur,
            segments=sorted(seg_entries, key=lambda s: s.segment_number),
        )
        vindex.write(compressed_dir)


def run_compress_all(
    config: AppConfig,
    tracker: ProgressTracker | None = None,
    single_file: Path | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    **kwargs: Any,
) -> list[ClipRecord]:
    resolve_binary(config.paths.ffmpeg, "ffmpeg")  # fail fast if missing
    ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")

    if single_file:
        videos = [single_file]
    else:
        from clio.tasks._video_loader import source_videos

        videos = source_videos(config)
        offline = [v for v in videos if not v.is_file()]
        if offline:
            print(f"[跳过] {len(offline)} 个离线/缺失视频（仍保留在 videos.json，可用 relink 修复）")
            for v in offline[:5]:
                print(f"  - {v}")
            if len(offline) > 5:
                print(f"  ... 另有 {len(offline) - 5} 个")
        videos = [v for v in videos if v.is_file()]
    if files is not None:
        selected = _selected_stems(files)
        videos = [v for v in videos if _matches_selected_stem(v, selected)]
    config.compressed_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: one compressed file per original.
    items: list[tuple[Path, Path]] = [(video, video) for video in videos]

    # Phase 2: build existing lookup (source path or stem -> (index, Path, VideoMeta))
    # Only include files >= 50KB to skip partially-written files from interrupted runs.
    # Match by resolved source path recorded in the .vmeta sidecar so two videos
    # sharing a basename never collide on the same existing file.
    MIN_VALID_SIZE = 50 * 1024
    existing_map: dict[str, list[tuple[int, Path, VideoMeta | None]]] = {}
    # Files ffprobe could not read during scan. A probe timeout/fork failure is
    # NOT proof of corruption: keep the artifact and only remove it after a
    # fresh replacement commits (GAP-P1-07).
    unverifiable: dict[str, list[Path]] = {}
    if config.analyze.skip_existing and config.compressed_dir.is_dir():
        for f in config.compressed_dir.iterdir():
            if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
                continue
            if f.stat().st_size < MIN_VALID_SIZE:
                continue
            try:
                dur = get_duration_sec(f, ffprobe)
                if dur <= 0:
                    raise ValueError("zero duration")
            except Exception:
                meta = VideoMeta.read(f)
                if meta and meta.source_path:
                    unverifiable.setdefault(str(Path(meta.source_path).resolve()), []).append(f)
                print(f"[保留] {f.name} 无法验证（ffprobe 异常），等新压缩成功后再清理")
                continue
            if "_" in f.stem:
                prefix, stem_part = f.stem.split("_", 1)
                if prefix.isdigit():
                    meta = VideoMeta.read(f)
                    key = _meta_source_key(stem_part, meta)
                    if key:
                        existing_map.setdefault(key, []).append((int(prefix), f, meta))

    # Phase 3: assign indices and compress each
    next_idx = _next_index(config.compressed_dir, config.naming.index_width)
    records: list[ClipRecord] = []
    state = ProcessingState(config.paths.output_dir)
    comp_label = f"run_compress_all（{len(items)} 个）"
    with timed(comp_label):
        completed = 0
        elapsed_total = 0.0
        for i, (original, source) in enumerate(items, start=1):
            label_name = source.name if source == original else f"{original.name} → {source.name}"

            # Reuse existing compressed file if it is fresh and still matches
            # (only in skip mode; overwrite mode always forces a re-compress).
            if not overwrite:
                use_idx, use_out, use_meta, reject_reason = _find_reusable(source, existing_map, config, ffprobe)
                if use_out is not None:
                    if tracker:
                        tracker.update(phase="compress", current=i, total=len(items), message=f"压缩 {source.name}...")
                        tracker.log(f"⏭️ 跳过 {label_name}（已存在 {use_out.name}）")
                    state.mark(original.stem, "compress", "skipped")
                    print(f"[跳过压缩] {label_name} (已存在: {use_out.name})")
                    records.append(
                        ClipRecord(index=use_idx, stem=use_out.stem, source_path=original, compressed_path=use_out)
                    )
                    continue
            else:
                reject_reason = "overwrite 模式强制重新压缩"

            # Leftover *_segNN files do NOT satisfy whole-file compress; continue to create
            # a new non-segment compressed file so the project can migrate forward.

            overwrite_slot = _find_existing_slot(source, existing_map) if overwrite else None
            if overwrite_slot is not None:
                use_idx, use_out = overwrite_slot
            else:
                use_idx = next_idx + completed
                use_out = (
                    config.compressed_dir / f"{format_index(use_idx, config.naming.index_width)}_{source.stem}.mp4"
                )
            if reject_reason:
                print(f"[重新压缩] {source.stem} {reject_reason}")
            if tracker:
                tracker.update(phase="compress", current=i, total=len(items), message=f"压缩 {source.name}...")
                tracker.log(f"▶ 压缩 {label_name}")
            print(_eta_line("压缩", i, len(items), label_name, completed, elapsed_total))
            t0 = time.monotonic()
            target_pre_existed = use_out.exists()
            try:
                if tracker:

                    def _on_progress(_sec: float, total_dur: float, _i: int = i, _name: str = label_name):
                        pct = int(_sec / total_dur * 100) if total_dur > 0 else 0
                        tracker.update(phase="compress", current=_i, total=len(items), message=f"压缩 {_name} ({pct}%)")

                    compress_video(source, use_out, config, progress_callback=_on_progress, cancel_event=cancel_event)
                else:
                    compress_video(source, use_out, config, cancel_event=cancel_event)
            except Exception:
                # Keep the last valid artifact when the encode fails: only the
                # atomic temp write is discarded, so a pre-existing target must
                # be left untouched (GAP-P1-07). A freshly-created partial
                # (non-atomic callers) is still cleaned up.
                if use_out.exists() and not target_pre_existed:
                    use_out.unlink(missing_ok=True)
                raise
            state.mark(original.stem, "compress", "done")
            elapsed_total += time.monotonic() - t0
            completed += 1

            # Always write non-split identity for new compresses.
            src_dur = _safe_duration(original, ffprobe)
            tgt_dur = _safe_duration(use_out, ffprobe)
            meta = VideoMeta.build(
                source=original,
                target=use_out,
                source_duration=src_dur,
                target_duration=tgt_dur,
                compress_settings={
                    "max_width": config.compress.max_width,
                    "fps": config.compress.fps,
                    "target_size_mb": config.compress.target_size_mb,
                },
                split_info=None,
            )
            meta.write(use_out)

            _prune_stale_siblings(source, use_out, existing_map, config, force=overwrite)

            # Now that a fresh valid output is committed, drop previously
            # unverifiable same-source artifacts that were retained on scan
            # (GAP-P1-07: defer deletion until a replacement exists).
            resolved_src = str(source.resolve())
            for stale in unverifiable.pop(resolved_src, []):
                if stale.resolve() != use_out.resolve():
                    print(f"[清理] 移除旧压缩 {stale.name}（新压缩已成功提交）")
                    stale.unlink(missing_ok=True)
                    stale.with_suffix(VMETA_EXT).unlink(missing_ok=True)

            records.append(
                ClipRecord(
                    index=use_idx,
                    stem=use_out.stem,
                    source_path=original,
                    compressed_path=use_out,
                    meta=meta,
                )
            )

    # 写 .vindex（每个原始文件一个，汇总所有分段）
    _write_vindex(records, config, ffprobe)
    return records

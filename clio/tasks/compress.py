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
from clio.vmeta import SegmentEntry, VideoIndex, VideoMeta


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
    existing_map: dict[str, tuple[int, Path, VideoMeta | None]],
    config: AppConfig,
    ffprobe: str,
) -> tuple[int | None, Path | None, VideoMeta | None]:
    """Look up an existing compressed file for `source`.

    Returns (index, path, meta) when a fresh, settings-matching output exists,
    otherwise (None, None, None) so the caller re-compresses.
    """
    resolved = str(source.resolve())
    for key in (f"src:{resolved}", f"stem:{source.stem}"):
        hit = existing_map.get(key)
        if hit is None:
            continue
        use_idx, use_out, meta = hit
        if meta is not None:
            if meta.is_stale(source, use_out):
                print(f"[重新压缩] {source.stem} 源文件已变更，忽略旧压缩 {use_out.name}")
                continue
            if not _meta_matches_settings(meta, config):
                print(f"[重新压缩] {source.stem} 压缩参数已变更，忽略旧压缩 {use_out.name}")
                continue
        # No sidecar: legacy file, only trust it if ffprobe can still read it.
        try:
            if get_duration_sec(use_out, ffprobe) <= 0:
                continue
        except Exception:
            continue
        return use_idx, use_out, meta
    return None, None, None


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
    existing_map: dict[str, tuple[int, Path, VideoMeta | None]] = {}
    if not overwrite and config.analyze.skip_existing and config.compressed_dir.is_dir():
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
                print(f"[清理] {f.name} 已损坏（ffprobe 无法读取），重新压缩")
                f.unlink(missing_ok=True)
                continue
            if "_" in f.stem:
                prefix, stem_part = f.stem.split("_", 1)
                if prefix.isdigit():
                    meta = VideoMeta.read(f)
                    key = _meta_source_key(stem_part, meta)
                    if key:
                        existing_map.setdefault(key, (int(prefix), f, meta))

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

            # Reuse existing compressed file if it is fresh and still matches.
            use_idx, use_out, use_meta = _find_reusable(source, existing_map, config, ffprobe)
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

            # Leftover *_segNN files do NOT satisfy whole-file compress; continue to create
            # a new non-segment compressed file so the project can migrate forward.

            use_idx = next_idx + completed
            use_out = config.compressed_dir / f"{format_index(use_idx, config.naming.index_width)}_{source.stem}.mp4"
            if tracker:
                tracker.update(phase="compress", current=i, total=len(items), message=f"压缩 {source.name}...")
                tracker.log(f"▶ 压缩 {label_name}")
            print(_eta_line("压缩", i, len(items), label_name, completed, elapsed_total))
            t0 = time.monotonic()
            try:
                if tracker:

                    def _on_progress(_sec: float, total_dur: float, _i: int = i, _name: str = label_name):
                        pct = int(_sec / total_dur * 100) if total_dur > 0 else 0
                        tracker.update(phase="compress", current=_i, total=len(items), message=f"压缩 {_name} ({pct}%)")

                    compress_video(source, use_out, config, progress_callback=_on_progress, cancel_event=cancel_event)
                else:
                    compress_video(source, use_out, config, cancel_event=cancel_event)
            except Exception:
                if use_out.exists():
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

"""Cut task — clip video segments based on plan."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTS
from clio.config import AppConfig
from clio.cut import cut_one, parse_time_range
from clio.i18n import t
from clio.identity import SEGMENT_SUFFIX_RE, legacy_segment_offset_sec, load_identity
from clio.log import format_duration, timed
from clio.plan_readiness import expand_index_keys
from clio.processing_state import ProcessingState
from clio.tasks._helpers import _eta_line
from clio.utils import (
    get_duration_sec,
    plan_day_stem,
    resolve_binary,
    safe_basename,
    sanitize_name,
    write_json_atomic,
    write_text_atomic,
)
from clio.vmeta import VideoMeta

_VIDEO_OUT_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm"}
CUT_BAK_SUFFIX = ".clio_bak"


class CutBackupConflictError(Exception):
    """Raised when a cut target and its backup coexist and no decision was given."""


def _legacy_segment_parts(stem: str) -> tuple[str, int] | None:
    match = SEGMENT_SUFFIX_RE.search(stem)
    if match is None or match.start() == 0:
        return None
    return stem[: match.start()], int(match.group(1))


def _indexed_files(directory: Path, raw_index: str, *, index_width: int, suffix: str | None = None) -> list[Path]:
    keys = expand_index_keys(raw_index, index_width=index_width)
    if not directory.is_dir() or not keys:
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.stem.split("_", 1)[0] in keys and (suffix is None or path.suffix.lower() == suffix)
    )


def resolve_cut_output_dir(config: AppConfig, day_label: str, output_dir: Path | None = None) -> Path:
    """Resolve where cut clips are written for a day.

    When *output_dir* is provided it must resolve under ``config.paths.output_dir``
    (R-033a). Raises ValueError if outside that root.
    """
    root = Path(config.paths.output_dir).expanduser().resolve()
    if output_dir is not None:
        out = Path(output_dir).expanduser().resolve()
        try:
            out.relative_to(root)
        except ValueError as e:
            raise ValueError(f"output_dir outside project output: {out}") from e
        return out
    day_dir = safe_basename(day_label)
    return (root / "cuts" / day_dir).resolve()


def list_existing_cut_videos(out_root: Path) -> list[str]:
    """Basenames of video files already present under a cut output directory."""
    if not out_root.is_dir():
        return []
    names: list[str] = []
    try:
        for p in sorted(out_root.iterdir()):
            if p.is_file() and p.suffix.lower() in _VIDEO_OUT_EXTS:
                names.append(p.name)
    except OSError:
        return []
    return names


def replace_file_safely(dest: Path, write_fn) -> None:
    """Write *dest* via rename-backup → write → delete-backup; restore on failure.

    Matches product rule for re-cuts: keep old file until the new one succeeds.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    bak: Path | None = None
    if dest.exists():
        bak = dest.with_name(dest.name + ".clio_bak")
        if bak.exists():
            bak.unlink()
        dest.replace(bak)
    try:
        write_fn(dest)
    except Exception:
        if bak is not None and bak.exists():
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            bak.replace(dest)
        elif dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        raise
    if bak is not None and bak.exists():
        bak.unlink()


def target_path_for_cut_bak(bak: Path) -> Path | None:
    """Map foo.mp4.clio_bak → foo.mp4. Returns None if not a cut backup name."""
    name = bak.name
    if not name.endswith(CUT_BAK_SUFFIX):
        return None
    target_name = name[: -len(CUT_BAK_SUFFIX)]
    if not target_name:
        return None
    return bak.with_name(target_name)


def list_orphaned_cut_backups(project_output_dir: Path) -> list[dict[str, str]]:
    """Find leftover *.clio_bak under output/cuts (interrupted re-cut).

    Each item: bak, target, day (relative day folder under cuts when known),
    and conflict=True when the target (a completed new cut) already exists.
    """
    cuts_root = Path(project_output_dir) / "cuts"
    if not cuts_root.is_dir():
        return []
    found: list[dict[str, str]] = []
    try:
        for bak in sorted(cuts_root.rglob(f"*{CUT_BAK_SUFFIX}")):
            if not bak.is_file():
                continue
            target = target_path_for_cut_bak(bak)
            if target is None:
                continue
            day = ""
            try:
                rel = bak.relative_to(cuts_root)
                if len(rel.parts) >= 2:
                    day = rel.parts[0]
            except ValueError:
                day = ""
            found.append(
                {
                    "bak": str(bak.resolve()),
                    "target": str(target.resolve()),
                    "day": day,
                    "name": target.name,
                    "conflict": "true" if target.exists() else "false",
                }
            )
    except OSError:
        return found
    return found


def restore_orphaned_cut_backup(bak: Path, *, keep_target: bool | None = None) -> dict[str, str]:
    """Resolve one cut backup.

    When the target file also exists, restoring the old file would discard a
    completed new cut, so an explicit decision is required (GAP-P1-06):
      - keep_target=True  -> delete the backup, keep the new target
      - keep_target=False -> delete the new target, restore the backup
      - keep_target=None  -> no decision; raise CutBackupConflictError
    """
    bak = Path(bak)
    target = target_path_for_cut_bak(bak)
    if target is None:
        raise ValueError(f"not a cut backup: {bak}")
    if not bak.is_file():
        raise FileNotFoundError(f"backup missing: {bak}")
    if target.exists() and keep_target is None:
        raise CutBackupConflictError(f"target and backup coexist; choose keep or restore: {target}")
    if target.exists():
        if keep_target:
            bak.unlink()
            return {"bak": str(bak), "target": str(target), "name": target.name, "kept": "target"}
        target.unlink()
    bak.replace(target)
    return {"bak": str(bak), "target": str(target), "name": target.name, "kept": "backup"}


def restore_orphaned_cut_backups(
    project_output_dir: Path,
    *,
    only: list[str] | None = None,
    keep_target: bool | None = None,
) -> dict[str, Any]:
    """Restore orphaned cut backups under output/cuts.

    only: optional list of absolute bak paths or target basenames to restore.
    keep_target: required decision when a target coexists with its backup.
        True keeps the new target (deletes the backup), False restores the old
        file; None leaves coexisting items unresolved and reports them as
        conflicts.
    """
    items = list_orphaned_cut_backups(project_output_dir)
    if only is not None:
        wanted = {str(Path(x)) for x in only} | set(only)
        items = [
            it
            for it in items
            if it["bak"] in wanted or it["target"] in wanted or it["name"] in wanted or Path(it["bak"]).name in wanted
        ]
    restored: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for it in items:
        try:
            restored.append(restore_orphaned_cut_backup(Path(it["bak"]), keep_target=keep_target))
        except CutBackupConflictError:
            conflicts.append(it)
        except OSError as e:
            errors.append({"bak": it["bak"], "error": str(e)})
    return {"restored": restored, "errors": errors, "conflicts": conflicts, "count": len(restored)}


def _compute_segment_offset(compressed_stem: str, comp_dir: Path, original_path: Path, ffprobe: str) -> float:
    """For a split segment, compute its start offset in the original video.
    Returns 0.0 if the file is not a segment or offset cannot be computed.
    """
    for p in comp_dir.glob(f"{compressed_stem}.*"):
        if p.suffix.lower() in VIDEO_EXTS:
            meta = VideoMeta.read(p)
            if meta and meta.split_info:
                return meta.split_info.offset_sec

    # 降级：原有估算逻辑
    parts = _legacy_segment_parts(compressed_stem.split("_", 1)[1] if "_" in compressed_stem else compressed_stem)
    if parts is None:
        return 0.0
    prefix, seg_num = parts
    if prefix.casefold() == original_path.stem.casefold():
        return 0.0
    prefix = prefix.lower()
    total = 0
    for p in sorted(comp_dir.iterdir()):
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        segment_parts = _legacy_segment_parts(p.stem.split("_", 1)[1] if "_" in p.stem else p.stem)
        if segment_parts is not None and segment_parts[0].lower() == prefix:
            total = max(total, segment_parts[1])
    if total <= 1:
        return 0.0
    try:
        dur = get_duration_sec(original_path, ffprobe)
    except Exception:
        return 0.0
    return round((seg_num - 1) * dur / total, 1)


def run_cut_all(
    config: AppConfig,
    day_label: str = "day1",
    output_dir: Path | None = None,
    reencode: bool = False,
    source: str = "compressed",
    cancel_event: threading.Event | None = None,
    overwrite: bool = True,
    tracker: Any | None = None,
) -> list[dict]:
    """根据 plan 按时间区间裁剪视频片段。

    读取 plans/<day_label>_plan.json，对 sequence[] 中每个 segment
    用 ffmpeg 从对应压缩视频中裁剪 [use_timeline] 段。

    输出：剪好的 clip 文件 + 对应 texts JSON + manifest.md。
    overwrite=False 时若输出目录已有视频则抛 FileExistsError（不改写）。
    overwrite=True 时对每个目标文件先备份再写，成功后删备份。
    """
    plan_path = config.plans_dir / f"{plan_day_stem(day_label)}_plan.json"
    if not plan_path.is_file():
        raise FileNotFoundError(t("task.cut.plan_missing", {"path": plan_path}))

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    seq = plan.get("sequence", [])
    if not seq:
        print(t("task.cut.plan_empty_sequence", {"name": plan_path.name}))
        return []
    if tracker is not None:
        tracker.update(phase="cut", current=0, total=len(seq), message=t("task.cut.preparing", {"count": len(seq)}))

    out_root = resolve_cut_output_dir(config, day_label, output_dir)
    existing = list_existing_cut_videos(out_root)
    if existing and not overwrite:
        raise FileExistsError(t("task.cut.output_exists", {"count": len(existing), "path": out_root}))
    out_root.mkdir(parents=True, exist_ok=True)
    ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
    ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
    comp_dir = config.compressed_dir
    source_label = str(comp_dir if source == "compressed" else (config.project_dir or ""))

    print(t("task.cut.header_plan", {"name": plan_path.name, "count": len(seq)}))
    print(t("task.cut.header_output", {"path": out_root}))
    print(t("task.cut.header_source", {"source": source, "label": source_label}))

    state = ProcessingState(config.paths.output_dir)

    def _orig_stem_from_path(video_path: Path) -> str:
        stem = video_path.stem
        if source == "original":
            return stem
        if "_" in stem:
            stem = stem.split("_", 1)[1]
        return SEGMENT_SUFFIX_RE.sub("", stem)

    def _resolve_video_path(idx: str) -> Path | None:
        if source == "compressed":
            candidates = [
                path
                for path in _indexed_files(comp_dir, idx, index_width=config.naming.index_width)
                if path.suffix.lower() in VIDEO_EXTS
            ]
            return candidates[0] if candidates else None
        else:
            comp_candidates = [
                path
                for path in _indexed_files(comp_dir, idx, index_width=config.naming.index_width)
                if path.suffix.lower() in VIDEO_EXTS
            ]
            if not comp_candidates:
                return None
            compressed = comp_candidates[0]

            # 优先：读 .vmeta 直接拿原始路径（O(1)，支持任意目录层级）
            meta = VideoMeta.read(compressed)
            if meta is not None:
                src = meta.source_path_obj()
                if src.is_file():
                    return src

            # 降级：regex 反解 + videos.json。先按完整 stem 匹配，避免把自然文件名
            # 末尾的 _partNN 误认为历史分段后缀。
            suffix = compressed.stem.split("_", 1)[1].lower()
            from clio.tasks._video_loader import source_videos

            videos = source_videos(config)
            for p in videos:
                if p.stem.lower() == suffix:
                    return p
            orig_stem = SEGMENT_SUFFIX_RE.sub("", suffix)
            if orig_stem != suffix:
                for p in videos:
                    if p.stem.lower() == orig_stem:
                        return p
            return None

    clips: list[dict] = []
    completed = 0
    elapsed_total = 0.0

    with timed(t("task.cut.timed_label", {"day": day_label, "count": len(seq)})):
        for i, seg in enumerate(seq, start=1):
            if cancel_event and cancel_event.is_set():
                print(t("task.cut.cancelled", {"index": i}))
                break
            idx = seg.get("index", "")
            title = seg.get("title", "").strip()
            timeline = (seg.get("use_timeline") or "").strip()
            if not idx or not timeline:
                print(t("task.cut.skip_no_index", {"index": i}))
                if tracker is not None:
                    tracker.next(message=t("task.cut.skip_no_index_tracker", {"index": i}))
                continue

            video_path = _resolve_video_path(idx)
            if video_path is None:
                src = "compressed" if source != "original" else "original"
                print(t("task.cut.skip_video_missing", {"index": idx, "source": src, "title": seg.get("title", "")}))
                if tracker is not None:
                    tracker.next(message=t("task.cut.skip_video_missing_tracker", {"index": i}))
                continue

            try:
                start, end = parse_time_range(timeline)
            except ValueError as e:
                print(t("task.cut.skip_bad_time", {"timeline": timeline, "detail": e}))
                orig_stem = _orig_stem_from_path(video_path) if video_path else ""
                if orig_stem:
                    state.mark(orig_stem, "cut", "skipped")
                if tracker is not None:
                    tracker.next(message=t("task.cut.skip_bad_time_tracker", {"index": i}))
                continue
            plan_start, plan_end = start, end

            if end <= start:
                print(t("task.cut.skip_invalid_range", {"start": start, "end": end, "title": seg.get("title", "")}))
                orig_stem = _orig_stem_from_path(video_path) if video_path else ""
                if orig_stem:
                    state.mark(orig_stem, "cut", "skipped")
                if tracker is not None:
                    tracker.next(message=t("task.cut.skip_invalid_range_tracker", {"index": i}))
                continue

            try:
                from clio.utils import get_duration_sec
                from clio.utils import resolve_binary as _resolve_binary

                probe = _resolve_binary(ffprobe or "", "ffprobe")
                media_dur = get_duration_sec(video_path, probe)
                if end > media_dur:
                    print(t("task.cut.clamp_end", {"end": end, "duration": media_dur}))
                    end = media_dur
            except Exception:
                pass
            if end <= start:
                print(t("task.cut.skip_beyond_duration", {"start": start, "end": end, "title": seg.get("title", "")}))
                orig_stem = _orig_stem_from_path(video_path) if video_path else ""
                if orig_stem:
                    state.mark(orig_stem, "cut", "skipped")
                if tracker is not None:
                    tracker.next(message=t("task.cut.skip_beyond_duration_tracker", {"index": i}))
                continue

            # Apply segment offset for original source with legacy split videos
            offset = 0.0
            if source == "original":
                # Prefer media_identity via legacy gate (new identities always 0)
                text_json_paths = _indexed_files(
                    config.texts_dir, idx, index_width=config.naming.index_width, suffix=".json"
                )
                if text_json_paths:
                    try:
                        data = json.loads(text_json_paths[0].read_text(encoding="utf-8"))
                        identity = load_identity(data)
                        offset = legacy_segment_offset_sec(identity)
                    except Exception:
                        pass
                # Fall back to vmeta-based computation for v1 files
                if offset == 0.0:
                    comp_candidates = [
                        path
                        for path in _indexed_files(comp_dir, idx, index_width=config.naming.index_width)
                        if path.suffix.lower() in VIDEO_EXTS
                    ]
                    if comp_candidates:
                        stem = comp_candidates[0].stem
                        offset = _compute_segment_offset(stem, comp_dir, video_path, ffprobe)
                if offset:
                    start += offset
                    end += offset

            clip_stem = f"{safe_basename(idx, max_len=30)}_{sanitize_name(title, max_len=30)}_seg_{i:03d}"
            clip_path = out_root / f"{clip_stem}.mp4"

            print(_eta_line(t("task.cut.eta_label"), i, len(seq), clip_stem, completed, elapsed_total))
            t0 = time.monotonic()
            try:

                def _write_clip(dest: Path) -> None:
                    cut_one(
                        video_path,
                        dest,
                        start,
                        end,
                        ffmpeg,
                        reencode=reencode,
                        cancel_event=cancel_event,
                    )

                replace_file_safely(clip_path, _write_clip)
                state.mark(_orig_stem_from_path(video_path), "cut", "done")
            except Exception:
                state.mark(_orig_stem_from_path(video_path), "cut", "error")
                raise
            elapsed_total += time.monotonic() - t0
            completed += 1

            # 复制对应的 texts JSON，并始终写 sidecar 标明片段来源
            text_json = None
            cut_info = {
                "seg_index": i,
                "timeline": timeline,
                "plan_start_sec": round(plan_start, 2),
                "plan_end_sec": round(plan_end, 2),
                "start_sec": round(start, 2),
                "end_sec": round(end, 2),
            }
            matching_texts = _indexed_files(
                config.texts_dir, idx, index_width=config.naming.index_width, suffix=".json"
            )
            if matching_texts:
                text_path = matching_texts[0]
                data = json.loads(text_path.read_text(encoding="utf-8"))
            else:
                data = {}
            data["_cut_info"] = cut_info
            dst = out_root / f"{clip_stem}.json"
            replace_file_safely(dst, lambda p, d=data: write_json_atomic(p, d))
            text_json = dst.name
            print(f"  -> texts: {dst.name}")

            clips.append(
                {
                    "seg_index": i,
                    "video_index": idx,
                    "title": title,
                    "timeline": timeline,
                    "plan_start_sec": round(plan_start, 2),
                    "plan_end_sec": round(plan_end, 2),
                    "start_sec": round(start, 2),
                    "end_sec": round(end, 2),
                    "duration_sec": round(end - start, 2),
                    "output_file": clip_path.name,
                    "text_file": text_json or "",
                }
            )
            if tracker is not None:
                tracker.next(message=t("task.cut.segment_done", {"index": i, "total": len(seq)}))

    manifest_path = out_root / "manifest.md"
    lines = [
        t("cut.manifest_title", {"title": plan.get("day_title", day_label)}),
        "",
        t("plan.md_theme", {"theme": plan.get("theme", "")}),
        t("plan.md_estimated", {"seconds": plan.get("total_estimated_sec", "")}),
        t("cut.manifest_output", {"path": out_root}),
        "",
        t("cut.manifest_header"),
        "|---|------|------|---------|------|---------|-------|",
    ]
    for c in clips:
        lines.append(
            f"| {c['seg_index']} | {c['video_index']} | {c['title']} "
            f"| {c['timeline']} | {format_duration(c['duration_sec'])} "
            f"| {c['output_file']} | {c['text_file'] or '-'} |"
        )
    write_text_atomic(manifest_path, "\n".join(lines) + "\n")
    print(f"  -> manifest: {manifest_path.name}")
    print(t("task.cut.done", {"count": len(clips), "path": out_root}))
    return clips

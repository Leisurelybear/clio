"""Analysis task — AI analysis of compressed videos."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTS
from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import analyze_video
from clio.analyze_windows import (
    build_analyze_windows,
    cleanup_analyze_windows_dir,
    merge_window_analyses,
    shift_analysis_times,
    slice_window_video,
)
from clio.config import AppConfig
from clio.identity import (
    SEGMENT_SUFFIX_RE,
    _identity_to_dict,
    is_legacy_split_path,
    load_identity,
    prefer_canonical_compressed,
    resolve_identity,
)
from clio.log import format_duration, timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.prompt_overrides import resolve_prompt_template
from clio.prompts import ANALYZE_PROMPT
from clio.schema import add_schema_version
from clio.tasks._helpers import (
    ClipRecord,
    _build_stem,
    _clip_records_from_csv,
    _matches_selected_stem,
    _merge_summary_records,
    _selected_stems,
    _write_csv,
    _write_text_file,
)
from clio.tasks.cover import extract_cover_frame
from clio.tasks.transcript_align import attach_transcript_to_analysis
from clio.utils import get_duration_sec, resolve_binary, write_json_atomic
from clio.vmeta import VideoIndex


def _analysis_lineage_fingerprint(config: AppConfig, task_prompts: dict[str, str] | None = None) -> str:
    """Fingerprint of everything that can change a cached analysis result.

    Changing the analyze prompt (builtin, override file, or runtime task prompt)
    or the video_analyze provider/model invalidates the skip_existing cache so
    users never silently keep results produced under an older lineage.
    """
    try:
        task = config.ai.tasks.get("video_analyze")
        provider = getattr(task, "provider", "") if task else ""
        model = getattr(task, "model", "") if task else ""
    except Exception:
        provider, model = "", ""
    try:
        prompt = resolve_prompt_template("video_analyze", ANALYZE_PROMPT, config, task_prompts=task_prompts)
    except Exception:
        prompt = ANALYZE_PROMPT
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "max_analyze_duration_min": getattr(config.analyze, "max_analyze_duration_min", 0),
            "max_tokens": getattr(config.ai, "max_tokens", None),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_stem_to_path(project_dir: Path | None = None) -> dict[str, Path]:
    """Build a one-time {stem_lower: path} map from videos.json."""
    from clio.tasks._video_loader import load_selected_videos, stem_to_path_map

    return stem_to_path_map(load_selected_videos(project_dir))


def _resolve_original(
    compressed_stem: str,
    stem_cache: dict[str, Path] | None = None,
    project_dir: Path | None = None,
) -> Path | None:
    """Resolve original video path from a compressed file stem via videos.json cache."""
    if "_" not in compressed_stem:
        orig_stem = compressed_stem
    else:
        _, orig_stem = compressed_stem.split("_", 1)

    def _try_find(stem: str) -> Path | None:
        key = stem.lower()
        if stem_cache is not None:
            return stem_cache.get(key)
        from clio.tasks._video_loader import load_selected_videos

        for p in load_selected_videos(project_dir):
            if p.stem.lower() == key:
                return p
        return None

    result = _try_find(orig_stem)
    if result is not None:
        return result

    m = SEGMENT_SUFFIX_RE.search(orig_stem)
    if m:
        return _try_find(SEGMENT_SUFFIX_RE.sub("", orig_stem))
    return None


def _analyze_with_optional_windows(
    *,
    compressed: Path,
    duration_sec: float,
    config: AppConfig,
    token_store: FileTokenUsageStore,
    cancel_event: threading.Event | None,
    effective_context: str | None,
    task_prompts: dict[str, str] | None,
) -> dict:
    """Run Gemini analyze: legacy split files stay single-call; else use windows."""

    def _one_call(video_path: str) -> dict:
        try:
            return analyze_video(
                video_path,
                config,
                progress_callback=lambda msg: None,
                token_store=token_store,
                cancel_event=cancel_event,
                context_override=effective_context,
                task_prompts=task_prompts,
            )
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("分析被用户取消") from exc
            raise

    if is_legacy_split_path(compressed):
        return _one_call(str(compressed))

    w_max = int(getattr(config.analyze, "window_max_min", 15) or 15)
    overlap = int(getattr(config.analyze, "window_overlap_sec", 20) or 0)
    windows = build_analyze_windows(duration_sec, w_max, overlap)

    if len(windows) <= 1:
        analysis = _one_call(str(compressed))
        return merge_window_analyses([(windows[0], analysis)], overlap) if windows else analysis

    ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
    ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
    # Per-clip temp dir so concurrent max_workers cannot wipe sibling slices.
    dest = config.paths.output_dir / ".analyze_windows" / compressed.stem
    dest.mkdir(parents=True, exist_ok=True)
    partials: list = []
    try:
        for w in windows:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("分析被用户取消")
            slice_path = slice_window_video(
                source=compressed,
                window=w,
                dest_dir=dest,
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
                cancel_event=cancel_event,
            )
            try:
                part = _one_call(str(slice_path))
                part = shift_analysis_times(part, w.start_sec)
                partials.append((w, part))
            finally:
                slice_path.unlink(missing_ok=True)
    except Exception:
        cleanup_analyze_windows_dir(dest)
        raise

    return merge_window_analyses(partials, overlap)


def _process_video_item(
    compressed: Path,
    original: Path,
    idx_str: str,
    config: AppConfig,
    token_store: FileTokenUsageStore,
    overwrite: bool,
    tracker: ProgressTracker | None,
    state: ProcessingState,
    error_count: list[int],
    cancel_event: threading.Event | None = None,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> ClipRecord | None:
    idx_val = int(idx_str)

    # Probe compressed file duration once (cheaper than probing original in _write_csv)
    duration_sec = 0.0
    try:
        ffprobe_bin = resolve_binary(config.paths.ffprobe, "ffprobe")
        duration_sec = get_duration_sec(compressed, ffprobe_bin)
    except Exception:
        pass
    if duration_sec <= 0:
        # Fallback: probe original so windowing can still run for long clips.
        try:
            ffprobe_bin = resolve_binary(config.paths.ffprobe, "ffprobe")
            duration_sec = get_duration_sec(original, ffprobe_bin)
        except Exception:
            pass

    existing = sorted(config.texts_dir.glob(f"{idx_str}_*.json"))
    json_path = None
    analysis = None
    # Lineage or source mismatches: keep the old files until a fresh analysis
    # has been committed, so a failed re-run never loses the previous result.
    stale_candidates: list[Path] = []
    if not overwrite and config.analyze.skip_existing and existing:
        candidate = existing[0]
        try:
            existing_data = json.loads(candidate.read_text(encoding="utf-8"))
            if existing_data.get("source_file", "") == original.name:
                lineage_ok = existing_data.get("_lineage") == _analysis_lineage_fingerprint(config, task_prompts)
                if lineage_ok:
                    json_path = candidate
                    analysis = existing_data
                elif existing_data.get("_lineage") is None:
                    # Pre-lineage cache: accept and stamp the current lineage so
                    # future prompt/model changes invalidate it.
                    json_path = candidate
                    analysis = existing_data
                    analysis["_lineage"] = _analysis_lineage_fingerprint(config, task_prompts)
                    write_json_atomic(candidate, analysis)
                    if tracker:
                        tracker.log(f"✓ {candidate.name} 血缘未记录，已标记当前版本")
                else:
                    print(f"  [覆盖] 提示词/模型已变更，将重新分析 {candidate.name}")
                    stale_candidates.append(candidate)
            else:
                print(f"  [覆盖] {candidate.name} 的 source_file 不匹配，将重新分析")
                stale_candidates.append(candidate)
        except (json.JSONDecodeError, OSError):
            pass
    if json_path:
        text_path = json_path.with_suffix(".txt")
        if tracker:
            tracker.next(message=f"跳过 {compressed.name}...")
            tracker.log(f"跳过 {compressed.name}（已分析）")
        state.mark(original.stem, "analyze", "skipped")
        print(f"[跳过分析] {compressed.name} (已存在: {json_path.name})")
        identity = (
            resolve_identity(compressed, idx_str, project_dir=config.project_dir)
            if analysis is None
            else (load_identity(analysis) or resolve_identity(compressed, idx_str, project_dir=config.project_dir))
        )
        return ClipRecord(
            index=idx_val,
            stem=json_path.stem,
            source_path=original,
            compressed_path=compressed,
            text_path=text_path,
            analysis=analysis,
            duration_sec=duration_sec,
            identity=identity,
        )

    if duration_sec <= 0 and not is_legacy_split_path(compressed):
        print(f"  [跳过] {compressed.name} 无法读取时长，跳过分析")
        if tracker:
            tracker.next(message=f"跳过 {compressed.name}（无时长）")
            tracker.log(f"跳过 {compressed.name}（无法读取时长）")
        state.mark(original.stem, "analyze", "skipped")
        return None

    # Whole-clip quota guard applies before both single-call and windowed analysis.
    max_min = config.analyze.max_analyze_duration_min
    if max_min > 0 and duration_sec > max_min * 60:
        print(f"  [跳过] {compressed.name} 时长 {format_duration(duration_sec)} 超过限制 {max_min} 分钟")
        if tracker:
            tracker.next(message=f"跳过 {compressed.name}（超长）")
            tracker.log(f"跳过 {compressed.name}（超长 {format_duration(duration_sec)}）")
        state.mark(original.stem, "analyze", "skipped")
        return None

    # Check cancel_event before starting analysis
    if cancel_event and cancel_event.is_set():
        print(f"  [取消] 分析 {compressed.name} 被取消")
        state.mark(original.stem, "analyze", "cancelled")
        return None

    print(f"  [{compressed.name}] 分析中...")
    t0 = time.monotonic()
    try:
        from clio.gpmf import merge_telemetry_into_context

        effective_context = merge_telemetry_into_context(
            context_override,
            original,
            use_gpmf=bool(getattr(config.analyze, "use_gpmf", False)),
        )
        analysis = _analyze_with_optional_windows(
            compressed=compressed,
            duration_sec=duration_sec,
            config=config,
            token_store=token_store,
            cancel_event=cancel_event,
            effective_context=effective_context,
            task_prompts=task_prompts,
        )
    except InterruptedError:
        if cancel_event is not None:
            cancel_event.set()
        print(f"  [取消] 分析 {compressed.name} 被取消")
        state.mark(original.stem, "analyze", "cancelled")
        if tracker:
            tracker.next(message=f"取消 {compressed.name}")
            tracker.log(f"分析 {compressed.name} 被用户取消")
        return None
    except Exception as e:
        elapsed_total = time.monotonic() - t0
        print(f"  [错误] 分析 {compressed.name} 失败: {e}（耗时 {format_duration(elapsed_total)}）")
        state.mark(original.stem, "analyze", "error")
        error_count[0] += 1
        if tracker:
            tracker.next(message=f"失败 {compressed.name}")
            tracker.log(f"分析 {compressed.name} 失败: {e}")
        return None

    analysis["index"] = idx_val
    analysis["source_file"] = original.name
    analysis["compressed_file"] = compressed.name  # stable lookup key for videos.py
    identity = resolve_identity(compressed, idx_str, project_dir=config.project_dir)
    add_schema_version(analysis)
    analysis["_lineage"] = _analysis_lineage_fingerprint(config, task_prompts)
    analysis["media_identity"] = _identity_to_dict(identity)
    attach_transcript_to_analysis(config, analysis)

    stem = _build_stem(idx_val, analysis.get("title", original.stem), config)
    final_text = config.texts_dir / f"{stem}.txt"
    json_path = config.texts_dir / f"{stem}.json"
    cover_path = extract_cover_frame(config, compressed, analysis, stem)
    if cover_path is not None:
        analysis["cover_file"] = str(cover_path.relative_to(config.paths.output_dir))
    _write_text_file(final_text, analysis, original, compressed)
    write_json_atomic(json_path, analysis)

    # Old results were only kept until here; drop them now that fresh output
    # is committed, so the next glob still resolves this video's own JSON.
    for stale in stale_candidates:
        if stale.name != json_path.name:
            stale.with_suffix(".txt").unlink(missing_ok=True)
            stale.unlink(missing_ok=True)

    state.mark(original.stem, "analyze", "done")
    if tracker:
        tracker.next(message=f"完成 {compressed.name}")
        tracker.log(f"分析 {original.stem} ✓")
    print(f"  -> {final_text.name}")
    return ClipRecord(
        index=idx_val,
        stem=stem,
        source_path=original,
        compressed_path=compressed,
        text_path=final_text,
        analysis=analysis,
        duration_sec=duration_sec,
        identity=identity,
    )


def run_analyze_all(
    config: AppConfig,
    tracker: ProgressTracker | None = None,
    single_file: Path | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    **kwargs: Any,
) -> list[ClipRecord]:
    """Analyze already-compressed videos using AI (compress step must precede this).

    Scans compressed_dir for existing *.mp4 files and analyzes each.
    For single_file (an original video), finds the matching compressed file first.
    """
    config.texts_dir.mkdir(parents=True, exist_ok=True)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    records: list[ClipRecord] = []

    # Build one-time stem→path cache to avoid per-video rglob
    stem_cache = _build_stem_to_path(config.project_dir)

    def _list_compressed(d: Path) -> list[Path]:
        paths = sorted(p for p in d.iterdir() if p.suffix.lower() in VIDEO_EXTS and p.is_file())
        return prefer_canonical_compressed(paths)

    if single_file:
        items: list[tuple[Path, Path, str]] = []

        # 优先读 .vindex（O(1)，含所有分段）
        vindex = VideoIndex.read(single_file.stem, config.compressed_dir)
        if vindex is not None:
            comp_paths = vindex.compressed_paths(config.compressed_dir)
            if not comp_paths:
                print(f"[错误] .vindex 存在但压缩文件缺失: {single_file.name}，请重新压缩")
                return []
            for comp in comp_paths:
                idx_str = comp.stem.split("_", 1)[0]
                items.append((comp, single_file, idx_str))
        else:
            # 降级：原有 stem 匹配，修复只取 candidates[0] 的 bug
            candidates = [
                p for p in _list_compressed(config.compressed_dir) if single_file.stem.lower() in p.stem.lower()
            ]
            if not candidates:
                print(f"[错误] 未找到 {single_file.name} 对应的压缩文件，请先运行压缩步骤")
                return []
            for comp in candidates:
                idx_str = comp.stem.split("_", 1)[0]
                items.append((comp, single_file, idx_str))
    else:
        items = []
        for p in _list_compressed(config.compressed_dir):
            parts = p.stem.split("_", 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            orig_path = _resolve_original(p.stem, stem_cache, config.project_dir)
            if orig_path is None:
                print(f"[警告] 找不到 {p.name} 对应的原始视频，跳过")
                continue
            idx_str = parts[0]
            items.append((p, orig_path, idx_str))

    if files is not None:
        selected = _selected_stems(files)
        items = [
            it for it in items if _matches_selected_stem(it[0], selected) or _matches_selected_stem(it[1], selected)
        ]

    if not items:
        print(f"[错误] 压缩目录为空或无法匹配: {config.compressed_dir}，请先运行压缩步骤")
        return []

    total = len(items)
    print(f"待分析视频: {total} 个（压缩目录: {config.compressed_dir}）")
    state = ProcessingState(config.paths.output_dir)

    with timed(f"run_analyze_all（{total} 个）"):
        error_count: list[int] = [0]
        max_workers = config.analyze.max_workers

        if tracker:
            tracker.update(phase="analyze", total=total, current=0)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            # Check cancel_event before submitting any tasks
            if cancel_event and cancel_event.is_set():
                print("[取消] 分析步骤被用户终止")
                return records

            # Submit a bounded number of futures (avoid queueing all at once)
            batch_size = min(max_workers, len(items))
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                # Check cancel_event before each batch submission
                if cancel_event and cancel_event.is_set():
                    print("[取消] 取消剩余任务提交")
                    break

                batch_futures: list[Future] = []
                for compressed, original, idx_str in batch:
                    # Check cancel_event before each submission
                    if cancel_event and cancel_event.is_set():
                        print("[取消] 取消剩余任务提交")
                        break

                    f = pool.submit(
                        _process_video_item,
                        compressed,
                        original,
                        idx_str,
                        config,
                        token_store,
                        overwrite,
                        tracker,
                        state,
                        error_count,
                        cancel_event,
                        context_override,
                        task_prompts,
                    )
                    batch_futures.append(f)

                # Process completed futures from this batch before submitting next
                for future in as_completed(batch_futures):
                    try:
                        result = future.result()
                        if result is not None:
                            records.append(result)
                    except Exception as e:
                        print(f"  [错误] 任务执行失败: {e}")
                        error_count[0] += 1

                # Cancel remaining pending futures if cancelled mid-batch
                if cancel_event and cancel_event.is_set():
                    print("[取消] 取消未开始任务")
                    for f in batch_futures:
                        if not f.done():
                            f.cancel()
                    break

    records.sort(key=lambda r: r.index)
    existing = _clip_records_from_csv(config.summary_csv)
    to_write = _merge_summary_records(existing, records)
    _write_csv(config.summary_csv, to_write, config)
    print(f"\nCSV 已保存: {config.summary_csv}")

    completed = len(records)
    failed = error_count[0]
    if failed > 0:
        raise RuntimeError(f"AI 分析未完整完成（{completed} 个成功，{failed} 个失败），请检查 API Key 和网络连接")
    return records

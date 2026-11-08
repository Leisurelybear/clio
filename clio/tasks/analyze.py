"""Analysis task — AI analysis of compressed videos."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTS
from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import _prompt_context_parts, analyze_video
from clio.analyze_windows import (
    build_analyze_windows,
    cleanup_analyze_windows_dir,
    merge_window_analyses,
    shift_analysis_times,
    slice_window_video,
)
from clio.config import AppConfig
from clio.i18n import t
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
from clio.prompts import ANALYZE_PROMPT, TRANSCRIPT_CONTEXT
from clio.schema import add_schema_version
from clio.tasks._helpers import (
    ClipRecord,
    _build_stem,
    _clip_records_from_csv,
    _matches_selected_stem,
    _merge_summary_records,
    _selected_stems,
    _task_lineage_settings,
    _write_csv,
    _write_text_file,
)
from clio.tasks.cover import extract_cover_frame
from clio.tasks.transcript_align import attach_transcript_to_analysis
from clio.utils import get_duration_sec, resolve_binary, run_probe, write_json_atomic
from clio.vmeta import VideoIndex

_CAPTURE_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)")


def _capture_date_key(value: str) -> str:
    match = _CAPTURE_DATE_RE.search(value)
    if not match:
        return ""
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d").date().isoformat()
    except ValueError:
        return ""


def _original_capture_date(original: Path, ffprobe: str) -> str:
    if ffprobe and original.is_file():
        try:
            result = run_probe(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format_tags=creation_time:stream_tags=creation_time",
                    "-of",
                    "json",
                    str(original),
                ],
                max_output_bytes=16384,
            )
            probe_data = json.loads(result.stdout or "{}")
            format_tags = (probe_data.get("format") or {}).get("tags") or {}
            if isinstance(format_tags, dict):
                for key, value in format_tags.items():
                    if key.lower() == "creation_time":
                        date_key = _capture_date_key(str(value))
                        if date_key:
                            return date_key
            for stream in probe_data.get("streams") or []:
                tags = stream.get("tags") or {}
                if isinstance(tags, dict):
                    for key, value in tags.items():
                        if key.lower() == "creation_time":
                            date_key = _capture_date_key(str(value))
                            if date_key:
                                return date_key
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return _capture_date_key(original.stem)


def _build_day_labels(config: AppConfig, items: list[tuple[Path, Path, str]]) -> dict[str, str]:
    """Map each source path to a stable relative day label for this project."""
    if len(items) <= 1:
        return {str(original.resolve()): "day1" for _, original, _ in items}
    try:
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
    except OSError:
        ffprobe = ""
    dates = {str(original.resolve()): _original_capture_date(original, ffprobe) for _, original, _ in items}
    known_dates = sorted({date for date in dates.values() if date})
    labels = {date: f"day{index}" for index, date in enumerate(known_dates, start=1)}
    return {path: labels.get(date, "day1") for path, date in dates.items()}


def _analysis_lineage_fingerprint(
    config: AppConfig,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
    transcript_context: str = "",
) -> str:
    """Fingerprint of everything that can change a cached analysis result.

    Changing the analyze prompt (builtin, override file, or runtime task prompt)
    or the video_analyze provider/model invalidates the skip_existing cache so
    users never silently keep results produced under an older lineage.
    """
    try:
        prompt = resolve_prompt_template("video_analyze", ANALYZE_PROMPT, config, task_prompts=task_prompts)
    except Exception:
        prompt = ANALYZE_PROMPT
    payload = json.dumps(
        {
            "task": _task_lineage_settings(config, "video_analyze"),
            "prompt": prompt,
            "max_analyze_duration_min": getattr(config.analyze, "max_analyze_duration_min", 0),
            "window_max_min": int(getattr(config.analyze, "window_max_min", 15) or 15),
            "window_overlap_sec": int(getattr(config.analyze, "window_overlap_sec", 20) or 0),
            "compress": {
                "codec": getattr(config.compress, "codec", ""),
                "fps": getattr(config.compress, "fps", 0),
                "remove_audio": getattr(config.compress, "remove_audio", False),
                "crf": getattr(config.compress, "crf", 0),
                "target_size_mb": getattr(config.compress, "target_size_mb", 0),
                "max_width": getattr(config.compress, "max_width", 0),
            },
            "context": _prompt_context_parts(config, context_override),
            "transcript_context": transcript_context,
            "transcript_prompt": (
                resolve_prompt_template("transcript_context", TRANSCRIPT_CONTEXT, config) if transcript_context else ""
            ),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_transcript_context(config: AppConfig, compressed: Path) -> str:
    transcripts_dir = getattr(config, "transcripts_dir", None)
    if not isinstance(transcripts_dir, Path):
        return ""
    path = transcripts_dir / f"{compressed.stem}_transcript.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list) or not data["segments"]:
        return ""
    return json.dumps(
        {"source_stem": data.get("source_stem", compressed.stem), "segments": data["segments"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _effective_analysis_context(config: AppConfig, original: Path, context_override: str | None) -> str | None:
    try:
        from clio.gpmf import merge_telemetry_into_context

        return merge_telemetry_into_context(
            context_override,
            original,
            use_gpmf=bool(getattr(config.analyze, "use_gpmf", False)),
        )
    except Exception as exc:
        # Context enrichment is optional; a malformed sidecar must not prevent
        # the base video analysis or cache check from running.
        print(t("task.analyze.gpmf_context_failed", {"exc": exc}))
        return context_override


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
    transcript_context: str = "",
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
                transcript_context=transcript_context,
            )
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError(t("task.analyze.cancelled")) from exc
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
                raise InterruptedError(t("task.analyze.cancelled"))
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
    day_label: str = "day1",
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

    effective_context = _effective_analysis_context(config, original, context_override)
    transcript_context = _load_transcript_context(config, compressed)

    existing = sorted(config.texts_dir.glob(f"{idx_str}_*.json"))
    json_path = None
    analysis = None
    # Lineage or source mismatches: keep the old files until a fresh analysis
    # has been committed, so a failed re-run never loses the previous result.
    stale_candidates: list[Path] = list(existing) if overwrite or not config.analyze.skip_existing else []
    if not overwrite and config.analyze.skip_existing and existing:
        current_lineage = _analysis_lineage_fingerprint(config, task_prompts, effective_context, transcript_context)
        for candidate in existing:
            try:
                existing_data = json.loads(candidate.read_text(encoding="utf-8"))
                source_matches = existing_data.get("source_file", "") == original.name
                if source_matches and existing_data.get("_lineage") == current_lineage and json_path is None:
                    if not str(existing_data.get("day_label") or "").strip():
                        existing_data["day_label"] = day_label
                        write_json_atomic(candidate, existing_data)
                    json_path = candidate
                    analysis = existing_data
                    continue
                if source_matches and existing_data.get("_lineage") is None and json_path is None:
                    # Pre-lineage cache: accept and stamp the current lineage so
                    # future prompt/model changes invalidate it.
                    json_path = candidate
                    analysis = existing_data
                    if not str(analysis.get("day_label") or "").strip():
                        analysis["day_label"] = day_label
                    analysis["_lineage"] = current_lineage
                    write_json_atomic(candidate, analysis)
                    if tracker:
                        tracker.log(t("task.analyze.lineage_stamped", {"name": candidate.name}))
                    continue
                if source_matches:
                    print(t("task.analyze.overwrite_prompt_changed", {"name": candidate.name}))
                else:
                    print(t("task.analyze.overwrite_source_mismatch", {"name": candidate.name}))
            except (json.JSONDecodeError, OSError):
                pass
            stale_candidates.append(candidate)
    if json_path:
        text_path = json_path.with_suffix(".txt")
        if tracker:
            tracker.next(message=t("task.analyze.skip_message", {"name": compressed.name}))
            tracker.log(t("task.analyze.skip_log_analyzed", {"name": compressed.name}))
        state.mark(original.stem, "analyze", "skipped")
        print(t("task.analyze.skip_exists", {"name": compressed.name, "json": json_path.name}))
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
        print(t("task.analyze.skip_no_duration", {"name": compressed.name}))
        if tracker:
            tracker.next(message=t("task.analyze.skip_message_no_duration", {"name": compressed.name}))
            tracker.log(t("task.analyze.skip_log_no_duration", {"name": compressed.name}))
        state.mark(original.stem, "analyze", "skipped")
        return None

    # Whole-clip quota guard applies before both single-call and windowed analysis.
    max_min = config.analyze.max_analyze_duration_min
    if max_min > 0 and duration_sec > max_min * 60:
        print(
            t(
                "task.analyze.skip_too_long",
                {"name": compressed.name, "duration": format_duration(duration_sec), "max": max_min},
            )
        )
        if tracker:
            tracker.next(message=t("task.analyze.skip_message_too_long", {"name": compressed.name}))
            tracker.log(
                t(
                    "task.analyze.skip_log_too_long",
                    {"name": compressed.name, "duration": format_duration(duration_sec)},
                )
            )
        state.mark(original.stem, "analyze", "skipped")
        return None

    # Check cancel_event before starting analysis
    if cancel_event and cancel_event.is_set():
        print(t("task.analyze.cancel_item", {"name": compressed.name}))
        state.mark(original.stem, "analyze", "cancelled")
        return None

    print(t("task.analyze.analyzing", {"name": compressed.name}))
    t0 = time.monotonic()
    try:
        analysis = _analyze_with_optional_windows(
            compressed=compressed,
            duration_sec=duration_sec,
            config=config,
            token_store=token_store,
            cancel_event=cancel_event,
            effective_context=effective_context,
            task_prompts=task_prompts,
            transcript_context=transcript_context,
        )
    except InterruptedError:
        if cancel_event is not None:
            cancel_event.set()
        print(t("task.analyze.cancel_item", {"name": compressed.name}))
        state.mark(original.stem, "analyze", "cancelled")
        if tracker:
            tracker.next(message=t("task.analyze.cancel_message", {"name": compressed.name}))
            tracker.log(t("task.analyze.cancel_log", {"name": compressed.name}))
        return None
    except Exception as e:
        elapsed_total = time.monotonic() - t0
        print(
            t(
                "task.analyze.error_item",
                {"name": compressed.name, "detail": e, "elapsed": format_duration(elapsed_total)},
            )
        )
        state.mark(original.stem, "analyze", "error")
        error_count[0] += 1
        if tracker:
            tracker.next(message=t("task.analyze.fail_message", {"name": compressed.name}))
            tracker.log(t("task.analyze.fail_log", {"name": compressed.name, "detail": e}))
        return None

    analysis["index"] = idx_val
    analysis["source_file"] = original.name
    analysis["compressed_file"] = compressed.name  # stable lookup key for videos.py
    if not str(analysis.get("day_label") or "").strip():
        analysis["day_label"] = day_label
    identity = resolve_identity(compressed, idx_str, project_dir=config.project_dir)
    add_schema_version(analysis)
    analysis["_lineage"] = _analysis_lineage_fingerprint(config, task_prompts, effective_context, transcript_context)
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
        tracker.next(message=t("task.analyze.done_message", {"name": compressed.name}))
        tracker.log(t("task.analyze.done_log", {"name": original.stem}))
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
                print(t("task.analyze.vindex_missing_compressed", {"name": single_file.name}))
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
                print(t("task.analyze.compressed_missing", {"name": single_file.name}))
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
                print(t("task.analyze.original_missing", {"name": p.name}))
                continue
            idx_str = parts[0]
            items.append((p, orig_path, idx_str))

    # Keep the complete project item set for day assignment. A selected-file
    # run must retain the same dayN mapping as a full analysis run.
    all_items = list(items)
    if single_file or files is not None:
        all_items = []
        for p in _list_compressed(config.compressed_dir):
            parts = p.stem.split("_", 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            orig_path = _resolve_original(p.stem, stem_cache, config.project_dir)
            if orig_path is not None:
                all_items.append((p, orig_path, parts[0]))

    if files is not None:
        selected = _selected_stems(files)
        items = [
            it for it in items if _matches_selected_stem(it[0], selected) or _matches_selected_stem(it[1], selected)
        ]

    if not items:
        print(t("task.analyze.compressed_dir_empty", {"path": config.compressed_dir}))
        return []

    day_labels = _build_day_labels(config, all_items)

    total = len(items)
    print(t("task.analyze.pending", {"total": total, "path": config.compressed_dir}))
    state = ProcessingState(config.paths.output_dir)

    with timed(t("task.analyze.phase_title", {"count": total})):
        error_count: list[int] = [0]
        max_workers = config.analyze.max_workers

        if tracker:
            tracker.update(phase="analyze", total=total, current=0)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            # Check cancel_event before submitting any tasks
            if cancel_event and cancel_event.is_set():
                print(t("task.analyze.cancelled_step"))
                return records

            # Submit a bounded number of futures (avoid queueing all at once)
            batch_size = min(max_workers, len(items))
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                # Check cancel_event before each batch submission
                if cancel_event and cancel_event.is_set():
                    print(t("task.analyze.cancel_remaining"))
                    break

                batch_futures: list[Future] = []
                for compressed, original, idx_str in batch:
                    # Check cancel_event before each submission
                    if cancel_event and cancel_event.is_set():
                        print(t("task.analyze.cancel_remaining"))
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
                        day_labels.get(str(original.resolve()), "day1"),
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
                        print(t("task.analyze.task_failed", {"detail": e}))
                        error_count[0] += 1

                # Cancel remaining pending futures if cancelled mid-batch
                if cancel_event and cancel_event.is_set():
                    print(t("task.analyze.cancel_pending"))
                    for f in batch_futures:
                        if not f.done():
                            f.cancel()
                    break

    records.sort(key=lambda r: r.index)
    existing = _clip_records_from_csv(config.summary_csv)
    to_write = _merge_summary_records(existing, records)
    _write_csv(config.summary_csv, to_write, config)
    print(t("task.analyze.csv_saved", {"path": config.summary_csv}))

    completed = len(records)
    failed = error_count[0]
    if failed > 0:
        raise RuntimeError(t("task.analyze.incomplete", {"completed": completed, "failed": failed}))
    return records

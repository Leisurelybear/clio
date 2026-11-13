"""Analysis task — AI analysis of compressed videos."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
import time
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from clio._constants import VIDEO_EXTS
from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import _prompt_context_parts, analyze_video, summarize_window_analysis
from clio.analyze_windows import (
    AnalyzeWindow,
    build_analyze_windows,
    cleanup_window_cache_dir,
    load_window_cache,
    measure_timeline_coverage,
    merge_timeline_completion,
    merge_window_analyses,
    save_window_cache,
    select_sparse_retry_gap,
    shift_analysis_times,
    slice_transcript_context,
    window_cache_dir,
)
from clio.artifact_guard import (
    ArtifactOverwriteConfirmationRequired,
    artifact_requires_overwrite_confirmation,
    backup_artifacts,
)
from clio.compress import analysis_compress_advice
from clio.config import AppConfig
from clio.i18n import t
from clio.identity import (
    SEGMENT_SUFFIX_RE,
    _identity_to_dict,
    is_legacy_split_identity,
    is_legacy_split_path,
    load_identity,
    prefer_canonical_compressed,
    resolve_identity,
)
from clio.log import format_duration, timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressReporter
from clio.prompt_overrides import resolve_prompt_template
from clio.prompts import ANALYZE_PROMPT, TRANSCRIPT_CONTEXT, WINDOW_SUMMARY_PROMPT
from clio.scene import SCENE_DETECT_VERSION, detect_scene_boundaries, slice_scene_boundaries
from clio.schema import add_schema_version
from clio.tasks._helpers import (
    ClipRecord,
    _artifact_source_path,
    _build_stem,
    _clip_records_from_csv,
    _matches_selected_stem,
    _merge_summary_records,
    _path_key,
    _project_selection_stems,
    _selection_from_files,
    _task_lineage_settings,
    _write_csv,
    _write_text_file,
)
from clio.tasks.cover import extract_cover_frame
from clio.tasks.transcript_align import attach_transcript_to_analysis
from clio.utils import get_duration_sec, resolve_binary, run_probe, write_json_atomic
from clio.vmeta import VideoIndex, VideoMeta

_CAPTURE_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-_]?([01]\d)[-_]?([0-3]\d)")


class _WindowCacheKey(TypedDict):
    lineage: str
    source_size: int
    source_mtime_ns: int


def _capture_date_key(value: str) -> str:
    match = _CAPTURE_DATE_RE.search(value)
    if not match:
        return ""
    try:
        return datetime.strptime("".join(match.groups()), "%Y%m%d").date().isoformat()
    except ValueError:
        return ""


def _creation_time_utc_date_key(value: str) -> str:
    """Return the UTC calendar date encoded by a container creation_time."""
    text = str(value).strip()
    if not text:
        return ""
    iso_text = f"{text[:-1]}+00:00" if text[-1] in "Zz" else text
    try:
        parsed = datetime.fromisoformat(iso_text)
    except ValueError:
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.date().isoformat()


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
                        date_key = _creation_time_utc_date_key(str(value))
                        if date_key:
                            return date_key
            for stream in probe_data.get("streams") or []:
                tags = stream.get("tags") or {}
                if isinstance(tags, dict):
                    for key, value in tags.items():
                        if key.lower() == "creation_time":
                            date_key = _creation_time_utc_date_key(str(value))
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


class _FailureCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0

    def increment(self) -> None:
        with self._lock:
            self._count += 1

    @property
    def value(self) -> int:
        with self._lock:
            return self._count


def _analysis_lineage_fingerprint(
    config: AppConfig,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
    transcript_context: str = "",
    *,
    multi_window: bool = False,
) -> str:
    """Fingerprint of everything that can change a cached analysis result.

    Changing the analyze prompt (builtin, override file, or runtime task prompt)
    or the video_analyze provider/model invalidates the skip_existing cache so
    users never silently keep results produced under an older lineage.
    """
    try:
        prompt = resolve_prompt_template("video_analyze", ANALYZE_PROMPT, config, task_prompts=task_prompts)
    except (OSError, UnicodeDecodeError, ValueError):
        prompt = ANALYZE_PROMPT
    payload_data: dict[str, object] = {
        "task": _task_lineage_settings(config, "video_analyze"),
        "prompt": prompt,
        "max_analyze_duration_min": getattr(config.analyze, "max_analyze_duration_min", 0),
        "window_max_min": int(getattr(config.analyze, "window_max_min", 15) or 15),
        "window_overlap_sec": int(getattr(config.analyze, "window_overlap_sec", 20) or 0),
        "media_resolution": getattr(config.analyze, "media_resolution", "medium"),
        "analysis_engine_version": 5,
        "scene_detection_version": SCENE_DETECT_VERSION,
        "video_fps": 3,
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
    }
    if multi_window:
        try:
            summary_prompt = resolve_prompt_template(
                "window_summary", WINDOW_SUMMARY_PROMPT, config, task_prompts=task_prompts
            )
        except (OSError, UnicodeDecodeError, ValueError):
            summary_prompt = WINDOW_SUMMARY_PROMPT
        payload_data["window_summary"] = {
            "version": 1,
            "task": _task_lineage_settings(config, "refine_text"),
            "prompt": summary_prompt,
        }
    payload = json.dumps(payload_data, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _uses_multiple_windows(compressed: Path, duration_sec: float, config: AppConfig) -> bool:
    if is_legacy_split_path(compressed) or duration_sec <= 0:
        return False
    window_max = int(getattr(config.analyze, "window_max_min", 15) or 15)
    overlap = int(getattr(config.analyze, "window_overlap_sec", 20) or 0)
    return len(build_analyze_windows(duration_sec, window_max, overlap)) > 1


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
    except (OSError, TypeError, ValueError) as exc:
        # Context enrichment is optional; a malformed sidecar must not prevent
        # the base video analysis or cache check from running.
        print(t("task.analyze.gpmf_context_failed", {"exc": exc}))
        return context_override


def analysis_artifact_overwrite_required(
    config: AppConfig,
    compressed: Path,
    original: Path,
    idx_str: str,
    *,
    overwrite: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
    duration_sec: float,
) -> list[Path]:
    """Return protected JSON/TXT analysis pairs a rerun would replace."""
    existing = sorted(config.texts_dir.glob(f"{idx_str}_*.json"))
    if not existing:
        return []

    if not overwrite and config.analyze.skip_existing:
        effective_context = _effective_analysis_context(config, original, context_override)
        transcript_context = _load_transcript_context(config, compressed)
        current_lineage = _analysis_lineage_fingerprint(
            config,
            task_prompts,
            effective_context,
            transcript_context,
            multi_window=_uses_multiple_windows(compressed, duration_sec, config),
        )
        for candidate in existing:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError):
                continue
            if not isinstance(data, dict) or data.get("source_file") != original.name:
                continue
            if data.get("_lineage") in {None, current_lineage}:
                # One accepted cache entry skips the item before stale files are removed.
                return []

    risky: list[Path] = []
    for candidate in existing:
        if not artifact_requires_overwrite_confirmation(candidate):
            continue
        risky.append(candidate)
        text_path = candidate.with_suffix(".txt")
        if text_path.is_file():
            risky.append(text_path)
    return risky


def _analysis_duration_sec(config: AppConfig, compressed: Path, original: Path) -> float:
    meta = VideoMeta.read(compressed)
    if meta is not None and meta.target_duration_sec > 0:
        return meta.target_duration_sec
    try:
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
    except (OSError, TypeError, ValueError):
        return 0.0
    for candidate in (compressed, original):
        try:
            duration = get_duration_sec(candidate, ffprobe)
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    return 0.0


def _build_stem_to_path(project_dir: Path | None = None) -> dict[str, Path | None]:
    """Build a one-time {stem_lower: path} map from videos.json (None = ambiguous)."""
    from clio.tasks._video_loader import load_selected_videos, stem_to_path_map

    return stem_to_path_map(load_selected_videos(project_dir))


def _resolve_original(
    compressed_stem: str,
    stem_cache: dict[str, Path | None] | None = None,
    project_dir: Path | None = None,
    compressed_path: Path | None = None,
    selected_paths: frozenset[str] | None = None,
) -> Path | None:
    """Resolve the original video for a compressed artifact.

    The sidecar source path wins over any filename guess; duplicate stems in
    videos.json resolve to None instead of silently picking the first file.
    A sidecar that names a live source outside ``selected_paths`` is rejected
    so a removed original never comes back through its leftover compressed file.
    """
    if compressed_path is not None:
        source = _artifact_source_path(compressed_path)
        if source is not None:
            if selected_paths is None or _path_key(source) in selected_paths:
                return source
            if source.is_file():
                return None

    if "_" not in compressed_stem:
        orig_stem = compressed_stem
    else:
        _, orig_stem = compressed_stem.split("_", 1)

    def _try_find(stem: str) -> tuple[bool, Path | None]:
        key = stem.lower()
        if stem_cache is not None:
            if key in stem_cache:
                return True, stem_cache[key]
            return False, None
        from clio.tasks._video_loader import load_selected_videos

        hits = [p for p in load_selected_videos(project_dir) if p.stem.lower() == key]
        if hits:
            return True, hits[0] if len(hits) == 1 else None
        return False, None

    found, result = _try_find(orig_stem)
    if found:
        return result

    m = SEGMENT_SUFFIX_RE.search(orig_stem)
    if m:
        _, result = _try_find(SEGMENT_SUFFIX_RE.sub("", orig_stem))
        return result
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
    lineage: str = "",
) -> dict:
    """Run Gemini analyze: legacy split files stay single-call; else use windows."""
    scene_boundaries = detect_scene_boundaries(
        compressed,
        duration_sec,
        str(getattr(config.paths, "ffmpeg", "") or ""),
        cancel_event=cancel_event,
    )

    def _one_call(
        video_path: str,
        *,
        start_sec: float | None = None,
        end_sec: float | None = None,
        fps: float | None = None,
        window_boundaries: Sequence[float] | None = None,
    ) -> dict:
        window_transcript_context = transcript_context
        if transcript_context and start_sec is not None and end_sec is not None:
            window_transcript_context = slice_transcript_context(transcript_context, start_sec, end_sec)
        try:
            return analyze_video(
                video_path,
                config,
                progress_callback=lambda msg: None,
                token_store=token_store,
                cancel_event=cancel_event,
                context_override=effective_context,
                task_prompts=task_prompts,
                transcript_context=window_transcript_context,
                video_start_sec=start_sec,
                video_end_sec=end_sec,
                video_fps=fps,
                media_resolution=getattr(config.analyze, "media_resolution", "medium"),
                scene_boundaries=window_boundaries,
            )
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError(t("task.analyze.cancelled")) from exc
            raise

    def _complete_if_sparse(part: dict, window: AnalyzeWindow) -> dict:
        gap = select_sparse_retry_gap(part, window.start_sec, window.end_sec)
        if gap is None:
            return part
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError(t("task.analyze.cancelled"))
        print(
            t(
                "task.analyze.sparse_retry",
                {
                    "name": compressed.name,
                    "index": window.index,
                    "start": f"{gap.start_sec:.1f}",
                    "end": f"{gap.end_sec:.1f}",
                },
            )
        )
        try:
            completion = _one_call(
                str(compressed),
                start_sec=gap.start_sec,
                end_sec=gap.end_sec,
                fps=3,
                window_boundaries=slice_scene_boundaries(scene_boundaries, gap.start_sec, gap.end_sec),
            )
        except InterruptedError:
            raise
        except Exception as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError(t("task.analyze.cancelled")) from exc
            print(
                t(
                    "task.analyze.sparse_retry_failed",
                    {"name": compressed.name, "index": window.index, "detail": exc},
                )
            )
            return part
        completed = merge_timeline_completion(part, completion, gap)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError(t("task.analyze.cancelled"))
        return completed

    if is_legacy_split_path(compressed):
        return _one_call(str(compressed), window_boundaries=scene_boundaries)

    w_max = int(getattr(config.analyze, "window_max_min", 15) or 15)
    overlap = int(getattr(config.analyze, "window_overlap_sec", 20) or 0)
    windows = build_analyze_windows(duration_sec, w_max, overlap)

    if len(windows) <= 1:
        window_boundaries = slice_scene_boundaries(scene_boundaries, 0.0, duration_sec)
        analysis = _one_call(
            str(compressed),
            start_sec=0.0,
            end_sec=duration_sec,
            fps=3,
            window_boundaries=window_boundaries,
        )
        if windows:
            analysis = _complete_if_sparse(analysis, windows[0])
        return merge_window_analyses([(windows[0], analysis)], overlap) if windows else analysis

    cache_dir = window_cache_dir(config.paths.output_dir, compressed.stem)
    cache_key: _WindowCacheKey | None
    try:
        source_stat = compressed.stat()
        cache_key = {
            "lineage": lineage,
            "source_size": source_stat.st_size,
            "source_mtime_ns": source_stat.st_mtime_ns,
        }
    except OSError:
        cache_key = None
    partials: list = []
    for w in windows:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError(t("task.analyze.cancelled"))
        part = load_window_cache(cache_dir, window=w, **cache_key) if cache_key is not None and lineage else None
        if part is not None:
            print(t("task.analyze.window_cache_hit", {"name": compressed.name, "index": w.index}))
            partials.append((w, part))
            continue
        window_boundaries = slice_scene_boundaries(scene_boundaries, w.start_sec, w.end_sec)
        part = _one_call(
            str(compressed),
            start_sec=w.start_sec,
            end_sec=w.end_sec,
            fps=3,
            window_boundaries=window_boundaries,
        )
        part = shift_analysis_times(part, w.start_sec)
        part = _complete_if_sparse(part, w)
        if cache_key is not None and lineage:
            save_window_cache(cache_dir, window=w, analysis=part, **cache_key)
        partials.append((w, part))

    merged = merge_window_analyses(partials, overlap)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError(t("analyze.cancelled"))
    return summarize_window_analysis(
        merged,
        config,
        token_store=token_store,
        cancel_event=cancel_event,
        context_override=effective_context,
        task_prompts=task_prompts,
    )


def _process_video_item(
    compressed: Path,
    original: Path,
    idx_str: str,
    config: AppConfig,
    token_store: FileTokenUsageStore,
    overwrite: bool,
    tracker: ProgressReporter | None,
    state: ProcessingState,
    failure_counter: _FailureCounter,
    day_label: str = "day1",
    cancel_event: threading.Event | None = None,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    confirm_artifact_overwrite: bool = False,
) -> ClipRecord | None:
    idx_val = int(idx_str)

    # Probe compressed file duration once (cheaper than probing original in _write_csv)
    duration_sec = 0.0
    try:
        ffprobe_bin = resolve_binary(config.paths.ffprobe, "ffprobe")
        duration_sec = get_duration_sec(compressed, ffprobe_bin)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        pass
    if duration_sec <= 0:
        # Fallback: probe original so windowing can still run for long clips.
        try:
            ffprobe_bin = resolve_binary(config.paths.ffprobe, "ffprobe")
            duration_sec = get_duration_sec(original, ffprobe_bin)
        except (OSError, subprocess.SubprocessError, TypeError, ValueError):
            pass

    effective_context = _effective_analysis_context(config, original, context_override)
    transcript_context = _load_transcript_context(config, compressed)
    multi_window = _uses_multiple_windows(compressed, duration_sec, config)
    current_lineage = _analysis_lineage_fingerprint(
        config,
        task_prompts,
        effective_context,
        transcript_context,
        multi_window=multi_window,
    )

    existing = sorted(config.texts_dir.glob(f"{idx_str}_*.json"))
    json_path = None
    analysis = None
    # Lineage or source mismatches: keep the old files until a fresh analysis
    # has been committed, so a failed re-run never loses the previous result.
    stale_candidates: list[Path] = list(existing) if overwrite or not config.analyze.skip_existing else []
    if not overwrite and config.analyze.skip_existing and existing:
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

    protected_stale = [
        candidate for candidate in stale_candidates if artifact_requires_overwrite_confirmation(candidate)
    ]
    if protected_stale and not confirm_artifact_overwrite:
        raise ArtifactOverwriteConfirmationRequired(
            t(
                "task.artifact.overwrite_confirmation_required",
                {"items": ", ".join(candidate.name for candidate in protected_stale)},
            ),
            protected_stale,
        )

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
            lineage=current_lineage,
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
    except Exception as e:  # noqa: BLE001 - one failed clip must be recorded without aborting the batch
        elapsed_total = time.monotonic() - t0
        print(
            t(
                "task.analyze.error_item",
                {"name": compressed.name, "detail": e, "elapsed": format_duration(elapsed_total)},
            )
        )
        state.mark(original.stem, "analyze", "error")
        failure_counter.increment()
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
    analysis["_lineage"] = current_lineage
    analysis["media_identity"] = _identity_to_dict(identity)
    attach_transcript_to_analysis(config, analysis)
    coverage = measure_timeline_coverage(analysis, duration_sec)
    print(
        f"[analyze] {compressed.name}: rows={coverage.rows} "
        f"coverage={coverage.coverage_ratio * 100:.1f}% max_gap={coverage.max_gap_sec:.1f}s"
    )

    stem = _build_stem(idx_val, analysis.get("title", original.stem), config)
    final_text = config.texts_dir / f"{stem}.txt"
    json_path = config.texts_dir / f"{stem}.json"
    # Legacy split artifacts carry timestamps relative to their segment, so the
    # original would be seeked at the wrong place; keep those on the compressed clip.
    cover_source = compressed if is_legacy_split_identity(identity) else original
    cover_path = extract_cover_frame(config, cover_source, analysis, stem, fallback_path=compressed)
    if cover_path is not None:
        analysis["cover_file"] = str(cover_path.relative_to(config.paths.output_dir))

    # Re-check immediately before replacing files so an edit made while the
    # provider call was running still cannot be overwritten silently.
    protected_stale = [
        candidate for candidate in stale_candidates if artifact_requires_overwrite_confirmation(candidate)
    ]
    if protected_stale and not confirm_artifact_overwrite:
        raise ArtifactOverwriteConfirmationRequired(
            t(
                "task.artifact.overwrite_confirmation_required",
                {"items": ", ".join(candidate.name for candidate in protected_stale)},
            ),
            protected_stale,
        )
    if protected_stale:
        backup_paths: list[Path] = []
        for candidate in protected_stale:
            backup_paths.append(candidate)
            text_path = candidate.with_suffix(".txt")
            if text_path.is_file():
                backup_paths.append(text_path)
        backup_artifacts(backup_paths)

    _write_text_file(final_text, analysis, original, compressed)
    write_json_atomic(json_path, analysis)
    cleanup_window_cache_dir(window_cache_dir(config.paths.output_dir, compressed.stem))

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
    tracker: ProgressReporter | None = None,
    single_file: Path | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    confirm_artifact_overwrite: bool = False,
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
    project_selection = _project_selection_stems(config)
    selected_paths = project_selection.paths if project_selection is not None else None

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
            orig_path = _resolve_original(p.stem, stem_cache, config.project_dir, p, selected_paths)
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
            orig_path = _resolve_original(p.stem, stem_cache, config.project_dir, p, selected_paths)
            if orig_path is not None:
                all_items.append((p, orig_path, parts[0]))

    if files is not None:
        selected = _selection_from_files(files)
        items = [
            it
            for it in items
            if _matches_selected_stem(it[0], selected, artifact=True) or _matches_selected_stem(it[1], selected)
        ]

    if not items:
        print(t("task.analyze.compressed_dir_empty", {"path": config.compressed_dir}))
        return []

    if not confirm_artifact_overwrite:
        protected_indices = {
            candidate.stem.split("_", 1)[0]
            for candidate in config.texts_dir.glob("*.json")
            if candidate.stem.split("_", 1)[0].isdigit() and artifact_requires_overwrite_confirmation(candidate)
        }
        protected_artifacts: list[Path] = []
        for compressed, original, idx_str in items:
            if idx_str not in protected_indices:
                continue
            protected_artifacts.extend(
                analysis_artifact_overwrite_required(
                    config,
                    compressed,
                    original,
                    idx_str,
                    overwrite=overwrite,
                    context_override=context_override,
                    task_prompts=task_prompts,
                    duration_sec=_analysis_duration_sec(config, compressed, original),
                )
            )
        unique_artifacts: dict[Path, Path] = {}
        for artifact in protected_artifacts:
            unique_artifacts.setdefault(artifact.resolve(), artifact)
        if unique_artifacts:
            names = ", ".join(path.name for path in unique_artifacts.values())
            raise ArtifactOverwriteConfirmationRequired(
                t("task.artifact.overwrite_confirmation_required", {"items": names}),
                unique_artifacts.values(),
            )

    compress_fps = getattr(config.compress, "fps", 0)
    compress_width = getattr(config.compress, "max_width", 0)
    media_resolution = str(getattr(config.analyze, "media_resolution", "medium") or "medium")
    for advice in analysis_compress_advice(
        media_resolution=media_resolution,
        max_width=compress_width,
        fps=compress_fps,
    ):
        if advice.field == "max_width":
            warning = t(
                "task.analyze.high_resolution_low_compress_width",
                {
                    "width": advice.current,
                    "recommended_min": advice.recommended_min,
                    "recommended_max": advice.recommended_max,
                },
            )
        else:
            warning = t("task.analyze.low_compress_fps", {"fps": advice.current})
        print(warning)
        if tracker:
            tracker.log(warning)

    day_labels = _build_day_labels(config, all_items)

    total = len(items)
    print(t("task.analyze.pending", {"total": total, "path": config.compressed_dir}))
    state = ProcessingState(config.paths.output_dir)

    with timed(t("task.analyze.phase_title", {"count": total})):
        failure_counter = _FailureCounter()
        max_workers = config.analyze.max_workers

        if tracker:
            tracker.update(phase="analyze", total=total, current=0)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            # Check cancel_event before submitting any tasks
            if cancel_event and cancel_event.is_set():
                print(t("task.analyze.cancelled_step"))
                return records

            item_iter = iter(items)
            pending: dict[Future, None] = {}

            def _submit_next() -> bool:
                try:
                    compressed, original, idx_str = next(item_iter)
                except StopIteration:
                    return False
                future = pool.submit(
                    _process_video_item,
                    compressed,
                    original,
                    idx_str,
                    config,
                    token_store,
                    overwrite,
                    tracker,
                    state,
                    failure_counter,
                    day_labels.get(str(original.resolve()), "day1"),
                    cancel_event,
                    context_override,
                    task_prompts,
                    confirm_artifact_overwrite,
                )
                pending[future] = None
                return True

            for _ in range(min(max_workers, len(items))):
                _submit_next()

            while pending:
                if cancel_event and cancel_event.is_set():
                    print(t("task.analyze.cancel_pending"))
                    for future in pending:
                        if not future.done():
                            future.cancel()
                    break

                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future, None)
                    try:
                        result = future.result()
                        if result is not None:
                            records.append(result)
                    except Exception as e:  # noqa: BLE001 - a worker failure must be counted without aborting analysis
                        print(t("task.analyze.task_failed", {"detail": e}))
                        failure_counter.increment()

                    if cancel_event and cancel_event.is_set():
                        break
                    _submit_next()

                if cancel_event and cancel_event.is_set():
                    print(t("task.analyze.cancel_pending"))
                    for future in pending:
                        if not future.done():
                            future.cancel()
                    break

    records.sort(key=lambda r: r.index)
    existing = _clip_records_from_csv(config.summary_csv)
    to_write = _merge_summary_records(existing, records)
    _write_csv(config.summary_csv, to_write, config)
    print(t("task.analyze.csv_saved", {"path": config.summary_csv}))

    completed = len(records)
    failed = failure_counter.value
    if failed > 0:
        raise RuntimeError(t("task.analyze.incomplete", {"completed": completed, "failed": failed}))
    return records

"""Analysis task — AI analysis of compressed videos."""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, tzinfo
from pathlib import Path
from typing import Any, TypedDict

from clio._constants import VIDEO_EXTS
from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import (
    _prompt_context_parts,
    analyze_video,
    release_video_upload,
    summarize_window_analysis,
)
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
    MediaIdentity,
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
    MediaSelection,
    _artifact_source_path,
    _build_stem,
    _clip_records_from_csv,
    _matches_selected_stem,
    _merge_summary_records,
    _parse_capture_time_text,
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


def _creation_time_local_date_key(value: str, tz: tzinfo | None = None) -> str:
    """Return the machine-local calendar date for a container creation_time."""
    parsed = _parse_capture_time_text(value, tz)
    return parsed.date().isoformat() if parsed is not None else ""


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
                        date_key = _creation_time_local_date_key(str(value))
                        if date_key:
                            return date_key
            for stream in probe_data.get("streams") or []:
                tags = stream.get("tags") or {}
                if isinstance(tags, dict):
                    for key, value in tags.items():
                        if key.lower() == "creation_time":
                            date_key = _creation_time_local_date_key(str(value))
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


class AnalyzeResult(list[ClipRecord]):
    """Successful analysis records with a count of clips that failed."""

    def __init__(self, records: list[ClipRecord] | None = None, *, failed: int = 0) -> None:
        super().__init__(records or [])
        self.failed = failed


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


ANALYSIS_LINEAGE_COMPONENTS = (
    "provider_model",
    "prompt",
    "context",
    "transcript",
    "compressed_media",
    "window",
)


def _lineage_component_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _compressed_media_component(compressed: Path | None) -> str:
    """Fingerprint the actual compressed artifact, not the desired settings."""
    if compressed is None:
        return _lineage_component_hash({"available": False})
    identity: dict[str, object] = {"available": False, "meta": False}
    try:
        stat = compressed.stat()
    except OSError:
        return _lineage_component_hash(identity)
    identity.update(
        {
            "available": True,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    )
    meta = VideoMeta.read(compressed)
    if meta is not None:
        identity.update(
            {
                "meta": True,
                "target_size": meta.target_size,
                "target_modifyTime": meta.target_modifyTime,
                "verify": meta.verify,
            }
        )
    return _lineage_component_hash(identity)


def _analysis_lineage_components(
    config: AppConfig,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
    transcript_context: str = "",
    *,
    multi_window: bool = False,
    compressed: Path | None = None,
) -> dict[str, str]:
    """Return independently identifiable inputs that affect analysis output."""
    try:
        prompt = resolve_prompt_template("video_analyze", ANALYZE_PROMPT, config, task_prompts=task_prompts)
    except (OSError, UnicodeDecodeError, ValueError):
        prompt = ANALYZE_PROMPT

    transcript_prompt = ""
    if transcript_context:
        try:
            transcript_prompt = resolve_prompt_template(
                "transcript_context", TRANSCRIPT_CONTEXT, config, task_prompts=task_prompts
            )
        except (OSError, UnicodeDecodeError, ValueError):
            transcript_prompt = TRANSCRIPT_CONTEXT

    window_data: dict[str, object] = {
        "max_analyze_duration_min": getattr(config.analyze, "max_analyze_duration_min", 0),
        "window_max_min": int(getattr(config.analyze, "window_max_min", 15) or 15),
        "window_overlap_sec": int(getattr(config.analyze, "window_overlap_sec", 20) or 0),
        "media_resolution": getattr(config.analyze, "media_resolution", "medium"),
        "analysis_engine_version": 5,
        "scene_detection_version": SCENE_DETECT_VERSION,
        "video_fps": 3,
        "multi_window": multi_window,
    }
    if multi_window:
        try:
            summary_prompt = resolve_prompt_template(
                "window_summary", WINDOW_SUMMARY_PROMPT, config, task_prompts=task_prompts
            )
        except (OSError, UnicodeDecodeError, ValueError):
            summary_prompt = WINDOW_SUMMARY_PROMPT
        window_data["window_summary"] = {
            "version": 1,
            "task": _task_lineage_settings(config, "refine_text"),
            "prompt": summary_prompt,
        }

    return {
        "provider_model": _lineage_component_hash(_task_lineage_settings(config, "video_analyze")),
        "prompt": _lineage_component_hash(prompt),
        "context": _lineage_component_hash(_prompt_context_parts(config, context_override)),
        "transcript": _lineage_component_hash({"context": transcript_context, "prompt": transcript_prompt}),
        "compressed_media": _compressed_media_component(compressed),
        "window": _lineage_component_hash(window_data),
    }


def _lineage_fingerprint_from_components(components: dict[str, str]) -> str:
    canonical = {name: components[name] for name in ANALYSIS_LINEAGE_COMPONENTS}
    return _lineage_component_hash(canonical)


def _analysis_lineage_fingerprint(
    config: AppConfig,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
    transcript_context: str = "",
    *,
    multi_window: bool = False,
    compressed: Path | None = None,
) -> str:
    """Fingerprint of everything that can change a cached analysis result.

    The aggregate is derived from stable components so callers can explain why a
    cache entry is stale without exposing prompt, context, or transcript text.
    """
    return _lineage_fingerprint_from_components(
        _analysis_lineage_components(
            config,
            task_prompts,
            context_override,
            transcript_context,
            multi_window=multi_window,
            compressed=compressed,
        )
    )


def _legacy_analysis_lineage_fingerprint(
    config: AppConfig,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
    transcript_context: str = "",
    *,
    multi_window: bool = False,
) -> str:
    """Reproduce the pre-component fingerprint for compatible cache migration.

    Existing analysis files do not carry component hashes. Accepting an unchanged
    legacy lineage avoids a one-time full-project rerun solely because the cache
    format gained reason reporting.
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


@dataclass(frozen=True)
class AnalysisCacheStatus:
    accepted_path: Path | None
    accepted_data: dict[str, Any] | None
    current_lineage: str
    current_components: dict[str, str]
    reasons: tuple[str, ...]
    legacy: bool
    source_mismatch: bool
    invalid: bool
    existing_paths: tuple[Path, ...]
    effective_context: str | None
    transcript_context: str
    multi_window: bool

    @property
    def accepted(self) -> bool:
        return self.accepted_path is not None

    @property
    def rerun_required(self) -> bool:
        return bool(self.existing_paths) and not self.accepted


def analysis_cache_status(
    config: AppConfig,
    compressed: Path,
    original: Path,
    idx_str: str,
    *,
    overwrite: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
    duration_sec: float,
    use_transcripts: bool = True,
) -> AnalysisCacheStatus:
    """Evaluate existing analysis candidates with the same rules as execution."""
    effective_context = _effective_analysis_context(config, original, context_override)
    transcript_context = _load_transcript_context(config, compressed) if use_transcripts else ""
    multi_window = _uses_multiple_windows(compressed, duration_sec, config)
    current_components = _analysis_lineage_components(
        config,
        task_prompts,
        effective_context,
        transcript_context,
        multi_window=multi_window,
        compressed=compressed,
    )
    current_lineage = _lineage_fingerprint_from_components(current_components)
    legacy_lineage = _legacy_analysis_lineage_fingerprint(
        config,
        task_prompts,
        effective_context,
        transcript_context,
        multi_window=multi_window,
    )
    existing = tuple(sorted(config.texts_dir.glob(f"{idx_str}_*.json")))
    accepted_path: Path | None = None
    accepted_data: dict[str, Any] | None = None
    reasons: set[str] = set()
    legacy = False
    source_mismatch = False
    invalid = False

    if not overwrite and config.analyze.skip_existing:
        for candidate in existing:
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError):
                invalid = True
                continue
            if not isinstance(data, dict) or data.get("source_file") != original.name:
                source_mismatch = True
                continue
            stored_components = data.get("_lineage_components")
            if not isinstance(stored_components, dict):
                legacy = True
                if data.get("_lineage") == legacy_lineage:
                    if accepted_path is None:
                        accepted_path = candidate
                        accepted_data = data
                else:
                    reasons.add("legacy")
                continue
            component_diff = [
                name for name in ANALYSIS_LINEAGE_COMPONENTS if stored_components.get(name) != current_components[name]
            ]
            if data.get("_lineage") != current_lineage or component_diff:
                reasons.update(component_diff)
                if not component_diff:
                    reasons.add("legacy")
                    legacy = True
                continue
            if accepted_path is None:
                accepted_path = candidate
                accepted_data = data

    ordered_reasons = tuple(name for name in ANALYSIS_LINEAGE_COMPONENTS if name in reasons)
    if "legacy" in reasons:
        ordered_reasons += ("legacy",)
    return AnalysisCacheStatus(
        accepted_path=accepted_path,
        accepted_data=accepted_data,
        current_lineage=current_lineage,
        current_components=current_components,
        reasons=ordered_reasons,
        legacy=legacy,
        source_mismatch=source_mismatch,
        invalid=invalid,
        existing_paths=existing,
        effective_context=effective_context,
        transcript_context=transcript_context,
        multi_window=multi_window,
    )


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
    use_transcripts: bool = True,
) -> list[Path]:
    """Return protected JSON/TXT analysis pairs a rerun would replace."""
    status = analysis_cache_status(
        config,
        compressed,
        original,
        idx_str,
        overwrite=overwrite,
        context_override=context_override,
        task_prompts=task_prompts,
        duration_sec=duration_sec,
        use_transcripts=use_transcripts,
    )
    if status.accepted:
        return []

    risky: list[Path] = []
    for candidate in status.existing_paths:
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


def _window_one_call(
    compressed: Path,
    *,
    config: AppConfig,
    token_store: FileTokenUsageStore | None,
    cancel_event: threading.Event | None,
    effective_context: str | None,
    task_prompts: dict[str, str] | None,
    transcript_context: str,
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
            str(compressed),
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
            reuse_upload=True,
        )
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError(t("task.analyze.cancelled")) from exc
        raise


def _complete_sparse_window(
    part: dict,
    window: AnalyzeWindow,
    *,
    compressed: Path,
    scene_boundaries: Sequence[float],
    config: AppConfig,
    token_store: FileTokenUsageStore | None,
    cancel_event: threading.Event | None,
    effective_context: str | None,
    task_prompts: dict[str, str] | None,
    transcript_context: str,
) -> dict:
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
        completion = _window_one_call(
            compressed,
            config=config,
            token_store=token_store,
            cancel_event=cancel_event,
            effective_context=effective_context,
            task_prompts=task_prompts,
            transcript_context=transcript_context,
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


def _build_window_cache_key(compressed: Path, lineage: str) -> _WindowCacheKey | None:
    try:
        source_stat = compressed.stat()
    except OSError:
        return None
    return {
        "lineage": lineage,
        "source_size": source_stat.st_size,
        "source_mtime_ns": source_stat.st_mtime_ns,
    }


def _load_or_analyze_window(
    window: AnalyzeWindow,
    *,
    compressed: Path,
    scene_boundaries: Sequence[float],
    config: AppConfig,
    token_store: FileTokenUsageStore | None,
    cancel_event: threading.Event | None,
    effective_context: str | None,
    task_prompts: dict[str, str] | None,
    transcript_context: str,
    cache_dir: Path,
    cache_key: _WindowCacheKey | None,
    lineage: str,
) -> tuple[AnalyzeWindow, dict]:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError(t("task.analyze.cancelled"))
    part = load_window_cache(cache_dir, window=window, **cache_key) if cache_key is not None and lineage else None
    if part is not None:
        print(t("task.analyze.window_cache_hit", {"name": compressed.name, "index": window.index}))
        return window, part
    window_boundaries = slice_scene_boundaries(scene_boundaries, window.start_sec, window.end_sec)
    part = _window_one_call(
        compressed,
        config=config,
        token_store=token_store,
        cancel_event=cancel_event,
        effective_context=effective_context,
        task_prompts=task_prompts,
        transcript_context=transcript_context,
        start_sec=window.start_sec,
        end_sec=window.end_sec,
        fps=3,
        window_boundaries=window_boundaries,
    )
    part = shift_analysis_times(part, window.start_sec)
    part = _complete_sparse_window(
        part,
        window,
        compressed=compressed,
        scene_boundaries=scene_boundaries,
        config=config,
        token_store=token_store,
        cancel_event=cancel_event,
        effective_context=effective_context,
        task_prompts=task_prompts,
        transcript_context=transcript_context,
    )
    if cache_key is not None and lineage:
        save_window_cache(cache_dir, window=window, analysis=part, **cache_key)
    return window, part


def _summarize_window_analysis_result(
    merged: dict,
    *,
    compressed: Path,
    config: AppConfig,
    token_store: FileTokenUsageStore | None,
    cancel_event: threading.Event | None,
    effective_context: str | None,
    task_prompts: dict[str, str] | None,
) -> dict:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError(t("analyze.cancelled"))
    try:
        return summarize_window_analysis(
            merged,
            config,
            token_store=token_store,
            cancel_event=cancel_event,
            context_override=effective_context,
            task_prompts=task_prompts,
        )
    except InterruptedError:
        raise
    except Exception as exc:  # noqa: BLE001 - merged window data is still a usable analysis
        print(t("task.analyze.window_summary_failed", {"name": compressed.name, "detail": exc}))
        merged["_summary_failed"] = True
        return merged


def _analyze_with_optional_windows(
    *,
    compressed: Path,
    duration_sec: float,
    config: AppConfig,
    token_store: FileTokenUsageStore | None,
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
    if is_legacy_split_path(compressed):
        return _window_one_call(
            compressed,
            config=config,
            token_store=token_store,
            cancel_event=cancel_event,
            effective_context=effective_context,
            task_prompts=task_prompts,
            transcript_context=transcript_context,
            window_boundaries=scene_boundaries,
        )

    w_max = int(getattr(config.analyze, "window_max_min", 15) or 15)
    overlap = int(getattr(config.analyze, "window_overlap_sec", 20) or 0)
    windows = build_analyze_windows(duration_sec, w_max, overlap)

    if len(windows) <= 1:
        analysis = _window_one_call(
            compressed,
            config=config,
            token_store=token_store,
            cancel_event=cancel_event,
            effective_context=effective_context,
            task_prompts=task_prompts,
            transcript_context=transcript_context,
            start_sec=0.0,
            end_sec=duration_sec,
            fps=3,
            window_boundaries=slice_scene_boundaries(scene_boundaries, 0.0, duration_sec),
        )
        if windows:
            analysis = _complete_sparse_window(
                analysis,
                windows[0],
                compressed=compressed,
                scene_boundaries=scene_boundaries,
                config=config,
                token_store=token_store,
                cancel_event=cancel_event,
                effective_context=effective_context,
                task_prompts=task_prompts,
                transcript_context=transcript_context,
            )
        return merge_window_analyses([(windows[0], analysis)], overlap) if windows else analysis

    cache_dir = window_cache_dir(config.paths.output_dir, compressed.stem)
    cache_key = _build_window_cache_key(compressed, lineage)
    partials = [
        _load_or_analyze_window(
            window,
            compressed=compressed,
            scene_boundaries=scene_boundaries,
            cache_dir=cache_dir,
            cache_key=cache_key,
            lineage=lineage,
            config=config,
            token_store=token_store,
            cancel_event=cancel_event,
            effective_context=effective_context,
            task_prompts=task_prompts,
            transcript_context=transcript_context,
        )
        for window in windows
    ]
    return _summarize_window_analysis_result(
        merge_window_analyses(partials, overlap),
        compressed=compressed,
        config=config,
        token_store=token_store,
        cancel_event=cancel_event,
        effective_context=effective_context,
        task_prompts=task_prompts,
    )


_PROBE_ERRORS = (OSError, subprocess.SubprocessError, TypeError, ValueError)


def _probe_media_duration(compressed: Path, original: Path, config: AppConfig) -> float:
    duration_sec = 0.0
    with contextlib.suppress(*_PROBE_ERRORS):
        duration_sec = get_duration_sec(compressed, resolve_binary(config.paths.ffprobe, "ffprobe"))
    if duration_sec <= 0:
        # Fallback: probe original so windowing can still run for long clips.
        with contextlib.suppress(*_PROBE_ERRORS):
            duration_sec = get_duration_sec(original, resolve_binary(config.paths.ffprobe, "ffprobe"))
    return duration_sec


def _log_stale_candidates(existing: list[Path], cache: AnalysisCacheStatus) -> None:
    for candidate in existing:
        if candidate not in cache.existing_paths:
            continue
        message_key = (
            "task.analyze.overwrite_source_mismatch"
            if cache.source_mismatch
            else "task.analyze.overwrite_prompt_changed"
        )
        print(t(message_key, {"name": candidate.name}))


def _build_cached_record(
    *,
    compressed: Path,
    original: Path,
    idx_str: str,
    idx_val: int,
    json_path: Path,
    analysis: dict | None,
    duration_sec: float,
    config: AppConfig,
    tracker: ProgressReporter | None,
    state: ProcessingState,
) -> ClipRecord:
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
        text_path=text_path,
        compressed_path=compressed,
        analysis=analysis,
        duration_sec=duration_sec,
        identity=identity,
    )


def _skip_no_duration(
    compressed: Path,
    original: Path,
    state: ProcessingState,
    tracker: ProgressReporter | None,
) -> None:
    print(t("task.analyze.skip_no_duration", {"name": compressed.name}))
    if tracker:
        tracker.next(message=t("task.analyze.skip_message_no_duration", {"name": compressed.name}))
        tracker.log(t("task.analyze.skip_log_no_duration", {"name": compressed.name}))
    state.mark(original.stem, "analyze", "skipped")


def _skip_too_long(
    compressed: Path,
    original: Path,
    duration_sec: float,
    max_min: int,
    state: ProcessingState,
    tracker: ProgressReporter | None,
) -> None:
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


def _handle_cancelled(
    compressed: Path,
    original: Path,
    state: ProcessingState,
    tracker: ProgressReporter | None,
    *,
    notify_tracker: bool,
) -> None:
    print(t("task.analyze.cancel_item", {"name": compressed.name}))
    state.mark(original.stem, "analyze", "cancelled")
    if notify_tracker and tracker:
        tracker.next(message=t("task.analyze.cancel_message", {"name": compressed.name}))
        tracker.log(t("task.analyze.cancel_log", {"name": compressed.name}))


def _handle_analysis_error(
    compressed: Path,
    original: Path,
    error: Exception,
    elapsed_total: float,
    state: ProcessingState,
    failure_counter: _FailureCounter,
    tracker: ProgressReporter | None,
) -> None:
    print(
        t(
            "task.analyze.error_item",
            {"name": compressed.name, "detail": error, "elapsed": format_duration(elapsed_total)},
        )
    )
    state.mark(original.stem, "analyze", "error")
    failure_counter.increment()
    if tracker:
        tracker.next(message=t("task.analyze.fail_message", {"name": compressed.name}))
        tracker.log(t("task.analyze.fail_log", {"name": compressed.name, "detail": error}))


def _guard_protected_stale(
    stale_candidates: list[Path],
    *,
    confirm_artifact_overwrite: bool,
    backup: bool,
) -> None:
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
    if not backup or not protected_stale:
        return
    backup_paths: list[Path] = []
    for candidate in protected_stale:
        backup_paths.append(candidate)
        text_path = candidate.with_suffix(".txt")
        if text_path.is_file():
            backup_paths.append(text_path)
    backup_artifacts(backup_paths)


def _finalize_analysis_artifacts(
    analysis: dict,
    *,
    compressed: Path,
    original: Path,
    idx_val: int,
    idx_str: str,
    config: AppConfig,
    current_lineage: str,
    current_components: dict[str, str],
    duration_sec: float,
    use_transcripts: bool,
    day_label: str,
) -> tuple[MediaIdentity, str, Path, Path]:
    analysis["index"] = idx_val
    analysis["source_file"] = original.name
    analysis["compressed_file"] = compressed.name  # stable lookup key for videos.py
    if not str(analysis.get("day_label") or "").strip():
        analysis["day_label"] = day_label
    identity = resolve_identity(compressed, idx_str, project_dir=config.project_dir)
    add_schema_version(analysis)
    analysis["_lineage"] = current_lineage
    analysis["_lineage_components"] = current_components
    analysis["media_identity"] = _identity_to_dict(identity)
    if use_transcripts:
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
    return identity, stem, final_text, json_path


def _remove_stale_artifacts(stale_candidates: list[Path], json_path: Path) -> None:
    # Old results were only kept until here; drop them now that fresh output
    # is committed, so the next glob still resolves this video's own JSON.
    for stale in stale_candidates:
        if stale.name == json_path.name:
            continue
        stale.with_suffix(".txt").unlink(missing_ok=True)
        stale.unlink(missing_ok=True)


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
    use_transcripts: bool = True,
) -> ClipRecord | None:
    idx_val = int(idx_str)
    duration_sec = _probe_media_duration(compressed, original, config)
    cache = analysis_cache_status(
        config,
        compressed,
        original,
        idx_str,
        overwrite=overwrite,
        context_override=context_override,
        task_prompts=task_prompts,
        duration_sec=duration_sec,
        use_transcripts=use_transcripts,
    )
    effective_context = cache.effective_context
    transcript_context = cache.transcript_context
    current_lineage = cache.current_lineage
    existing = list(cache.existing_paths)
    json_path = None
    analysis = None
    # Lineage or source mismatches: keep the old files until a fresh analysis
    # has been committed, so a failed re-run never loses the previous result.
    stale_candidates: list[Path] = (
        existing if (overwrite or not config.analyze.skip_existing or cache.rerun_required) else []
    )
    if cache.accepted:
        json_path = cache.accepted_path
        analysis = cache.accepted_data
        if json_path is None or analysis is None:
            raise RuntimeError("analysis cache status accepted without a candidate")
        if not str(analysis.get("day_label") or "").strip():
            analysis["day_label"] = day_label
            write_json_atomic(json_path, analysis)
    elif existing:
        _log_stale_candidates(existing, cache)
    if json_path:
        return _build_cached_record(
            compressed=compressed,
            original=original,
            idx_str=idx_str,
            idx_val=idx_val,
            json_path=json_path,
            analysis=analysis,
            duration_sec=duration_sec,
            config=config,
            tracker=tracker,
            state=state,
        )

    if duration_sec <= 0 and not is_legacy_split_path(compressed):
        _skip_no_duration(compressed, original, state, tracker)
        return None

    # Whole-clip quota guard applies before both single-call and windowed analysis.
    max_min = config.analyze.max_analyze_duration_min
    if max_min > 0 and duration_sec > max_min * 60:
        _skip_too_long(compressed, original, duration_sec, max_min, state, tracker)
        return None

    if cancel_event and cancel_event.is_set():
        _handle_cancelled(compressed, original, state, tracker, notify_tracker=False)
        return None

    _guard_protected_stale(
        stale_candidates,
        confirm_artifact_overwrite=confirm_artifact_overwrite,
        backup=False,
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
        _handle_cancelled(compressed, original, state, tracker, notify_tracker=True)
        return None
    except ArtifactOverwriteConfirmationRequired:
        raise
    except Exception as e:  # noqa: BLE001 - one failed clip must be recorded without aborting the batch
        _handle_analysis_error(
            compressed,
            original,
            e,
            time.monotonic() - t0,
            state,
            failure_counter,
            tracker,
        )
        return None
    finally:
        release_video_upload(config, str(compressed))

    identity, stem, final_text, json_path = _finalize_analysis_artifacts(
        analysis,
        compressed=compressed,
        original=original,
        idx_val=idx_val,
        idx_str=idx_str,
        config=config,
        current_lineage=current_lineage,
        current_components=cache.current_components,
        duration_sec=duration_sec,
        use_transcripts=use_transcripts,
        day_label=day_label,
    )

    # Re-check immediately before replacing files so an edit made while the
    # provider call was running still cannot be overwritten silently.
    _guard_protected_stale(
        stale_candidates,
        confirm_artifact_overwrite=confirm_artifact_overwrite,
        backup=True,
    )

    _write_text_file(final_text, analysis, original, compressed)
    write_json_atomic(json_path, analysis)
    cleanup_window_cache_dir(window_cache_dir(config.paths.output_dir, compressed.stem))
    _remove_stale_artifacts(stale_candidates, json_path)

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


def _list_compressed_videos(directory: Path) -> list[Path]:
    paths = sorted(p for p in directory.iterdir() if p.suffix.lower() in VIDEO_EXTS and p.is_file())
    return prefer_canonical_compressed(paths)


def _collect_single_file_items(
    single_file: Path,
    config: AppConfig,
) -> list[tuple[Path, Path, str]] | None:
    items: list[tuple[Path, Path, str]] = []
    # Prefer the .vindex sidecar: O(1) split discovery for multi-segment originals.
    vindex = VideoIndex.read(single_file.stem, config.compressed_dir)
    if vindex is not None:
        comp_paths = vindex.compressed_paths(config.compressed_dir)
        if not comp_paths:
            print(t("task.analyze.vindex_missing_compressed", {"name": single_file.name}))
            return None
    else:
        # Fall back to original-stem matching so every matching segment is analyzed.
        comp_paths = [
            p for p in _list_compressed_videos(config.compressed_dir) if single_file.stem.lower() in p.stem.lower()
        ]
        if not comp_paths:
            print(t("task.analyze.compressed_missing", {"name": single_file.name}))
            return None
    for comp in comp_paths:
        idx_str = comp.stem.split("_", 1)[0]
        items.append((comp, single_file, idx_str))
    return items


def _collect_project_items(
    config: AppConfig,
    stem_cache: dict[str, Path | None],
    selected_paths: frozenset[str] | None,
    *,
    warn_missing: bool,
) -> list[tuple[Path, Path, str]]:
    items: list[tuple[Path, Path, str]] = []
    for compressed in _list_compressed_videos(config.compressed_dir):
        parts = compressed.stem.split("_", 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        original = _resolve_original(
            compressed.stem,
            stem_cache,
            config.project_dir,
            compressed,
            selected_paths,
        )
        if original is None:
            if warn_missing:
                print(t("task.analyze.original_missing", {"name": compressed.name}))
            continue
        items.append((compressed, original, parts[0]))
    return items


def _filter_selected_items(
    items: list[tuple[Path, Path, str]],
    selected: MediaSelection,
) -> list[tuple[Path, Path, str]]:
    return [
        item
        for item in items
        if _matches_selected_stem(item[0], selected, artifact=True) or _matches_selected_stem(item[1], selected)
    ]


def _check_analysis_overwrite_gate(
    config: AppConfig,
    items: list[tuple[Path, Path, str]],
    *,
    overwrite: bool,
    context_override: str | None,
    task_prompts: dict[str, str] | None,
    use_transcripts: bool,
    confirm_artifact_overwrite: bool,
) -> None:
    if confirm_artifact_overwrite:
        return
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
                use_transcripts=use_transcripts,
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


def _warn_compress_advice(config: AppConfig, tracker: ProgressReporter | None) -> None:
    for advice in analysis_compress_advice(
        media_resolution=str(getattr(config.analyze, "media_resolution", "medium") or "medium"),
        max_width=getattr(config.compress, "max_width", 0),
        fps=getattr(config.compress, "fps", 0),
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


@dataclass(frozen=True)
class _AnalyzeRunContext:
    config: AppConfig
    token_store: FileTokenUsageStore
    tracker: ProgressReporter | None
    state: ProcessingState
    failure_counter: _FailureCounter
    overwrite: bool
    cancel_event: threading.Event | None
    context_override: str | None
    task_prompts: dict[str, str] | None
    confirm_artifact_overwrite: bool
    use_transcripts: bool
    day_labels: dict[str, str]


def _submit_next_analysis(
    pool: ThreadPoolExecutor,
    item_iter: Iterator[tuple[Path, Path, str]],
    pending: dict[Future, None],
    ctx: _AnalyzeRunContext,
) -> bool:
    try:
        compressed, original, idx_str = next(item_iter)
    except StopIteration:
        return False
    future = pool.submit(
        _process_video_item,
        compressed,
        original,
        idx_str,
        ctx.config,
        ctx.token_store,
        ctx.overwrite,
        ctx.tracker,
        ctx.state,
        ctx.failure_counter,
        ctx.day_labels.get(str(original.resolve()), "day1"),
        ctx.cancel_event,
        ctx.context_override,
        ctx.task_prompts,
        ctx.confirm_artifact_overwrite,
        ctx.use_transcripts,
    )
    pending[future] = None
    return True


def _cancel_pending_analyses(pending: dict[Future, None]) -> None:
    print(t("task.analyze.cancel_pending"))
    for future in pending:
        if not future.done():
            future.cancel()


def _run_analysis_worker_pool(
    items: list[tuple[Path, Path, str]],
    records: AnalyzeResult,
    ctx: _AnalyzeRunContext,
    max_workers: int,
) -> None:
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # Check cancel_event before submitting any tasks.
        if ctx.cancel_event and ctx.cancel_event.is_set():
            print(t("task.analyze.cancelled_step"))
            return
        item_iter = iter(items)
        pending: dict[Future, None] = {}
        for _ in range(min(max_workers, len(items))):
            _submit_next_analysis(pool, item_iter, pending, ctx)

        while pending:
            if ctx.cancel_event and ctx.cancel_event.is_set():
                _cancel_pending_analyses(pending)
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                pending.pop(future, None)
                try:
                    result = future.result()
                    if result is not None:
                        records.append(result)
                except ArtifactOverwriteConfirmationRequired:
                    raise
                except Exception as e:  # noqa: BLE001 - a worker failure must be counted without aborting analysis
                    print(t("task.analyze.task_failed", {"detail": e}))
                    ctx.failure_counter.increment()

                if ctx.cancel_event and ctx.cancel_event.is_set():
                    break
                _submit_next_analysis(pool, item_iter, pending, ctx)

            if ctx.cancel_event and ctx.cancel_event.is_set():
                _cancel_pending_analyses(pending)
                break


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
    use_transcripts: bool = True,
    strict: bool = False,
    **kwargs: Any,
) -> AnalyzeResult:
    """Analyze already-compressed videos using AI (compress step must precede this).

    Scans compressed_dir for existing *.mp4 files and analyzes each.
    For single_file (an original video), finds the matching compressed file first.
    """
    config.texts_dir.mkdir(parents=True, exist_ok=True)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    records: AnalyzeResult = AnalyzeResult()

    # Build one-time stem->path cache to avoid per-video rglob.
    stem_cache = _build_stem_to_path(config.project_dir)
    project_selection = _project_selection_stems(config)
    selected_paths = project_selection.paths if project_selection is not None else None

    if single_file:
        items = _collect_single_file_items(single_file, config)
        if items is None:
            return AnalyzeResult()
    else:
        items = _collect_project_items(config, stem_cache, selected_paths, warn_missing=True)

    # Keep the complete project item set for day assignment. A selected-file
    # run must retain the same dayN mapping as a full analysis run.
    all_items = list(items)
    if single_file or files is not None:
        all_items = _collect_project_items(config, stem_cache, selected_paths, warn_missing=False)

    if files is not None:
        items = _filter_selected_items(items, _selection_from_files(files))

    if not items:
        print(t("task.analyze.compressed_dir_empty", {"path": config.compressed_dir}))
        return AnalyzeResult()

    _check_analysis_overwrite_gate(
        config,
        items,
        overwrite=overwrite,
        context_override=context_override,
        task_prompts=task_prompts,
        use_transcripts=use_transcripts,
        confirm_artifact_overwrite=confirm_artifact_overwrite,
    )
    _warn_compress_advice(config, tracker)
    day_labels = _build_day_labels(config, all_items)

    total = len(items)
    print(t("task.analyze.pending", {"total": total, "path": config.compressed_dir}))
    state = ProcessingState(config.paths.output_dir)
    failure_counter = _FailureCounter()

    with timed(t("task.analyze.phase_title", {"count": total})):
        max_workers = config.analyze.max_workers
        if tracker:
            tracker.update(phase="analyze", total=total, current=0)
        ctx = _AnalyzeRunContext(
            config=config,
            token_store=token_store,
            tracker=tracker,
            state=state,
            failure_counter=failure_counter,
            overwrite=overwrite,
            cancel_event=cancel_event,
            context_override=context_override,
            task_prompts=task_prompts,
            confirm_artifact_overwrite=confirm_artifact_overwrite,
            use_transcripts=use_transcripts,
            day_labels=day_labels,
        )
        _run_analysis_worker_pool(items, records, ctx, max_workers)

    records.sort(key=lambda r: r.index)
    existing = _clip_records_from_csv(config.summary_csv)
    to_write = _merge_summary_records(existing, records)
    _write_csv(config.summary_csv, to_write, config)
    print(t("task.analyze.csv_saved", {"path": config.summary_csv}))

    completed = len(records)
    failed = failure_counter.value
    records.failed = failed
    if strict and failed > 0:
        raise RuntimeError(t("task.analyze.incomplete", {"completed": completed, "failed": failed}))
    return records

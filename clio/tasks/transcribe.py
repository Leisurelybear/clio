from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from clio.asr.readiness import check_asr_ready
from clio.config import AppConfig
from clio.i18n import t
from clio.identity import SEGMENT_SUFFIX_RE, _identity_to_dict, resolve_identity
from clio.log import format_duration
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.schema import add_schema_version
from clio.tasks._helpers import _matches_selected_stem, _selected_stems
from clio.tasks.transcript_align import enrich_matching_analysis_files
from clio.transcribe import transcribe_audio
from clio.utils import find_videos, no_console_kwargs, popen_subprocess, resolve_binary, write_json_atomic

# isort: split
from clio.shutdown import register_process, unregister_process

# Task-owned temp area for WAV extraction (GAP-P2-08): PCM is decoded to the
# project's own volume instead of the system temp so crashes cannot fill the OS
# temp disk, and stale leftovers are swept at the next run.
_TMP_DIRNAME = ".clio_tmp"
_STALE_TMP_MAX_AGE_S = 3600


def _transcribe_tmp_dir(config: AppConfig) -> Path:
    tmp = config.paths.output_dir / _TMP_DIRNAME
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def _cleanup_stale_audio_tmp(tmp_dir: Path) -> int:
    """Remove ``*.wav`` crash leftovers older than an hour. Newly-created files
    during a live run are never that old, so in-flight work is left alone."""
    now = time.time()
    removed = 0
    if not tmp_dir.is_dir():
        return 0
    for p in tmp_dir.glob("*.wav"):
        try:
            if now - p.stat().st_mtime > _STALE_TMP_MAX_AGE_S:
                p.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def _extract_orig_stem(compressed_stem: str) -> str:
    if "_" in compressed_stem:
        _, orig_stem = compressed_stem.split("_", 1)
    else:
        orig_stem = compressed_stem
    return SEGMENT_SUFFIX_RE.sub("", orig_stem)


def _find_original_video(compressed_stem: str, original_cache: dict[str, Path]) -> Path | None:
    """Match the full source stem first so natural ``_partNN`` names stay intact."""
    source_stem = compressed_stem.split("_", 1)[1] if "_" in compressed_stem else compressed_stem
    exact = original_cache.get(source_stem.lower())
    if exact is not None:
        return exact
    stripped = SEGMENT_SUFFIX_RE.sub("", source_stem)
    return original_cache.get(stripped.lower()) if stripped != source_stem else None


def _build_original_stem_map(project_dir: Path | None = None) -> dict[str, Path]:
    from clio.tasks._video_loader import load_selected_videos, stem_to_path_map

    return stem_to_path_map(load_selected_videos(project_dir))


def _get_video_duration(video_path: Path, ffprobe: str) -> float:
    """Use ffprobe to get video duration in seconds."""
    import subprocess as _subprocess

    cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)]
    try:
        r = _subprocess.run(cmd, capture_output=True, text=True, timeout=30, **no_console_kwargs())
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception:
        pass
    return 0.0


def _extract_audio(
    video_path: Path,
    ffmpeg: str,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
    total_duration: float = 0.0,
    tmp_dir: Path | None = None,
) -> Path | None:
    """ffmpeg 提取 16kHz 单声道 WAV，返回临时文件路径。实时输出进度（百分比）。"""
    if tmp_dir is None:
        tmp_dir = Path(tempfile.gettempdir())
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # 预估并预留 PCM 空间（16kHz 单声道 16bit = 32000 B/s）——磁盘不足时在写满
    # 之前就失败，而不是把临时盘撑爆（GAP-P2-08）。
    if total_duration > 0:
        need_bytes = int(total_duration * 16000 * 2 * 1.2)
        try:
            free_bytes = shutil.disk_usage(tmp_dir).free
        except OSError:
            free_bytes = -1
        if free_bytes >= 0 and free_bytes < need_bytes:
            need_mb = f"{need_bytes / 1e6:.0f}"
            free_mb = f"{free_bytes / 1e6:.0f}"
            print(t("task.transcribe.extract_no_space", {"need": need_mb, "free": free_mb}))
            return None
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=str(tmp_dir))
    tmp.close()
    tmp_path = Path(tmp.name)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        str(tmp_path),
    ]
    proc = popen_subprocess(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
    register_process(proc)
    time_pat = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    last_log_pct = -1
    try:
        for line in proc.stderr or []:
            if cancel_event and cancel_event.is_set():
                proc.terminate()
                proc.wait()
                print(t("task.transcribe.extract_cancelled"))
                tmp_path.unlink(missing_ok=True)
                return None
            m = time_pat.search(line)
            if m:
                sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                pct = min(int(sec / total_duration * 100), 100) if total_duration > 0 else 0
                if progress_callback and pct >= 0:
                    progress_callback(pct)
                if pct >= last_log_pct + 5:
                    sec_s = f"{sec:.0f}"
                    total_s = f"{total_duration:.0f}"
                    print(t("task.transcribe.extract_progress_line", {"sec": sec_s, "total": total_s, "pct": pct}))
                    last_log_pct = pct
        proc.wait()
        if proc.returncode != 0:
            print(t("task.transcribe.extract_failed", {"code": proc.returncode}))
            tmp_path.unlink(missing_ok=True)
            return None
        if progress_callback:
            progress_callback(100)
        return tmp_path
    except BaseException:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        tmp_path.unlink(missing_ok=True)
        raise
    finally:
        unregister_process(proc)


def run_transcribe_all(
    config: AppConfig,
    tracker: ProgressTracker | None = None,
    single_file: Path | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    **kwargs: Any,
) -> int:
    if not config.whisper.enabled:
        print(t("task.transcribe.disabled"))
        if tracker:
            tracker.log(t("task.transcribe.disabled_log"))
            tracker.update(phase="transcribe", current=0, total=0, message=t("task.transcribe.disabled_message"))
        return 0
    readiness = check_asr_ready(config)
    if not readiness.ready:
        msg = t("task.transcribe.not_ready", {"engine": readiness.display_name, "error": readiness.error})
        print(t("task.transcribe.warning", {"message": msg}))
        if tracker:
            tracker.error(msg)
        return 1

    transcripts_dir = config.transcripts_dir
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    compressed_dir = config.paths.output_dir / config.analyze.compressed_subdir
    if not compressed_dir.is_dir():
        print(t("task.transcribe.compressed_missing", {"path": compressed_dir}))
        if tracker:
            tracker.log(t("task.transcribe.compressed_missing_log", {"path": compressed_dir}))
            tracker.update(
                phase="transcribe", current=0, total=0, message=t("task.transcribe.compressed_missing_message")
            )
        return 0
    videos = find_videos(compressed_dir)
    if files is not None:
        selected = _selected_stems(files)
        videos = [v for v in videos if _matches_selected_stem(v, selected)]
    total = len(videos)
    if total == 0:
        print(t("task.transcribe.no_videos"))
        return 0

    if tracker:
        tracker.update(phase="transcribe", total=total, current=0, message=t("task.transcribe.phase_title"))
    state = ProcessingState(config.paths.output_dir)
    original_cache = _build_original_stem_map(config.project_dir)
    error_count = 0

    tmp_dir = _transcribe_tmp_dir(config)
    _cleanup_stale_audio_tmp(tmp_dir)

    start_time = time.time()
    for i, compressed_video in enumerate(videos):
        compressed_stem = compressed_video.stem
        out_path = transcripts_dir / f"{compressed_stem}_transcript.json"
        original_video = _find_original_video(compressed_stem, original_cache)
        if original_video is None:
            orig_stem = _extract_orig_stem(compressed_stem)
            print(t("task.transcribe.skip_no_original", {"name": compressed_stem}))
            state.mark(orig_stem, "transcribe", "skipped")
            if tracker:
                tracker.next(message=t("task.transcribe.skip_next", {"name": compressed_stem}))
                tracker.log(t("task.transcribe.skip_no_original_log", {"name": compressed_stem}))
            continue

        orig_stem = original_video.stem

        current_engine = getattr(config.whisper, "engine", "local")
        if not overwrite and config.analyze.skip_existing and out_path.exists():
            try:
                existing = json.loads(out_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print(t("task.transcribe.retranscribe_corrupt", {"name": compressed_stem}))
            else:
                existing_engine = existing.get("engine", "local")
                if existing_engine != current_engine:
                    print(
                        t(
                            "task.transcribe.retranscribe_engine",
                            {"name": compressed_stem, "existing": existing_engine, "current": current_engine},
                        )
                    )
                    if tracker:
                        tracker.log(
                            t(
                                "task.transcribe.engine_switched",
                                {"name": compressed_stem, "existing": existing_engine, "current": current_engine},
                            )
                        )
                else:
                    print(t("task.transcribe.skip_exists", {"name": compressed_stem, "engine": current_engine}))
                    state.mark(orig_stem, "transcribe", "skipped")
                    if tracker:
                        tracker.next(message=t("task.transcribe.skip_next", {"name": compressed_stem}))
                        tracker.log(t("task.transcribe.skip_exists_log", {"name": compressed_stem}))
                    continue

        if cancel_event and cancel_event.is_set():
            print(t("task.transcribe.cancelled"))
            break
        ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
        audio_source = (
            compressed_video if compressed_video.is_file() and not config.compress.remove_audio else original_video
        )
        audio_dur = _get_video_duration(audio_source, ffprobe)

        def _on_extract_progress(pct: int) -> None:
            if tracker:
                tracker.update(
                    phase="transcribe",
                    message=t("task.transcribe.extract_progress", {"name": compressed_stem, "pct": pct}),
                )

        def _on_transcribe_progress(pct: int) -> None:
            if tracker:
                tracker.update(
                    phase="transcribe",
                    message=t("task.transcribe.whisper_progress", {"name": compressed_stem, "pct": pct}),
                )

        wav_path = _extract_audio(
            audio_source,
            ffmpeg,
            progress_callback=_on_extract_progress,
            cancel_event=cancel_event,
            total_duration=audio_dur,
            tmp_dir=tmp_dir,
        )
        if wav_path is None:
            if cancel_event and cancel_event.is_set():
                print(t("task.transcribe.cancel_extract", {"name": compressed_stem}))
                state.mark(orig_stem, "transcribe", "cancelled")
                if tracker:
                    tracker.next(message=t("task.transcribe.cancel", {"name": compressed_stem}))
                    tracker.log(t("task.transcribe.cancel", {"name": compressed_stem}))
                break
            print(t("task.transcribe.skip_no_audio", {"name": compressed_stem}))
            state.mark(orig_stem, "transcribe", "skipped")
            if tracker:
                tracker.next(message=t("task.transcribe.skip_no_audio_next", {"name": compressed_stem}))
                tracker.log(t("task.transcribe.skip_no_audio_log", {"name": compressed_stem}))
            continue

        try:
            if tracker:
                engine_label = current_engine if current_engine != "local" else t("task.transcribe.engine_local_label")
                tracker.update(
                    phase="transcribe",
                    message=t("task.transcribe.transcribing", {"name": compressed_stem, "engine": engine_label}),
                )
                tracker.log(t("task.transcribe.transcribing", {"name": compressed_stem, "engine": engine_label}))
            segments = transcribe_audio(
                wav_path, config, progress_callback=_on_transcribe_progress, cancel_event=cancel_event
            )
            transcript: dict[str, Any] = {
                "source_video": original_video.name,
                "source_stem": compressed_stem,
                "language": config.whisper.language,
                "model_size": config.whisper.model_size,
                "engine": current_engine,
                "segments": segments,
                "generated_at": datetime.now().isoformat(),
            }
            idx = compressed_stem.split("_", 1)[0] if "_" in compressed_stem else ""
            transcript_identity = resolve_identity(compressed_video, idx, project_dir=config.project_dir)
            add_schema_version(transcript)
            transcript["media_identity"] = _identity_to_dict(transcript_identity)
            write_json_atomic(out_path, transcript)
            enriched = enrich_matching_analysis_files(config, transcript)
            if enriched:
                print(t("task.transcribe.aligned", {"count": enriched}))
            state.mark(orig_stem, "transcribe", "done")
            seg_info = (
                t("task.transcribe.seg_count", {"count": len(segments)}) if segments else t("task.transcribe.seg_none")
            )
            elapsed = time.time() - start_time
            pace = elapsed / (i + 1)
            eta = pace * (total - i - 1)
            eta_str = format_duration(eta)
            pace_str = format_duration(pace)
            line = t(
                "task.transcribe.line",
                {
                    "i": i + 1,
                    "total": total,
                    "name": compressed_stem,
                    "info": seg_info,
                    "pace": pace_str,
                    "eta": eta_str,
                },
            )
            print(line)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            err_msg = str(e)
            print(t("task.transcribe.error", {"name": compressed_stem, "detail": err_msg}))
            state.mark(orig_stem, "transcribe", "error")
            if tracker:
                tracker.next(message=t("task.transcribe.failed_next", {"name": compressed_stem}))
                tracker.log(t("task.transcribe.failed_log", {"name": compressed_stem, "detail": err_msg}))
            error_count += 1
            continue
        finally:
            wav_path.unlink(missing_ok=True)

        if tracker:
            tracker.next(message=t("task.transcribe.done_next", {"name": compressed_stem}))
            tracker.log(t("task.transcribe.done_log", {"name": compressed_stem}))

    if error_count > 0:
        msg = t("task.transcribe.completed_with_errors", {"count": error_count})
        print(msg)
        if tracker:
            tracker.log(msg)
            tracker.update(message=msg)
    return 1 if error_count > 0 else 0


def run_transcribe_one(
    config: AppConfig,
    video_path: Path,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[int], None] | None = None,
) -> dict:
    """单文件转录（供 UI rerun 使用）。"""
    import time

    t0 = time.time()
    print(t("task.transcribe.one_start", {"name": video_path.name}))
    if not video_path.is_file():
        return {"error": t("task.transcribe.one_file_missing", {"path": video_path})}
    if cancel_event and cancel_event.is_set():
        return {"error": t("task.transcribe.one_cancelled")}
    readiness = check_asr_ready(config)
    if not readiness.ready:
        return {"error": t("task.transcribe.not_ready", {"engine": readiness.display_name, "error": readiness.error})}
    print(t("task.transcribe.one_extract"))
    if progress_callback:
        progress_callback(0)
    tmp_dir = _transcribe_tmp_dir(config)
    _cleanup_stale_audio_tmp(tmp_dir)
    ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
    ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
    audio_dur = _get_video_duration(video_path, ffprobe)

    def _on_extract(pct: int) -> None:
        if progress_callback:
            progress_callback(int(pct * 0.1))

    wav_path = _extract_audio(
        video_path,
        ffmpeg,
        progress_callback=_on_extract,
        cancel_event=cancel_event,
        total_duration=audio_dur,
        tmp_dir=tmp_dir,
    )
    if wav_path is None:
        return {"error": t("task.transcribe.one_extract_failed")}
    wav_size = wav_path.stat().st_size
    wav_kb = f"{wav_size / 1024:.0f}"
    elapsed_s = f"{time.time() - t0:.1f}"
    print(t("task.transcribe.one_wav_done", {"name": wav_path.name, "kb": wav_kb, "seconds": elapsed_s}))
    try:
        model = config.whisper.model_size
        print(t("task.transcribe.one_load_model", {"model": model}))
        if progress_callback:
            progress_callback(10)
        t1 = time.time()
        segments = transcribe_audio(
            wav_path,
            config,
            progress_callback=(lambda pct: progress_callback(10 + int(pct * 0.8))) if progress_callback else None,
            cancel_event=cancel_event,
        )
        t2 = time.time()
        took_s = f"{t2 - t1:.1f}"
        print(t("task.transcribe.one_done", {"count": len(segments), "seconds": took_s}))
        transcript: dict[str, Any] = {
            "source_video": video_path.name,
            "source_stem": video_path.stem,
            "language": config.whisper.language,
            "model_size": config.whisper.model_size,
            "engine": getattr(config.whisper, "engine", "local"),
            "segments": segments,
            "generated_at": datetime.now().isoformat(),
        }
        idx = video_path.stem.split("_", 1)[0] if "_" in video_path.stem else ""
        transcript_identity = resolve_identity(video_path, idx, project_dir=config.project_dir)
        add_schema_version(transcript)
        transcript["media_identity"] = _identity_to_dict(transcript_identity)
        transcripts_dir = config.transcripts_dir
        transcripts_dir.mkdir(parents=True, exist_ok=True)
        out_path = transcripts_dir / f"{video_path.stem}_transcript.json"
        write_json_atomic(out_path, transcript)
        enrich_matching_analysis_files(config, transcript)
        print(t("task.transcribe.one_saved", {"path": out_path}))
        return transcript
    finally:
        wav_path.unlink(missing_ok=True)
        total_s = f"{time.time() - t0:.1f}"
        print(t("task.transcribe.one_tmp_removed", {"seconds": total_s}))

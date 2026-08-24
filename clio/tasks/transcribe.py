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

from clio.config import AppConfig
from clio.identity import _identity_to_dict, resolve_identity
from clio.log import format_duration
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.schema import add_schema_version
from clio.tasks._helpers import _matches_selected_stem, _selected_stems
from clio.tasks.transcript_align import enrich_matching_analysis_files
from clio.transcribe import check_whisper, transcribe_audio
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
    return re.sub(r"_seg\d+$", "", orig_stem)
    if "_" in compressed_stem:
        _, orig_stem = compressed_stem.split("_", 1)
    else:
        orig_stem = compressed_stem
    return re.sub(r"_seg\d+$", "", orig_stem)


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
            print(f"  [提取音频] 临时盘空间不足：需要约 {need_bytes / 1e6:.0f}MB，当前剩余 {free_bytes / 1e6:.0f}MB")
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
                print("  [提取音频] 被用户取消")
                tmp_path.unlink(missing_ok=True)
                return None
            m = time_pat.search(line)
            if m:
                sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                pct = min(int(sec / total_duration * 100), 100) if total_duration > 0 else 0
                if progress_callback and pct >= 0:
                    progress_callback(pct)
                if pct >= last_log_pct + 5:
                    print(f"  [提取音频] {sec:.0f}s / {total_duration:.0f}s ({pct}%)")
                    last_log_pct = pct
        proc.wait()
        if proc.returncode != 0:
            print(f"  [ffmpeg] 提取失败 (code {proc.returncode})")
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
        print("Whisper 转录未启用（whisper.enabled=false），跳过")
        if tracker:
            tracker.log("Whisper 转录未启用，跳过")
            tracker.update(phase="transcribe", current=0, total=0, message="Whisper 未启用，跳过")
        return 0
    if not check_whisper():
        msg = "faster-whisper 未安装，跳过转录。执行: python main.py whisper install"
        print(f"警告：{msg}")
        if tracker:
            tracker.error(msg)
        return 1

    transcripts_dir = config.transcripts_dir
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    compressed_dir = config.paths.output_dir / config.analyze.compressed_subdir
    if not compressed_dir.is_dir():
        print(f"压缩目录不存在: {compressed_dir}，请先运行压缩步骤")
        if tracker:
            tracker.log(f"压缩目录不存在: {compressed_dir}")
            tracker.update(phase="transcribe", current=0, total=0, message="压缩目录不存在，跳过")
        return 0
    videos = find_videos(compressed_dir)
    if files is not None:
        selected = _selected_stems(files)
        videos = [v for v in videos if _matches_selected_stem(v, selected)]
    total = len(videos)
    if total == 0:
        print("没有找到需要转录的视频")
        return 0

    if tracker:
        tracker.update(phase="transcribe", total=total, current=0, message="Whisper 语音转录...")
    state = ProcessingState(config.paths.output_dir)
    original_cache = _build_original_stem_map(config.project_dir)
    error_count = 0

    tmp_dir = _transcribe_tmp_dir(config)
    _cleanup_stale_audio_tmp(tmp_dir)

    start_time = time.time()
    for i, compressed_video in enumerate(videos):
        compressed_stem = compressed_video.stem
        out_path = transcripts_dir / f"{compressed_stem}_transcript.json"
        orig_stem = _extract_orig_stem(compressed_stem)
        original_video = original_cache.get(orig_stem.lower())
        if original_video is None:
            print(f"  [跳过] {compressed_stem}: 未找到原始视频")
            state.mark(orig_stem, "transcribe", "skipped")
            if tracker:
                tracker.next(message=f"跳过 {compressed_stem}")
                tracker.log(f"跳过 {compressed_stem}（无原始文件）")
            continue

        orig_stem = original_video.stem

        current_engine = getattr(config.whisper, "engine", "local")
        if not overwrite and config.analyze.skip_existing and out_path.exists():
            try:
                existing = json.loads(out_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print(f"  [重新转录] {compressed_stem} (已有文件损坏)")
            else:
                existing_engine = existing.get("engine", "local")
                if existing_engine != current_engine:
                    print(
                        f"[跳过] {compressed_stem} (已有转录 engine={existing_engine}, "
                        f"当前配置 {current_engine}；如需重跑请使用 --force)"
                    )
                else:
                    print(f"[跳过] {compressed_stem} (已有转录, engine={current_engine})")
                state.mark(orig_stem, "transcribe", "skipped")
                if tracker:
                    tracker.next(message=f"跳过 {compressed_stem}")
                    tracker.log(f"跳过 {compressed_stem}（已转录）")
                continue

        if cancel_event and cancel_event.is_set():
            print("  [取消] 转录阶段被终止")
            break
        ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
        ffprobe = resolve_binary(config.paths.ffprobe, "ffprobe")
        audio_source = (
            compressed_video if compressed_video.is_file() and not config.compress.remove_audio else original_video
        )
        audio_dur = _get_video_duration(audio_source, ffprobe)

        def _on_extract_progress(pct: int) -> None:
            if tracker:
                tracker.update(phase="transcribe", message=f"{compressed_stem}: 提取音频 ({pct}%)")

        def _on_transcribe_progress(pct: int) -> None:
            if tracker:
                tracker.update(phase="transcribe", message=f"{compressed_stem}: Whisper 转录 ({pct}%)")

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
                print(f"  [取消] {compressed_stem}: 音频提取被用户中断")
                state.mark(orig_stem, "transcribe", "cancelled")
                if tracker:
                    tracker.next(message=f"取消 {compressed_stem}")
                    tracker.log(f"取消 {compressed_stem}")
                break
            print(f"  [跳过] {compressed_stem}: 音频提取失败（可能无音轨）")
            state.mark(orig_stem, "transcribe", "skipped")
            if tracker:
                tracker.next(message=f"跳过 {compressed_stem} (no audio)")
                tracker.log(f"跳过 {compressed_stem}（无音轨）")
            continue

        try:
            if tracker:
                engine_label = current_engine if current_engine != "local" else "本地 Whisper"
                tracker.update(phase="transcribe", message=f"{compressed_stem}: {engine_label} 转录中...")
                tracker.log(f"{compressed_stem}: {engine_label} 转录中...")
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
                print(f"  [对齐] 已更新 {enriched} 个分析 timeline 的语音片段")
            state.mark(orig_stem, "transcribe", "done")
            seg_info = f"{len(segments)} 段" if segments else "无有效内容"
            elapsed = time.time() - start_time
            pace = elapsed / (i + 1)
            eta = pace * (total - i - 1)
            eta_str = format_duration(eta)
            pace_str = format_duration(pace)
            line = f"  [转录 {i + 1}/{total}] {compressed_stem}（{seg_info}，平均 {pace_str}，剩余 ~{eta_str}）"
            print(line)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            err_msg = str(e)
            print(f"  [错误] {compressed_stem}: {err_msg}")
            state.mark(orig_stem, "transcribe", "error")
            if tracker:
                tracker.next(message=f"失败 {compressed_stem}")
                tracker.log(f"转录 {compressed_stem} 失败: {err_msg}")
            error_count += 1
            continue
        finally:
            wav_path.unlink(missing_ok=True)

        if tracker:
            tracker.next(message=f"完成 {compressed_stem}")
            tracker.log(f"转录 {compressed_stem} ✓")

    if error_count > 0:
        msg = f"转录完成（{error_count} 个文件失败）"
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

    if not check_whisper():
        return {"error": "faster-whisper 未安装，无法转录。执行: python main.py whisper install"}

    t0 = time.time()
    print(f"  [transcribe_one] 开始处理: {video_path.name}")
    if not video_path.is_file():
        return {"error": f"文件不存在: {video_path}"}
    if cancel_event and cancel_event.is_set():
        return {"error": "转录被用户取消"}
    print("  [transcribe_one] 提取音频...")
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
        return {"error": "音频提取失败"}
    wav_size = wav_path.stat().st_size
    print(f"  [transcribe_one] WAV 提取完成: {wav_path.name} ({wav_size / 1024:.0f} KB)，耗时 {time.time() - t0:.1f}s")
    try:
        model = config.whisper.model_size
        print(f"  [transcribe_one] 开始加载模型 ({model})...")
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
        print(f"  [transcribe_one] Whisper 完成: {len(segments)} 段, 耗时 {t2 - t1:.1f}s")
        transcript: dict[str, Any] = {
            "source_video": video_path.name,
            "source_stem": video_path.stem,
            "language": config.whisper.language,
            "model_size": config.whisper.model_size,
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
        print(f"  [transcribe_one] ✓ 已保存到 {out_path}")
        return transcript
    finally:
        wav_path.unlink(missing_ok=True)
        print(f"  [transcribe_one] 临时 WAV 已删除，总计耗时 {time.time() - t0:.1f}s")

"""Lazy audio waveform peaks cache for the player UI.

Peaks are extracted with ffmpeg (mono PCM), binned max-abs, stored under
output/waveforms/<sha1-key>.json. Generation is non-blocking: ensure_waveform
returns ready or pending and may start a daemon job with file locks.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

WAVEFORM_VERSION = 1
STALE_SEC = 900
# After a failed extract, do not re-kick until this cool-down elapses.
ERROR_COOLDOWN_SEC = 60
MAX_CONCURRENT_JOBS = 2
_MIN_BINS, _MAX_BINS = 400, 2000

_jobs_lock = threading.Lock()
# BoundedSemaphore now owns concurrency accounting: waiting threads block
# without holding _jobs_lock, so a finishing job can always release a slot.
_job_slots = threading.BoundedSemaphore(MAX_CONCURRENT_JOBS)
# Keys with a live job thread in *this* process (orphan recovery after restart).
_active_job_keys: set[str] = set()
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def _pid_alive(pid: int) -> bool:
    """True if process *pid* appears to still be running."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _file_fingerprint(source_path: Path) -> str:
    try:
        st = source_path.stat()
        size = st.st_size
        mtime = int(st.st_mtime * 1000)
    except OSError:
        size, mtime = 0, 0
    head = b""
    try:
        with open(source_path, "rb") as f:
            head = f.read(1024)
    except OSError:
        pass
    return f"{size}:{mtime}:{hashlib.md5(head).hexdigest()[:8]}"


def cache_key(source_path: Path) -> str:
    resolved = str(Path(source_path).resolve()).replace("\\", "/").lower()
    fp = _file_fingerprint(source_path)
    return hashlib.sha1(f"{resolved}:{fp}".encode()).hexdigest()[:16]


def waveforms_dir(project_output: Path) -> Path:
    return Path(project_output) / "waveforms"


def ready_path(project_output: Path, key: str) -> Path:
    return waveforms_dir(project_output) / f"{key}.json"


def lock_path(project_output: Path, key: str) -> Path:
    return waveforms_dir(project_output) / f"{key}.generating"


def bin_count_for_duration(duration_sec: float) -> int:
    d = max(0.0, float(duration_sec or 0.0))
    return max(_MIN_BINS, min(_MAX_BINS, int(round(d * 2))))


def peaks_from_pcm_s16le(pcm: bytes, *, bin_count: int) -> list[float]:
    n = len(pcm) // 2
    if n <= 0 or bin_count <= 0:
        return [0.0] * max(bin_count, 0)
    peaks = [0.0] * bin_count
    fmt = struct.Struct("<h")
    for i in range(n):
        s = fmt.unpack_from(pcm, i * 2)[0]
        b = min(bin_count - 1, int(i * bin_count / n))
        a = abs(s) / 32768.0
        if a > peaks[b]:
            peaks[b] = a
    m = max(peaks) if peaks else 0.0
    if m > 0:
        peaks = [p / m for p in peaks]
    return peaks


def read_peaks(project_output: Path, key: str) -> dict[str, Any] | None:
    p = ready_path(project_output, key)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != WAVEFORM_VERSION or not isinstance(data.get("peaks"), list):
        return None
    if data.get("probe_error"):
        return None
    data.setdefault("status", "ready")
    return data


def write_peaks_atomic(project_output: Path, key: str, payload: dict[str, Any]) -> Path:
    from clio.utils import write_text_atomic

    dest = ready_path(project_output, key)
    body = dict(payload)
    body["status"] = "ready"
    body["version"] = WAVEFORM_VERSION
    write_text_atomic(dest, json.dumps(body, ensure_ascii=False))
    return dest


def lock_status(project_output: Path, key: str, *, now: float | None = None) -> Literal["none", "pending", "stale"]:
    """Classify .generating lock: none | pending | stale.

    Stale when:
    - lock JSON unreadable
    - age > STALE_SEC (absolute safety for long/hung ffmpeg)
    - recorded pid is dead (server restart mid-job — main orphan case)
    - lock is from *this* pid but no in-process job for *key* (thread died without cleanup)
    """
    lp = lock_path(project_output, key)
    if not lp.is_file():
        return "none"
    try:
        data = json.loads(lp.read_text(encoding="utf-8"))
        started = float(data.get("started_at") or 0)
        pid = int(data.get("pid") or 0)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return "stale"
    t = time.time() if now is None else now
    if t - started > STALE_SEC:
        return "stale"
    if pid and not _pid_alive(pid):
        return "stale"
    # Same process: lock exists but no live job → orphaned after thread crash
    if pid == os.getpid():
        with _jobs_lock:
            if key not in _active_job_keys:
                return "stale"
    return "pending"


def write_lock(project_output: Path, key: str, source_path: Path, *, now: float | None = None) -> None:
    d = waveforms_dir(project_output)
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "started_at": time.time() if now is None else now,
        "source_path": str(source_path),
        "pid": os.getpid(),
    }
    lock_path(project_output, key).write_text(json.dumps(payload), encoding="utf-8")


def clear_lock(project_output: Path, key: str) -> None:
    lock_path(project_output, key).unlink(missing_ok=True)


def error_path(project_output: Path, key: str) -> Path:
    return waveforms_dir(project_output) / f"{key}.error"


def recent_error(
    project_output: Path, key: str, *, now: float | None = None, cooldown_sec: float = ERROR_COOLDOWN_SEC
) -> str | None:
    """Return error message if a cool-down error file is still active."""
    ep = error_path(project_output, key)
    if not ep.is_file():
        return None
    try:
        mtime = ep.stat().st_mtime
        age = (time.time() if now is None else now) - mtime
        if age > cooldown_sec:
            return None
        return ep.read_text(encoding="utf-8", errors="replace").strip() or "waveform extract failed"
    except OSError:
        return None


def clear_error(project_output: Path, key: str) -> None:
    error_path(project_output, key).unlink(missing_ok=True)


def _key_lock(key: str) -> threading.Lock:
    with _key_locks_guard:
        if key not in _key_locks:
            _key_locks[key] = threading.Lock()
        return _key_locks[key]


def _spawn_job(fn: Callable[[], None]) -> None:
    t = threading.Thread(target=fn, daemon=True)
    t.start()


def has_audio_stream(video_path: Path, ffprobe: str) -> bool:
    """True if the media file has at least one audio stream (via ffprobe).

    Non-fatal: returns True on any probe failure so callers fall back to the
    old behavior (ffmpeg will surface a real error) rather than mis-skipping.
    """
    from clio.utils import resolve_binary, run_subprocess

    try:
        ffprobe_bin = resolve_binary(ffprobe or "", "ffprobe")
    except Exception:
        # No usable ffprobe → cannot verify; let the work proceed normally.
        return True
    try:
        result = run_subprocess(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
        )
    except Exception:
        return True
    return bool((result.stdout or "").strip())


def extract_peaks_for_video(
    video_path: Path,
    ffmpeg: str,
    *,
    duration_sec: float | None = None,
    audio_source: str = "original",
    ffprobe: str = "",
) -> dict[str, Any]:
    """Extract mono s16le via temp wav (8kHz is enough for amplitude peaks)."""
    from clio.utils import get_duration_sec, resolve_binary, run_ffmpeg

    video_path = Path(video_path)
    # Same contract as compress/cut: empty configured → PATH / known install roots.
    ffmpeg_bin = resolve_binary(ffmpeg or "", "ffmpeg")
    dur = duration_sec
    if dur is None or dur <= 0:
        try:
            probe = resolve_binary(ffprobe or "", "ffprobe")
            dur = get_duration_sec(video_path, probe)
        except Exception:
            dur = 0.0
    bins = bin_count_for_duration(dur if dur and dur > 0 else 60.0)
    # No audio stream (e.g. compressed files stripped with -an): a waveform is
    # impossible. Return a clean ready payload instead of crashing ffmpeg with
    # "Invalid argument", and mark it so the UI can show a friendly message.
    if not has_audio_stream(video_path, ffprobe):
        return {
            "version": WAVEFORM_VERSION,
            "source_path": str(video_path.resolve()),
            "audio_source": audio_source,
            "duration_sec": float(dur or 0.0),
            "bin_count": bins,
            "peaks": [],
            "no_audio": True,
            "status": "ready",
        }
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    tmp_path = Path(tmp.name)
    try:
        try:
            run_ffmpeg(
                [
                    "-y",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "8000",
                    "-acodec",
                    "pcm_s16le",
                    str(tmp_path),
                ],
                ffmpeg_bin,
            )
        except Exception as e:
            return {
                "version": WAVEFORM_VERSION,
                "source_path": str(video_path.resolve()),
                "audio_source": audio_source,
                "duration_sec": float(dur or 0.0),
                "bin_count": bins,
                "peaks": [],
                "probe_error": True,
                "probe_error_msg": str(e)[:200],
                "status": "ready",
            }
        raw = tmp_path.read_bytes()
        pcm = raw[44:] if len(raw) > 44 and raw[:4] == b"RIFF" else raw
        peaks = peaks_from_pcm_s16le(pcm, bin_count=bins)
        return {
            "version": WAVEFORM_VERSION,
            "source_path": str(video_path.resolve()),
            "audio_source": audio_source,
            "duration_sec": float(dur or 0.0),
            "bin_count": bins,
            "peaks": peaks,
            "status": "ready",
        }
    finally:
        tmp_path.unlink(missing_ok=True)


def ensure_waveform(
    project_output: Path,
    source_path: Path,
    ffmpeg: str,
    *,
    duration_sec: float | None = None,
    audio_source: str = "original",
    ffprobe: str = "",
) -> dict[str, Any]:
    source_path = Path(source_path)
    key = cache_key(source_path)
    with _key_lock(key):
        ready = read_peaks(project_output, key)
        if ready is not None:
            return ready
        # Missing binary is an environment issue — do not lock or cool-down.
        from clio.utils import probe_ffmpeg_deps

        deps = probe_ffmpeg_deps(ffmpeg or "", ffprobe or "")
        if not deps["ok"]:
            return {
                "status": "error",
                "error": deps["detail"] or "找不到 ffmpeg/ffprobe",
                "code": "missing_binary",
                "key": key,
            }
        err_msg = recent_error(project_output, key)
        if err_msg is not None:
            return {"status": "error", "error": err_msg, "key": key}
        st = lock_status(project_output, key)
        if st == "stale":
            clear_lock(project_output, key)
            st = "none"
        if st == "pending":
            try:
                data = json.loads(lock_path(project_output, key).read_text(encoding="utf-8"))
                started = float(data.get("started_at") or time.time())
            except Exception:
                started = time.time()
            return {"status": "pending", "started_at": started, "key": key}

        write_lock(project_output, key, source_path)
        clear_error(project_output, key)
        started_at = time.time()

        def _job() -> None:
            _job_slots.acquire()
            try:
                payload = extract_peaks_for_video(
                    source_path,
                    ffmpeg,
                    duration_sec=duration_sec,
                    audio_source=audio_source,
                    ffprobe=ffprobe,
                )
                write_peaks_atomic(project_output, key, payload)
                clear_error(project_output, key)
            except Exception as e:
                try:
                    error_path(project_output, key).write_text(str(e)[:500], encoding="utf-8")
                except OSError:
                    pass
            finally:
                clear_lock(project_output, key)
                with _jobs_lock:
                    _active_job_keys.discard(key)
                _job_slots.release()

        # Register key before spawn so concurrent GETs see pending (not same-pid stale).
        with _jobs_lock:
            _active_job_keys.add(key)
        try:
            _spawn_job(_job)
        except Exception:
            with _jobs_lock:
                _active_job_keys.discard(key)
            clear_lock(project_output, key)
            raise
        return {"status": "pending", "started_at": started_at, "key": key}

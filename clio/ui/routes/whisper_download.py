"""Whisper model download — thread, progress, and install handlers."""

from __future__ import annotations

import contextvars
import dataclasses
import json
import os
import re
import subprocess
import sys as _sys
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import requests as _req

from clio.config import AppConfig, load_config
from clio.i18n import t
from clio.task_center.manager import TaskAlreadyRunningError, TaskManager, TaskNotCancellableError
from clio.task_center.models import TaskKind, TaskStatus
from clio.task_center.reporter import TaskCancelled
from clio.task_center.store import TaskNotFoundError, TaskQuery
from clio.transcribe import (
    PROJECT_ROOT,
    _clear_model_cache,
    _get_model,
    _reload_whisper_import,
    _resolve_cache_dir,
    check_cublas,
    pip_mirror_for_config,
)
from clio.ui.handler_protocol import HandlerProtocol
from clio.utils import no_console_kwargs, run_subprocess
from clio.whisper_cache import (
    REQUIRED_MODEL_FILES,
    ensure_model_cache_refs,
    is_model_cache_complete,
    model_snapshot_dir,
)

_INSTALL_LOCK = threading.Lock()


@dataclasses.dataclass
class _DownloadTask:
    project_id: str
    thread: threading.Thread
    cancel: threading.Event


_PROJECT_TASKS: dict[str, _DownloadTask] = {}


class _InstallHandler(Protocol):
    def _resolve_project_dir(self, qs: dict[str, Any]) -> Path: ...
    def _get_config(self, project_dir: Path) -> AppConfig: ...


_INSTALL_REPORTER: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "clio_whisper_install_reporter", default=None
)


def _is_frozen() -> bool:
    """True when running from a PyInstaller bundle (clio.exe)."""
    return bool(getattr(_sys, "frozen", False))


def _install_progress_path(handler: HandlerProtocol, qs: dict[str, Any]) -> Path:
    proj_out = handler._get_project_output(qs)
    return proj_out / ".whisper_install.json"


def _write_install_progress(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = os.urandom(4).hex()
    tmp = path.parent / f"{path.name}.{suffix}.tmp"
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        tmp.unlink(missing_ok=True)
    reporter = _INSTALL_REPORTER.get()
    if reporter is not None:
        pct = data.get("progress_pct")
        reporter.progress(
            phase="whisper_install",
            current=int(pct) if isinstance(pct, (int, float)) else 0,
            total=100,
            message=str(data.get("message") or ""),
        )


_PIP_PROGRESS_RE = re.compile(r"([\d.]+)\s*/\s*[\d.]+\s*MB\s*([\d.]+)%")


def _parse_pip_progress(line: str) -> int:
    """Extract download percentage from a pip progress-bar line, or 0 if none."""
    m = _PIP_PROGRESS_RE.search(line)
    if m:
        try:
            return int(float(m.group(2)))
        except ValueError:
            return 0
    return 0


def _pip_install_streaming(
    packages: list[str],
    progress_path: Path,
    label: str,
    cancel: threading.Event,
    pip_index: str | None = None,
) -> tuple[bool, str]:
    """Run pip install with streaming progress updates to the progress file.

    Returns (ok, stderr_tail). Unlike run_subprocess(capture_output=True),
    this does not block silently — pip output is surfaced line by line, and an
    elapsed-time counter keeps the UI moving during silent large downloads
    (pip buffers its progress bar when stdout is a pipe, not a TTY).
    """
    if _is_frozen():
        return False, t("api.whisper_frozen_install")
    cmd = [_sys.executable, "-m", "pip", "install", "--no-input"]
    if pip_index:
        cmd += ["-i", pip_index]
    cmd += [*packages]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            **no_console_kwargs(),
        )
    except OSError as e:
        return False, str(e)
    stderr_tail = []
    assert proc.stdout is not None
    start = time.monotonic()
    last_beat = 0.0
    buf = ""
    while True:
        if cancel.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            return False, t("api.whisper_cancelled")
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            line = line.rstrip("\r")
            if not line.strip():
                continue
            stderr_tail.append(line)
            if len(stderr_tail) > 5:
                stderr_tail.pop(0)
            pct = _parse_pip_progress(line)
            _write_install_progress(
                progress_path,
                {
                    "status": "downloading",
                    "progress_pct": pct,
                    "message": f"{label}: {line[:80]}",
                },
            )
            last_beat = time.monotonic()
        now = time.monotonic()
        if now - last_beat > 3.0:
            last_beat = now
            elapsed = int(now - start)
            _write_install_progress(
                progress_path,
                {
                    "status": "downloading",
                    "progress_pct": 0,
                    "message": t("api.whisper_download_waiting", {"label": label, "elapsed": elapsed}),
                },
            )
    proc.wait()
    return proc.returncode == 0, "\n".join(stderr_tail[-3:])


def _project_id_from_qs(handler: HandlerProtocol, qs: dict[str, Any]) -> str:
    return str(handler._resolve_project_dir(qs).resolve())


def _managed_task_manager(handler: Any) -> TaskManager | None:
    try:
        manager = handler._get_task_manager()
    except (AttributeError, TypeError):
        return None
    return manager if isinstance(manager, TaskManager) else None


class _ManagedInstallHandler:
    def __init__(self, context):
        self._context = context

    def _resolve_project_dir(self, qs: dict[str, Any]) -> Path:
        del qs
        return Path(self._context.task.project_path or self._context.input_data["project_dir"])

    def _get_config(self, project_dir: Path) -> AppConfig:
        config_path = self._context.input_data.get("config_path") or "config.yaml"
        return load_config(config_path, project_dir=project_dir)


def _run_whisper_install_task(context) -> dict[str, Any]:
    progress_path = Path(context.input_data["progress_path"])
    token = _INSTALL_REPORTER.set(context.reporter)
    try:
        try:
            _run_install(_ManagedInstallHandler(context), {}, progress_path, context.cancel_event)
        except TaskCancelled:
            raise
        except Exception as e:
            _write_install_progress(
                progress_path,
                {
                    "status": "error",
                    "progress_pct": 0,
                    "message": t("api.whisper_install_failed_detail", {"detail": e}),
                },
            )
            raise
        if context.cancel_event.is_set():
            raise TaskCancelled(t("api.whisper_download_cancelled"))
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(t("api.whisper_status_read_failed", {"detail": e})) from e
        if data.get("status") == "error":
            raise RuntimeError(str(data.get("message") or t("api.whisper_install_failed")))
        if data.get("status") != "done":
            raise RuntimeError(str(data.get("message") or t("api.whisper_install_incomplete")))
        return {"model": context.input_data.get("model"), "artifact": "whisper-install"}
    finally:
        _INSTALL_REPORTER.reset(token)


def _ensure_whisper_handler(manager: TaskManager) -> None:
    manager.ensure_registered(
        TaskKind.WHISPER_INSTALL,
        _run_whisper_install_task,
        concurrency_key=lambda task: f"whisper:{task.project_id or task.project_path}",
        max_concurrency=1,
        cancellable=True,
    )


def _active_whisper_task(manager: TaskManager, project_id: str):
    tasks = manager.store.list(
        TaskQuery(
            project_id=project_id,
            statuses=(TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING),
            kinds=(TaskKind.WHISPER_INSTALL,),
            visibility=None,
            limit=1,
        )
    )
    return tasks[0] if tasks else None


def handle_get_whisper_install_status(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    manager = _managed_task_manager(handler)
    if manager is not None:
        task = _active_whisper_task(manager, _project_id_from_qs(handler, qs))
        if task is not None:
            return handler._send_json(
                {
                    "status": "downloading",
                    "running": True,
                    "task_id": task.id,
                    "progress_pct": task.progress_pct or 0,
                    "message": task.message,
                }
            )
    path = _install_progress_path(handler, qs)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {"status": "idle"}
    else:
        data = {"status": "idle"}
    proj_id = _project_id_from_qs(handler, qs)
    with _INSTALL_LOCK:
        task = _PROJECT_TASKS.get(proj_id)
        running = task is not None and task.thread.is_alive()
    if data.get("status") in ("downloading",) and not running:
        data = {"status": "idle", "message": t("api.whisper_download_interrupted")}
    data["running"] = running
    handler._send_json(data)


def handle_post_whisper_install(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    proj_id = _project_id_from_qs(handler, qs)

    manager = _managed_task_manager(handler)
    if manager is not None:
        _ensure_whisper_handler(manager)
        if _active_whisper_task(manager, proj_id) is not None:
            return handler._send_json({"ok": False, "error": "download is already running"}, 409)
        proj_dir = handler._resolve_project_dir(qs)
        cfg = handler._get_config(proj_dir)
        progress_path = _install_progress_path(handler, qs)
        config_path = getattr(handler, "config_path", None)
        try:
            managed_task = manager.submit(
                TaskKind.WHISPER_INSTALL,
                t("api.whisper_install_task_title"),
                project_id=proj_id,
                project_name=proj_dir.name,
                project_path=proj_id,
                input_data={
                    "config_path": str(config_path) if isinstance(config_path, Path) else None,
                    "project_dir": proj_id,
                    "progress_path": str(progress_path),
                    "model": getattr(getattr(cfg, "whisper", None), "model_size", None),
                },
                input_summary={"model": getattr(getattr(cfg, "whisper", None), "model_size", None)},
                reject_if_active=True,
            )
        except TaskAlreadyRunningError:
            return handler._send_json({"ok": False, "error": "download is already running"}, 409)
        return handler._send_json(
            {
                "ok": True,
                "message": "whisper install started",
                "task_id": managed_task.id,
                "task": managed_task.to_dict(),
            }
        )

    with _INSTALL_LOCK:
        download_task = _PROJECT_TASKS.get(proj_id)
        if download_task is not None and download_task.thread.is_alive():
            handler._send_json({"ok": False, "error": "download is already running"}, 409)
            return
        progress_path = _install_progress_path(handler, qs)
        cancel = threading.Event()

        def _worker() -> None:
            try:
                _run_install(handler, qs, progress_path, cancel)
            except Exception as e:  # noqa: BLE001 - daemon worker must persist any failure to progress
                _write_install_progress(
                    progress_path,
                    {
                        "status": "error",
                        "progress_pct": 0,
                        "message": t("api.whisper_install_failed_detail", {"detail": e}),
                    },
                )

        thread = threading.Thread(target=_worker, daemon=True)
        _PROJECT_TASKS[proj_id] = _DownloadTask(project_id=proj_id, thread=thread, cancel=cancel)
        thread.start()

    handler._send_json({"ok": True, "message": "whisper install started"})


def handle_post_whisper_install_cancel(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    proj_id = _project_id_from_qs(handler, qs)
    manager = _managed_task_manager(handler)
    if manager is not None:
        task = _active_whisper_task(manager, proj_id)
        if task is None:
            return handler._send_json({"ok": True, "message": t("api.whisper_no_install_task")})
        try:
            cancelled = manager.request_cancel(task.id)
        except (TaskNotFoundError, TaskNotCancellableError) as e:
            return handler._send_json({"ok": False, "error": str(e)}, 409)
        return handler._send_json(
            {"ok": True, "message": "cancel requested", "task_id": cancelled.id, "task": cancelled.to_dict()}
        )
    with _INSTALL_LOCK:
        task = _PROJECT_TASKS.get(proj_id)
        if task is not None:
            task.cancel.set()
    path = _install_progress_path(handler, qs)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") == "downloading":
                _write_install_progress(
                    path,
                    {"status": "idle", "progress_pct": 0, "message": t("api.whisper_download_cancelled")},
                )
        except (json.JSONDecodeError, OSError):
            pass
    handler._send_json({"ok": True, "message": "cancel requested"})


_KNOWN_MODEL_SIZES: dict[str, int] = {
    "tiny": 151_362_048,
    "base": 290_574_848,
    "small": 483_382_016,
    "medium": 1_536_351_744,
    "large-v2": 3_076_648_960,
    "large-v3": 3_076_648_960,
}


def _get_remote_file_size(repo_id: str, filename: str, cfg: Any) -> int:
    import requests as _req
    from huggingface_hub import hf_hub_url

    proxy_url = cfg.proxy.url if (cfg.proxy.enabled and cfg.proxy.url) else None
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    try:
        url = hf_hub_url(repo_id, filename=filename)
        r = _req.head(url, proxies=proxies, timeout=(15, 60), allow_redirects=True)
        r.raise_for_status()
        size = int(r.headers.get("Content-Length", 0))
        if size:
            return size
    except (OSError, TypeError, ValueError, _req.RequestException):
        pass
    return 0


def _get_model_download_size(repo_id: str, cfg: Any) -> int:
    total = 0
    for filename in REQUIRED_MODEL_FILES:
        total += _get_remote_file_size(repo_id, filename, cfg)
    if total:
        return total
    for key, sz in _KNOWN_MODEL_SIZES.items():
        if key in repo_id:
            return sz
    return 0


def _sanitize_url(url: str | None) -> str:
    """Remove userinfo (credentials) from a URL for safe logging."""
    if not url:
        return ""
    try:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc += f":{parsed.port}"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    except (AttributeError, TypeError, ValueError):
        pass
    return url


def _download_error_detail(e: Exception, cfg: Any) -> str:
    raw_proxy_url = cfg.proxy.url if (cfg.proxy.enabled and cfg.proxy.url) else None
    proxy_url = _sanitize_url(raw_proxy_url)
    endpoint = _sanitize_url(cfg.whisper.hf_endpoint) or t("api.whisper_hf_endpoint_default")
    msg = [t("api.whisper_download_failed", {"detail": e})]
    msg.append(f"  Endpoint: {endpoint}")
    msg.append(f"  Proxy: {proxy_url or t('api.whisper_proxy_none')}")
    if "10061" in str(e) or "refused" in str(e).lower():
        msg.append(t("api.whisper_hint_refused_title"))
        msg.append(t("api.whisper_hint_refused_1"))
        msg.append(t("api.whisper_hint_refused_2"))
        msg.append(t("api.whisper_hint_refused_3"))
        msg.append(t("api.whisper_hint_refused_4"))
    if "Read timed out" in str(e):
        msg.append(t("api.whisper_hint_timeout_title"))
        msg.append(t("api.whisper_hint_timeout_1"))
        msg.append(t("api.whisper_hint_timeout_2"))
        msg.append("      whisper:")
        msg.append("        hf_endpoint: https://hf-mirror.com")
        msg.append(t("api.whisper_hint_timeout_3"))
        msg.append("      proxy:")
        msg.append("        enabled: true")
        msg.append("        url: http://127.0.0.1:7890")
    return "\n".join(msg)


def _verify_install(cfg: Any, progress_path: Path, model_name: str) -> bool:
    """Smoke-test the install: confirm cuBLAS is loadable and the model loads.

    Returns True on success; on failure writes an error progress entry and
    returns False so the caller can stop (the install is NOT "done").
    """
    _write_install_progress(
        progress_path,
        {
            "status": "downloading",
            "progress_pct": 100,
            "message": t("api.whisper_verify_install"),
        },
    )
    if not check_cublas():
        _write_install_progress(
            progress_path,
            {
                "status": "error",
                "progress_pct": 100,
                "message": t("api.whisper_cublas_not_ready"),
            },
        )
        return False
    try:
        _get_model(cfg)
    except Exception as e:  # noqa: BLE001 - model loader failures are provider-specific
        _write_install_progress(
            progress_path,
            {
                "status": "error",
                "progress_pct": 100,
                "message": t("api.whisper_model_verify_failed", {"detail": e}),
            },
        )
        return False
    finally:
        _clear_model_cache()
    _write_install_progress(
        progress_path,
        {
            "status": "done",
            "progress_pct": 100,
            "message": t("api.whisper_model_downloaded_ok", {"model": model_name}),
        },
    )
    return True


def _ensure_hf_hub() -> None:
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        r = run_subprocess(
            [_sys.executable, "-m", "pip", "install", "huggingface_hub", "-q"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(t("api.whisper_install_hf_hub_failed", {"detail": r.stderr})) from None


def _install_whisper_packages(progress_path: Path, cancel: threading.Event, pip_index: str | None) -> bool:
    """Install faster-whisper requirements. Returns False when the install failed."""
    _write_install_progress(
        progress_path,
        {
            "status": "downloading",
            "progress_pct": 0,
            "message": t("api.whisper_install_faster_whisper"),
        },
    )
    req_txt = PROJECT_ROOT / "requirements-whisper.txt"
    if not req_txt.is_file():
        return True
    ok, err = _pip_install_streaming(
        ["-r", str(req_txt)], progress_path, t("api.whisper_install_faster_whisper_label"), cancel, pip_index
    )
    if not ok:
        _write_install_progress(
            progress_path,
            {
                "status": "error",
                "progress_pct": 0,
                "message": t("api.whisper_install_faster_whisper_failed", {"detail": err[:200]}),
            },
        )
        return False
    _reload_whisper_import()
    return True


def _install_cublas_packages(progress_path: Path, cancel: threading.Event, pip_index: str | None) -> None:
    _write_install_progress(
        progress_path,
        {
            "status": "downloading",
            "progress_pct": 0,
            "message": t("api.whisper_install_cublas"),
        },
    )
    cublas_pkgs = ["nvidia-cublas-cu12"]
    try:
        from ctranslate2 import get_cuda_device_count

        if get_cuda_device_count() > 0:
            cublas_pkgs.append("nvidia-cudnn-cu12")
    except (ImportError, OSError):
        pass
    if not cublas_pkgs:
        return
    ok, err = _pip_install_streaming(
        cublas_pkgs, progress_path, t("api.whisper_install_cublas_label"), cancel, pip_index
    )
    if not ok:
        _write_install_progress(
            progress_path,
            {
                "status": "downloading",
                "progress_pct": 0,
                "message": t("api.whisper_cublas_install_failed", {"detail": err[:100]}),
            },
        )


@dataclasses.dataclass
class _ModelDownloadState:
    downloaded: int = 0
    start: float = dataclasses.field(default_factory=time.monotonic)
    last_report_time: float = 0.0
    last_pct: int = 0
    total_size: int = 0


def _skip_complete_file(repo_id: str, filename: str, target: Path, cfg: AppConfig, state: _ModelDownloadState) -> bool:
    """Return True when an existing target file can be reused without downloading."""
    if not (target.is_file() and target.stat().st_size > 0):
        return False
    expected_size = _get_remote_file_size(repo_id, filename, cfg)
    if expected_size > 0 and target.stat().st_size != expected_size:
        target.unlink(missing_ok=True)
        return False
    state.downloaded += target.stat().st_size
    return True


def _stream_model_file(
    response: Any,
    tmp_path: Path,
    filename: str,
    model_name: str,
    progress_path: Path,
    state: _ModelDownloadState,
    cancel: threading.Event,
) -> bool:
    """Stream a response body to disk; return True when cancellation was requested."""
    cancelled = False
    with open(tmp_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            if cancel.is_set():
                cancelled = True
                break
            if chunk:
                f.write(chunk)
                state.downloaded += len(chunk)

            now = time.monotonic()
            if now - state.last_report_time < 1.0:
                continue
            state.last_report_time = now
            pct = int(state.downloaded / state.total_size * 100) if state.total_size else 0
            speed_bps = state.downloaded / max(now - state.start, 1.0)
            speed_str = (
                f"{speed_bps / 1024 / 1024:.1f} MB/s"
                if speed_bps > 1024 * 1024
                else f"{speed_bps / 1024:.0f} KB/s"
                if speed_bps
                else ""
            )
            eta_sec = (
                int((state.total_size - state.downloaded) / max(speed_bps, 1))
                if state.total_size and speed_bps
                else None
            )
            if state.total_size and pct >= state.last_pct + 2:
                _write_install_progress(
                    progress_path,
                    {
                        "status": "downloading",
                        "progress_pct": pct,
                        "message": t(
                            "api.whisper_download_model_progress",
                            {"model": model_name, "file": filename, "pct": pct},
                        ),
                        "speed": speed_str,
                        "eta_sec": eta_sec,
                    },
                )
                state.last_pct = pct
            elif not state.total_size:
                _write_install_progress(
                    progress_path,
                    {
                        "status": "downloading",
                        "progress_pct": 0,
                        "message": t(
                            "api.whisper_download_model_elapsed",
                            {
                                "model": model_name,
                                "file": filename,
                                "elapsed": int(now - state.start),
                                "size": _format_bytes(state.downloaded),
                            },
                        ),
                    },
                )
    return cancelled


def _download_one_model_file(
    cfg: AppConfig,
    progress_path: Path,
    model_name: str,
    repo_id: str,
    snapshots: Path,
    filename: str,
    state: _ModelDownloadState,
    proxies: dict[str, str] | None,
    cancel: threading.Event,
) -> bool:
    """Download one model file. Returns False when the download was cancelled."""
    from huggingface_hub import hf_hub_url

    target = snapshots / filename
    if _skip_complete_file(repo_id, filename, target, cfg, state):
        return True
    url = hf_hub_url(repo_id, filename=filename)
    tmp_path = target.with_name(target.name + f".{os.urandom(4).hex()}.tmp")
    try:
        response = _req.get(url, stream=True, proxies=proxies, timeout=(30, 180), allow_redirects=True)
        response.raise_for_status()
    except _req.exceptions.RequestException as e:
        raise RuntimeError(_download_error_detail(e, cfg)) from e

    if not state.total_size:
        state.total_size += int(response.headers.get("Content-Length", 0))

    if _stream_model_file(response, tmp_path, filename, model_name, progress_path, state, cancel):
        tmp_path.unlink(missing_ok=True)
        _write_install_progress(
            progress_path,
            {"status": "idle", "progress_pct": 0, "message": t("api.whisper_download_cancelled")},
        )
        return False

    try:
        tmp_path.replace(target)
    except OSError as e:
        raise RuntimeError(_download_error_detail(e, cfg)) from e
    return True


def _download_model_files(
    cfg: AppConfig,
    progress_path: Path,
    model_name: str,
    repo_id: str,
    snapshots: Path,
    total_size: int,
    proxies: dict[str, str] | None,
    cancel: threading.Event,
) -> bool:
    """Download every required model file. Returns False when cancelled."""
    state = _ModelDownloadState(total_size=total_size)
    for filename in REQUIRED_MODEL_FILES:
        if not _download_one_model_file(
            cfg, progress_path, model_name, repo_id, snapshots, filename, state, proxies, cancel
        ):
            return False
    return True


def _run_install(
    handler: _InstallHandler,
    qs: dict[str, Any],
    progress_path: Path,
    cancel: threading.Event,
) -> None:
    if _is_frozen():
        _write_install_progress(
            progress_path,
            {
                "status": "error",
                "progress_pct": 0,
                "message": t("api.whisper_frozen_install"),
            },
        )
        return
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    pip_index = pip_mirror_for_config(cfg)

    _write_install_progress(
        progress_path,
        {
            "status": "downloading",
            "progress_pct": 0,
            "message": t("api.whisper_check_hf_hub"),
        },
    )
    _ensure_hf_hub()

    if not _install_whisper_packages(progress_path, cancel, pip_index):
        return

    _install_cublas_packages(progress_path, cancel, pip_index)

    cache_dir = _resolve_cache_dir(cfg)
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_name = cfg.whisper.model_size
    repo_id = f"Systran/faster-whisper-{model_name}"

    if is_model_cache_complete(cache_dir, model_name):
        _verify_install(cfg, progress_path, model_name)
        return

    total_size = _get_model_download_size(repo_id, cfg)

    _write_install_progress(
        progress_path,
        {
            "status": "downloading",
            "progress_pct": 0,
            "message": t(
                "api.whisper_downloading_model",
                {"model": model_name, "size": _format_bytes(total_size) if total_size else "..."},
            ),
        },
    )

    proxy_url = cfg.proxy.url if (cfg.proxy.enabled and cfg.proxy.url) else None
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    snapshots = model_snapshot_dir(cache_dir, model_name)
    snapshots.mkdir(parents=True, exist_ok=True)

    if not _download_model_files(cfg, progress_path, model_name, repo_id, snapshots, total_size, proxies, cancel):
        return

    ensure_model_cache_refs(cache_dir, model_name)
    _verify_install(cfg, progress_path, model_name)


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"

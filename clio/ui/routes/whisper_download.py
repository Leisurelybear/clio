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
from typing import Any

import requests as _req

from clio.config import load_config
from clio.task_center.manager import TaskManager, TaskNotCancellableError
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
_INSTALL_REPORTER: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "clio_whisper_install_reporter", default=None
)


def _is_frozen() -> bool:
    """True when running from a PyInstaller bundle (clio.exe)."""
    return bool(getattr(_sys, "frozen", False))


_FROZEN_INSTALL_ERROR = (
    "打包版（clio.exe）不内置 Whisper 依赖，无法在应用内安装。"
    "请使用源码版：进入项目目录运行 `python main.py whisper install`，"
    "或将 faster-whisper 安装到外部 Python 后手动下载模型缓存。"
)


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
        return False, _FROZEN_INSTALL_ERROR
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
            return False, "已取消"
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
                    "message": f"{label}: 下载中... 已等待 {elapsed}s",
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

    def _get_config(self, project_dir: Path):
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
                {"status": "error", "progress_pct": 0, "message": f"安装失败: {e}"},
            )
            raise
        if context.cancel_event.is_set():
            raise TaskCancelled("下载已取消")
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise RuntimeError(f"无法读取 Whisper 安装状态: {e}") from e
        if data.get("status") == "error":
            raise RuntimeError(str(data.get("message") or "Whisper 安装失败"))
        if data.get("status") != "done":
            raise RuntimeError(str(data.get("message") or "Whisper 安装未完成"))
        return {"model": context.input_data.get("model"), "progress_path": str(progress_path)}
    finally:
        _INSTALL_REPORTER.reset(token)


def _ensure_whisper_handler(manager: TaskManager) -> None:
    if TaskKind.WHISPER_INSTALL not in manager.registry.kinds():
        manager.register(
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
        data = {"status": "idle", "message": "上次下载中断，可重新开始"}
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
        task = manager.submit(
            TaskKind.WHISPER_INSTALL,
            "安装 Whisper 依赖和模型",
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
        )
        return handler._send_json(
            {"ok": True, "message": "whisper install started", "task_id": task.id, "task": task.to_dict()}
        )

    with _INSTALL_LOCK:
        task = _PROJECT_TASKS.get(proj_id)
        if task is not None and task.thread.is_alive():
            handler._send_json({"ok": False, "error": "download is already running"}, 409)
            return
        progress_path = _install_progress_path(handler, qs)
        cancel = threading.Event()

        def _worker() -> None:
            try:
                _run_install(handler, qs, progress_path, cancel)
            except Exception as e:
                _write_install_progress(
                    progress_path,
                    {
                        "status": "error",
                        "progress_pct": 0,
                        "message": f"安装失败: {e}",
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
            return handler._send_json({"ok": True, "message": "当前没有运行中的 Whisper 安装任务"})
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
                    {"status": "idle", "progress_pct": 0, "message": "下载已取消"},
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
    except Exception:
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
    except Exception:
        pass
    return url


def _download_error_detail(e: Exception, cfg: Any) -> str:
    raw_proxy_url = cfg.proxy.url if (cfg.proxy.enabled and cfg.proxy.url) else None
    proxy_url = _sanitize_url(raw_proxy_url)
    endpoint = _sanitize_url(cfg.whisper.hf_endpoint) or "https://huggingface.co（未设置镜像）"
    msg = [f"模型下载失败: {e}"]
    msg.append(f"  Endpoint: {endpoint}")
    msg.append(f"  Proxy: {proxy_url or '无'}")
    if "10061" in str(e) or "refused" in str(e).lower():
        msg.append("  提示: 连接被拒绝。请检查:")
        msg.append("    - hf-mirror.com 是否可访问（试试浏览器打开）")
        msg.append("    - 配置文件中 ai.proxy.url 是否正确设置")
        msg.append("    - 如果不需要镜像，删除 config.yaml 中的 ai.whisper.hf_endpoint")
        msg.append("    - 若需代理，在 config.yaml 中配置 proxy: { enabled: true, url: http://127.0.0.1:7890 }")
    if "Read timed out" in str(e):
        msg.append("  提示: 读取超时，常见原因:")
        msg.append("    - 当前 endpoint 是 HuggingFace 官方地址，国内访问不稳定")
        msg.append("    - 建议在 config.yaml 中设置:")
        msg.append("      whisper:")
        msg.append("        hf_endpoint: https://hf-mirror.com")
        msg.append("    - 如果已设置镜像但仍超时，可尝试在 config.yaml 中配置代理:")
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
            "message": "验证安装（加载模型以确认依赖完整）...",
        },
    )
    if not check_cublas():
        _write_install_progress(
            progress_path,
            {
                "status": "error",
                "progress_pct": 100,
                "message": "cuBLAS 仍未就绪，转录将失败。请手动: pip install nvidia-cublas-cu12",
            },
        )
        return False
    try:
        _get_model(cfg)
    except Exception as e:
        _write_install_progress(
            progress_path,
            {
                "status": "error",
                "progress_pct": 100,
                "message": f"模型加载验证失败: {e}",
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
            "message": f"模型 {model_name} 下载完成，验证通过 ✔",
        },
    )
    return True


def _run_install(
    handler: HandlerProtocol,
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
                "message": _FROZEN_INSTALL_ERROR,
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
            "message": "检查 huggingface_hub 依赖...",
        },
    )
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        r = run_subprocess(
            [_sys.executable, "-m", "pip", "install", "huggingface_hub", "-q"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"安装 huggingface_hub 失败: {r.stderr}")

    _write_install_progress(
        progress_path,
        {
            "status": "downloading",
            "progress_pct": 0,
            "message": "安装 faster-whisper...",
        },
    )
    req_txt = PROJECT_ROOT / "requirements-whisper.txt"
    if req_txt.is_file():
        ok, err = _pip_install_streaming(["-r", str(req_txt)], progress_path, "安装 faster-whisper", cancel, pip_index)
        if not ok:
            _write_install_progress(
                progress_path,
                {
                    "status": "error",
                    "progress_pct": 0,
                    "message": f"安装 faster-whisper 失败: {err[:200]}",
                },
            )
            return
        _reload_whisper_import()

    _write_install_progress(
        progress_path,
        {
            "status": "downloading",
            "progress_pct": 0,
            "message": "安装 cuBLAS 库...",
        },
    )
    cublas_pkgs = ["nvidia-cublas-cu12"]
    try:
        from ctranslate2 import get_cuda_device_count

        if get_cuda_device_count() > 0:
            cublas_pkgs.append("nvidia-cudnn-cu12")
    except (ImportError, OSError):
        pass
    if cublas_pkgs:
        ok, err = _pip_install_streaming(cublas_pkgs, progress_path, "安装 cuBLAS", cancel, pip_index)
        if not ok:
            _write_install_progress(
                progress_path,
                {
                    "status": "downloading",
                    "progress_pct": 0,
                    "message": f"cuBLAS 安装失败（{err[:100]}），继续下载模型...",
                },
            )

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
            "message": f"正在下载模型 {model_name} ({_format_bytes(total_size) if total_size else '...'})...",
        },
    )

    from huggingface_hub import hf_hub_url

    proxy_url = cfg.proxy.url if (cfg.proxy.enabled and cfg.proxy.url) else None
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    snapshots = model_snapshot_dir(cache_dir, model_name)
    snapshots.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    start = time.monotonic()
    last_report_time = 0.0
    last_pct = 0

    for filename in REQUIRED_MODEL_FILES:
        target = snapshots / filename
        if target.is_file() and target.stat().st_size > 0:
            expected_size = _get_remote_file_size(repo_id, filename, cfg)
            if expected_size > 0 and target.stat().st_size != expected_size:
                target.unlink(missing_ok=True)
            else:
                downloaded += target.stat().st_size
                continue
        url = hf_hub_url(repo_id, filename=filename)
        tmp_path = target.with_name(target.name + f".{os.urandom(4).hex()}.tmp")
        try:
            response = _req.get(url, stream=True, proxies=proxies, timeout=(30, 180), allow_redirects=True)
            response.raise_for_status()
        except _req.exceptions.RequestException as e:
            raise RuntimeError(_download_error_detail(e, cfg)) from e

        if not total_size:
            total_size += int(response.headers.get("Content-Length", 0))

        cancelled = False
        with open(tmp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if cancel.is_set():
                    cancelled = True
                    break
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

                now = time.monotonic()
                if now - last_report_time < 1.0:
                    continue
                last_report_time = now
                pct = int(downloaded / total_size * 100) if total_size else 0
                speed_bps = downloaded / max(now - start, 1.0)
                speed_str = (
                    f"{speed_bps / 1024 / 1024:.1f} MB/s"
                    if speed_bps > 1024 * 1024
                    else f"{speed_bps / 1024:.0f} KB/s"
                    if speed_bps
                    else ""
                )
                eta_sec = int((total_size - downloaded) / max(speed_bps, 1)) if total_size and speed_bps else None
                if total_size and pct >= last_pct + 2:
                    _write_install_progress(
                        progress_path,
                        {
                            "status": "downloading",
                            "progress_pct": pct,
                            "message": f"下载模型 {model_name}: {filename} ({pct}%)",
                            "speed": speed_str,
                            "eta_sec": eta_sec,
                        },
                    )
                    last_pct = pct
                elif not total_size:
                    _write_install_progress(
                        progress_path,
                        {
                            "status": "downloading",
                            "progress_pct": 0,
                            "message": f"下载模型 {model_name}: {filename} ({int(now - start)}s, "
                            f"{_format_bytes(downloaded)} 已下载)",
                        },
                    )

        if cancelled:
            tmp_path.unlink(missing_ok=True)
            _write_install_progress(
                progress_path,
                {"status": "idle", "progress_pct": 0, "message": "下载已取消"},
            )
            return

        try:
            tmp_path.replace(target)
        except OSError as e:
            raise RuntimeError(_download_error_detail(e, cfg)) from e

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

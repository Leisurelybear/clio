"""Local web UI: video + player + JSON text visualization editing.

- Zero external dependencies: stdlib http.server
- Defaults to 127.0.0.1 only (not exposed to LAN)
- All file IO sandboxed within config.paths.output_dir to prevent path traversal
- Writes use atomic rename, first overwrite leaves a .bak copy
"""

# ruff: noqa: F401 — all handler imports used by router resolver (lazy lookup)
from __future__ import annotations

import contextlib
import errno
import json
import mimetypes
import secrets
import shutil
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import parse_qs, urlparse

from clio.config import AppConfig
from clio.http_access_log import (
    DEFAULT_ACCESS_LOG_MODE,
    DEFAULT_ACCESS_LOG_SLOW_MS,
    MAX_ACCESS_LOG_BODY_BYTES,
    format_access_line,
    prepare_body,
    sanitize_target,
    should_log,
)
from clio.http_access_log import (
    is_stream as is_stream_path,
)
from clio.i18n import t
from clio.log_history import DEFAULT_READ_BYTES, MAX_READ_BYTES, list_log_files, read_log_file
from clio.session_log import clear as clear_session_log
from clio.session_log import read as read_session_log
from clio.shutdown import before_stop, install_hooks
from clio.task_center.manager import TaskManager
from clio.task_center.store import TaskStore
from clio.tasks.reindex import auto_reindex_if_needed
from clio.ui.handler_security import has_valid_api_token, validate_local_session
from clio.ui.http_server import (
    BUSY_ERROR,
    REQUEST_TIMEOUT_SEC,
    BoundedThreadingHTTPServer,
    RequestBudget,
    header_bytes_too_large,
)
from clio.ui.request_body import (
    MAX_JSON_BODY_BYTES,
    JsonBodyResult,
    read_json_body,
)
from clio.ui.request_body import (
    parse_json_content_length as _parse_json_content_length,
)
from clio.ui.route_table import POST_HANDLER_PARAMS, build_router
from clio.ui.routes.ai import handle_post_ai_test
from clio.ui.routes.config_routes import (
    handle_delete_provider,
    handle_get_config,
    handle_get_config_global,
    handle_get_config_project,
    handle_get_config_raw,
    handle_get_config_schema,
    handle_get_providers,
    handle_post_config_init,
    handle_post_provider,
    handle_put_config_global,
    handle_put_config_project,
    handle_put_config_raw,
    handle_put_provider,
)
from clio.ui.routes.deps import (
    _ensure_ffmpeg_install_handler,
    handle_get_deps_ffmpeg,
    handle_get_deps_ffmpeg_install_status,
    handle_get_deps_keys,
    handle_post_deps_ffmpeg_install,
)
from clio.ui.routes.env_routes import handle_get_env, handle_put_env
from clio.ui.routes.export import handle_post_export
from clio.ui.routes.fs import (
    handle_get_fs_entries,
    handle_get_fs_network,
    handle_get_fs_videos,
    handle_post_fs_mkdir,
    handle_post_fs_reveal,
)
from clio.ui.routes.notifications import (
    handle_get_notifications,
    handle_get_notifications_stream,
    handle_post_notification,
    handle_post_notification_read,
    handle_post_notification_unread,
    handle_post_notifications_read_all,
)
from clio.ui.routes.plan import (
    handle_delete_plan,
    handle_get_cut_orphaned_backups,
    handle_get_plan,
    handle_get_plan_preview_media,
    handle_get_plans,
    handle_post_cut,
    handle_post_cut_restore_backups,
    handle_post_plan_readiness,
    handle_put_plan,
)
from clio.ui.routes.processing_state_routes import handle_get_processing_state
from clio.ui.routes.projects import (
    handle_get_project,
    handle_get_projects,
    handle_post_project_add,
    handle_post_project_create,
    handle_post_project_migrate,
    handle_post_project_remove,
    handle_put_project,
)
from clio.ui.routes.prompts import handle_delete_prompt, handle_get_prompts, handle_put_prompt
from clio.ui.routes.refine import handle_post_refine
from clio.ui.routes.run import (
    handle_post_rerun,
    handle_post_run_preview,
    handle_post_run_start,
)
from clio.ui.routes.static_files import handle_favicon, handle_index, handle_static
from clio.ui.routes.tasks import (
    handle_get_task,
    handle_get_tasks,
    handle_get_tasks_stream,
    handle_post_task_cancel,
    handle_post_task_retry,
)
from clio.ui.routes.texts import (
    handle_get_cover,
    handle_get_texts,
    handle_get_voiceover,
    handle_put_texts,
    handle_put_voiceover,
)
from clio.ui.routes.token_routes import handle_get_token_usage
from clio.ui.routes.transcripts import (
    handle_get_transcripts,
    handle_post_transcripts,
    handle_put_transcripts,
)
from clio.ui.routes.videos import (
    handle_get_video,
    handle_get_video_info,
    handle_get_videos,
    handle_get_videos_selected,
    handle_get_vmeta,
    handle_put_videos_relink,
    handle_put_videos_selected,
)
from clio.ui.routes.waveform import handle_get_waveform
from clio.ui.routes.whisper_routes import (
    handle_get_whisper_check,
    handle_get_whisper_install_status,
    handle_get_whisper_models,
    handle_post_whisper_install,
    handle_post_whisper_install_cancel,
    handle_post_whisper_model_delete,
    handle_put_whisper_model,
)
from clio.ui.services.config_cache import ConfigCache
from clio.ui.services.file_service import (
    resolve_in,
    resolve_texts,
    send_video_range,
)
from clio.ui.services.project_service import (
    ProjectResolutionError,
    _load_project_json,
    _project_output_dir,
    resolve_last_project_config,
    resolve_project_dir,
    resolve_project_input,
)
from clio.utils import write_json_atomic

STATIC_DIR = Path(__file__).parent / "static"

_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Security-Policy", _CONTENT_SECURITY_POLICY),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
)

_desktop_focus_callback: Callable[[], None] | None = None
_STATE_CREATE_LOCK = threading.Lock()
_TASK_MANAGER_CREATE_LOCK = threading.Lock()


def register_builtin_task_handlers(manager: TaskManager) -> None:
    """Make historical tasks retryable before any feature route is visited."""
    from clio.tasks.waveform import _ensure_waveform_handler
    from clio.ui.routes.export import _ensure_export_handler
    from clio.ui.routes.plan import _ensure_cut_handler
    from clio.ui.routes.run import _ensure_run_handlers
    from clio.ui.routes.whisper_download import _ensure_whisper_handler

    _ensure_run_handlers(manager)
    _ensure_cut_handler(manager)
    _ensure_ffmpeg_install_handler(manager)
    _ensure_whisper_handler(manager)
    _ensure_waveform_handler(manager)
    _ensure_export_handler(manager)


def set_desktop_focus_callback(callback: Callable[[], None] | None) -> None:
    """Register the desktop shell's window-focus action (single instance mode).

    The desktop host calls this after creating the pywebview window. The focus
    endpoint stays registered server-side; a None callback means no window is
    attached (web-only mode) and the endpoint answers 503.
    """
    global _desktop_focus_callback
    _desktop_focus_callback = callback


def handle_post_desktop_focus(handler, qs, obj) -> None:
    """Handle POST /api/desktop/focus — bring the running desktop window to front."""
    del qs, obj  # payload intentionally ignored
    cb = _desktop_focus_callback
    if cb is None:
        return handler._send_json({"ok": False, "error": "desktop window unavailable"}, 503)
    try:
        cb()
    except Exception as e:  # noqa: BLE001 — surface any focus failure to the caller
        return handler._send_json({"ok": False, "error": str(e)}, 500)
    return handler._send_json({"ok": True})


def _setting_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _parse_int_param(qs: dict, key: str, default: int, min_val: int = 0, max_val: int = 10_000_000) -> int:
    """Safely parse an integer query parameter with bounds checking."""
    raw = qs.get(key, [None])[0]
    if raw is None:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return max(min_val, min(max_val, val))


def _handle_get_logs(handler, qs):
    offset = _parse_int_param(qs, "offset", 0)
    return handler._send_json(read_session_log(offset))


def _logs_dir_from_config(handler) -> Path:
    return Path(handler._get_config().paths.logs_dir)


def _handle_get_log_files(handler, qs):
    return handler._send_json({"files": list_log_files(_logs_dir_from_config(handler))})


def _handle_get_log_file(handler, qs):
    name = (qs.get("name", [""])[0] or "").strip()
    max_bytes = _parse_int_param(
        qs,
        "max_bytes",
        DEFAULT_READ_BYTES,
        min_val=1,
        max_val=MAX_READ_BYTES,
    )
    try:
        result = read_log_file(_logs_dir_from_config(handler), name, max_bytes=max_bytes)
    except ValueError as exc:
        return handler._send_json({"ok": False, "error": str(exc)}, 400)
    except FileNotFoundError:
        return handler._send_json({"ok": False, "error": "log file not found"}, 404)
    except OSError as exc:
        return handler._send_json(
            {"ok": False, "error": f"failed to read log file: {exc}"},
            500,
        )
    return handler._send_json(result)


def _handle_post_logs_clear(handler, qs, obj):
    clear_session_log()
    cleared_disk = 0
    try:
        from clio.log import clear_disk_logs

        cfg = handler._get_config()
        cleared_disk = clear_disk_logs(Path(cfg.paths.logs_dir))
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        cleared_disk = 0
    return handler._send_json({"ok": True, "cleared_disk_files": cleared_disk})


@dataclass
class _ServerState:
    run_lock: threading.Lock = field(default_factory=threading.Lock)
    run_thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    job_thread: threading.Thread | None = None
    job_cancel_event: threading.Event = field(default_factory=threading.Event)


def make_handler(
    config: AppConfig,
    config_path: Path | None = None,
    api_token: str = "",
    bound_host: str = "127.0.0.1",
    bound_port: int = 0,
    enforce_local_session: bool = False,
    task_manager: TaskManager | None = None,
) -> type[BaseHTTPRequestHandler]:
    output_dir = config.paths.output_dir
    project_dir = config.project_dir or Path.cwd()
    static_dir = STATIC_DIR
    project_path = project_dir / "project.json"

    # Compatibility: migrate old location project.json
    old_project_path = output_dir / "project.json"
    if not project_path.is_file() and old_project_path.is_file():
        with contextlib.suppress(OSError):
            shutil.copy2(old_project_path, project_path)
    # Fix name after migration
    if project_path.is_file():
        cur = _load_project_json(project_dir)
        if cur.get("name") == output_dir.name and project_dir.name != output_dir.name:
            cur["name"] = project_dir.name
            write_json_atomic(project_path, cur)

    DEFAULT_PROJECT = {
        "currentDay": "day1",
        "source": "compressed",
        "name": project_dir.name,
        "output_dir": str(output_dir.resolve()),
        "lastEntity": None,
        "lastVideo": None,
    }

    # isort: split
    from clio.ui.handler_protocol import HandlerProtocol

    class Handler(BaseHTTPRequestHandler, HandlerProtocol):
        timeout = REQUEST_TIMEOUT_SEC
        _project_states: dict[str, _ServerState]
        _config_cache: ClassVar[ConfigCache]
        _task_manager: ClassVar[TaskManager | None]
        _task_store_path: ClassVar[Path]
        _task_retention_days: ClassVar[int]
        _task_max_terminal_tasks: ClassVar[int]
        _task_cleanup_interval_sec: ClassVar[float]
        DEFAULT_PROJECT: dict[str, Any] = {}
        project_dir: Path
        output_dir: Path
        config_path: Path | None
        _api_token: str | None
        _allowed_hosts: tuple[str, ...]
        _enforce_local_session: bool
        _enforce_json_ct: bool
        _access_log_mode: ClassVar[str] = DEFAULT_ACCESS_LOG_MODE
        _access_log_slow_ms: ClassVar[int] = DEFAULT_ACCESS_LOG_SLOW_MS
        _access_log_body_bytes: ClassVar[int] = 0
        raw_requestline: bytes
        _sent_header_names: set[str]
        _http_started: float = 0.0
        _http_req_id: str = ""
        _http_response_bytes: int | None = None
        _http_request_body_log: str | None = None
        _http_response_body_log: str | None = None
        _http_response_started: bool = False
        _http_streaming: bool = False
        _http_stream_status: int | None = None

        # Injected by BoundedThreadingHTTPServer; None disables the split budget.
        request_budget: ClassVar[RequestBudget | None] = None
        # Path of the request holding this connection's budget slot, if any.
        _budget_path: str | None = None

        def handle_one_request(self) -> None:
            self._http_started = time.perf_counter()
            self._http_req_id = secrets.token_hex(3)
            self._http_response_bytes = None
            self._http_request_body_log = None
            self._http_response_body_log = None
            self._http_response_started = False
            self._http_streaming = False
            self._http_stream_status = None
            try:
                super().handle_one_request()
            finally:
                self._release_request_budget()

        def parse_request(self) -> bool:
            ok = super().parse_request()
            if not ok:
                return False
            if header_bytes_too_large(self.raw_requestline, self.headers):
                self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE)
                self.close_connection = True
                return False
            budget = self.__class__.request_budget
            if budget is not None:
                if not budget.acquire(self.path):
                    self._send_json({"ok": False, "error": BUSY_ERROR}, 503)
                    self.close_connection = True
                    return False
                self._budget_path = self.path
            return True

        def _release_request_budget(self) -> None:
            path = self._budget_path
            if path is None:
                return
            self._budget_path = None
            budget = self.__class__.request_budget
            if budget is not None:
                budget.release(path)

        def _get_state(self, project_key: str) -> _ServerState:
            states = self.__class__._project_states
            if project_key in states:
                return states[project_key]
            with _STATE_CREATE_LOCK:
                if project_key not in states:
                    states[project_key] = _ServerState()
                return states[project_key]

        def _get_task_manager(self) -> TaskManager:
            manager = self.__class__._task_manager
            if manager is not None:
                return manager
            with _TASK_MANAGER_CREATE_LOCK:
                manager = self.__class__._task_manager
                if manager is None:
                    manager = TaskManager(
                        TaskStore(self.__class__._task_store_path),
                        retention_days=self.__class__._task_retention_days,
                        max_terminal_tasks=self.__class__._task_max_terminal_tasks,
                        cleanup_interval_sec=self.__class__._task_cleanup_interval_sec,
                    )
                    register_builtin_task_handlers(manager)
                    self.__class__._task_manager = manager
            return manager

        def log_message(self, fmt, *args):
            msg = sanitize_target(fmt % args)
            print(f"  [serve] {self.address_string()} - {msg}")

        def send_response(self, code: int, message: str | None = None) -> None:
            self._http_response_started = True
            self._sent_header_names = set()
            super().send_response(code, message)

        def send_header(self, keyword: str, value: str) -> None:
            self._sent_header_names.add(keyword.lower())
            super().send_header(keyword, value)

        def end_headers(self) -> None:
            for key, value in SECURITY_HEADERS:
                if key.lower() not in self._sent_header_names:
                    self.send_header(key, value)
            super().end_headers()

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            self._http_response_started = True
            super().send_error(code, message, explain)

        def _access_duration(self) -> float:
            started = getattr(self, "_http_started", 0.0)
            return max(0.0, time.perf_counter() - started) if started else 0.0

        def _body_limit(self) -> int:
            return max(0, min(MAX_ACCESS_LOG_BODY_BYTES, int(self.__class__._access_log_body_bytes)))

        def log_request(self, code="-", size="-"):
            del size
            try:
                status = int(code)
            except (TypeError, ValueError):
                status = 0
            path = getattr(self, "path", "") or ""
            method = getattr(self, "command", None) or "-"
            if self._http_streaming:
                self._http_stream_status = status
                return
            duration = self._access_duration()
            mode = self.__class__._access_log_mode
            slow_ms = self.__class__._access_log_slow_ms
            if not should_log(mode, method, path, status, duration, slow_ms):
                return
            line = format_access_line(
                req_id=self._http_req_id,
                method=method,
                path=path,
                status=status,
                duration_s=duration,
                response_bytes=self._http_response_bytes,
                slow_ms=slow_ms,
            )
            print(f"  {line}")
            self._flush_body_logs()

        def _flush_body_logs(self) -> None:
            if self._http_request_body_log:
                print(f"  [http-req] req={self._http_req_id} {self._http_request_body_log}")
            if self._http_response_body_log:
                print(f"  [http-resp] req={self._http_req_id} {self._http_response_body_log}")

        def _log_stream_close(self) -> None:
            status = self._http_stream_status or 200
            if not should_log(
                self.__class__._access_log_mode,
                getattr(self, "command", None) or "-",
                getattr(self, "path", "") or "",
                status,
                self._access_duration(),
                self.__class__._access_log_slow_ms,
            ):
                return
            line = format_access_line(
                req_id=self._http_req_id,
                method=getattr(self, "command", None) or "-",
                path=getattr(self, "path", "") or "",
                status=status,
                duration_s=self._access_duration(),
                response_bytes=None,
                slow_ms=self.__class__._access_log_slow_ms,
            )
            print(f"  {line} STREAM")

        def _require_auth(self) -> bool:
            """Reject requests without the configured bearer or query token."""
            if has_valid_api_token(self.headers, self.path, self.__class__._api_token):
                return True
            self._send_json({"error": t("api.unauthorized")}, 401)
            return False

        def _require_local_session(self) -> bool:
            """Reject non-loopback Host, Origin, and Referer values."""
            if not self.__class__._enforce_local_session:
                return True
            error = validate_local_session(self.headers, self.__class__._allowed_hosts)
            if error is None:
                return True
            self._send_json({"error": t(error)}, 403)
            return False

        def _read_json_body(self) -> tuple[dict | None, int | None]:
            """Parse JSON object body and send payload errors before dispatch."""
            result = read_json_body(
                self.headers,
                self.rfile,
                require_json_content_type=self.__class__._enforce_json_ct,
            )
            self._log_request_body(result)
            if result.error == "invalid_json":
                self._send_json({"ok": False, "error": f"invalid JSON: {result.detail}"}, 400)
                return None, -400
            if result.status is not None:
                return None, result.status
            return result.obj, None

        def _log_request_body(self, result: JsonBodyResult) -> None:
            self._http_request_body_log = prepare_body(
                result.raw,
                path=getattr(self, "path", ""),
                limit=self._body_limit(),
            )

        def _send_json(
            self,
            obj,
            status: int = 200,
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self._http_response_bytes = len(body)
            self._http_response_body_log = prepare_body(
                body,
                path=getattr(self, "path", ""),
                limit=self._body_limit(),
            )
            self._http_response_started = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for key, value in extra_headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(
            self,
            data: bytes,
            content_type: str = "application/octet-stream",
            *,
            extra_headers: dict[str, str] | None = None,
        ) -> None:
            self._http_response_bytes = len(data)
            self._http_response_started = True
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for key, value in extra_headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def _send_static(self, rel: str) -> None:
            target = (static_dir / rel).resolve()
            try:
                target.relative_to(static_dir.resolve())
            except ValueError:
                self.send_error(HTTPStatus.FORBIDDEN)
                return
            if not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            ct = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if target.suffix == ".js":
                ct = "application/javascript; charset=utf-8"
            elif target.suffix == ".css":
                ct = "text/css; charset=utf-8"
            elif target.suffix == ".html":
                ct = "text/html; charset=utf-8"
            elif target.suffix == ".json":
                ct = "application/json; charset=utf-8"
            self._send_bytes(target.read_bytes(), ct)

        def _get_config(self, project_input: Path | None = None) -> AppConfig:
            return self.__class__._config_cache.get(project_input)

        def _invoke_handler(self, handler_fn, *args, **kwargs):
            """Run a route handler; translate ProjectResolutionError into a JSON response."""
            try:
                return handler_fn(self, *args, **kwargs)
            except ProjectResolutionError as e:
                return self._send_json({"ok": False, "error": str(e)}, e.status)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return None
            except Exception:  # noqa: BLE001 - HTTP request boundary must return 500 instead of crashing the worker
                print(
                    f"  [http-error] req={self._http_req_id} "
                    f"{getattr(self, 'command', None) or '-'} "
                    f"{sanitize_target(getattr(self, 'path', '') or '')}",
                    file=sys.stderr,
                )
                traceback.print_exc()
                if not self._http_response_started:
                    return self._send_json({"ok": False, "error": "internal server error"}, 500)
                return None

        def _resolve_project_dir(self, qs: dict) -> Path:
            return resolve_project_dir(qs, project_dir, config_path)

        def _resolve_project_input(self, qs: dict) -> Path:
            """Compat alias for _resolve_project_dir."""
            return self._resolve_project_dir(qs)

        def _get_project_output(self, qs_or_proj_dir: dict | Path) -> Path:
            proj_dir = self._resolve_project_dir(qs_or_proj_dir) if isinstance(qs_or_proj_dir, dict) else qs_or_proj_dir
            return _project_output_dir(proj_dir)

        def _send_video_range(self, path: Path) -> None:
            send_video_range(self, path)

        def _resolve_texts(self, basename: str, proj_out: Path | None = None) -> Path | None:
            return resolve_texts(basename, proj_out, output_dir)

        def _resolve_in(self, subdir: str, basename: str, proj_out: Path | None = None) -> Path | None:
            return resolve_in(subdir, basename, proj_out, output_dir)

        # ---- HTTP method dispatchers ----

        def do_GET(self):
            if not self._require_local_session():
                return
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path

            if path in ("/", "/index.html"):
                return handle_index(self)
            if path == "/favicon.ico":
                return handle_favicon(self)
            if path.startswith("/static/"):
                rel = path[len("/static/") :]
                return handle_static(self, rel)

            handler_fn, path_kwargs, route = router.dispatch("GET", path)
            if handler_fn is None:
                return self.send_error(HTTPStatus.NOT_FOUND)

            if route and route.auth_required and not self._require_auth():
                return

            self._http_streaming = is_stream_path(path)
            try:
                if path_kwargs:
                    return self._invoke_handler(handler_fn, qs, **path_kwargs)
                return self._invoke_handler(handler_fn, qs)
            finally:
                if self._http_streaming:
                    self._log_stream_close()

        def do_HEAD(self):
            """HEAD for Range-capable video (P2-P40); other paths 404."""
            if not self._require_local_session():
                return
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path
            handler_fn, path_kwargs, route = router.dispatch("HEAD", path)
            if handler_fn is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
            if route and route.auth_required and not self._require_auth():
                return
            if path_kwargs:
                return self._invoke_handler(handler_fn, qs, **path_kwargs)
            return self._invoke_handler(handler_fn, qs)

        def do_PUT(self):
            if not self._require_local_session():
                return
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path
            if router.get_policy("PUT", path).auth_required and not self._require_auth():
                return
            obj, err_status = self._read_json_body()
            if err_status == -400:
                return  # invalid JSON already sent
            if err_status == 413:
                return self._send_json({"ok": False, "error": "request body too large"}, 413)
            if err_status == 400:
                return self._send_json({"ok": False, "error": "invalid Content-Length"}, 400)
            if err_status == 415:
                return self._send_json({"ok": False, "error": "unsupported media type, expected application/json"}, 415)
            if err_status is not None:
                return

            handler_fn, path_kwargs, route = router.dispatch("PUT", path)
            if handler_fn is None:
                return self._send_json({"ok": False, "error": "unknown endpoint"}, 404)

            if path_kwargs:
                return self._invoke_handler(handler_fn, qs, obj, **path_kwargs)
            return self._invoke_handler(handler_fn, qs, obj)

        def do_POST(self):
            if not self._require_local_session():
                return
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path
            if router.get_policy("POST", path).auth_required and not self._require_auth():
                return
            obj, err_status = self._read_json_body()
            if err_status == -400:
                return  # invalid JSON already sent
            if err_status == 413:
                return self._send_json({"ok": False, "error": "request body too large"}, 413)
            if err_status == 400:
                return self._send_json({"ok": False, "error": "invalid Content-Length"}, 400)
            if err_status == 415:
                return self._send_json({"ok": False, "error": "unsupported media type, expected application/json"}, 415)
            if err_status is not None:
                return

            handler_fn, path_kwargs, route = router.dispatch("POST", path)
            if handler_fn is None:
                return self._send_json({"ok": False, "error": "unknown endpoint"}, 404)

            assert route is not None
            handler_key = route.handler if isinstance(route.handler, str) else ""
            params = POST_HANDLER_PARAMS.get(handler_key, {"qs", "obj"})
            call_kwargs: dict[str, Any] = {}
            if "qs" in params:
                call_kwargs["qs"] = qs
            if "obj" in params:
                call_kwargs["obj"] = obj
            call_kwargs.update(path_kwargs)
            return self._invoke_handler(handler_fn, **call_kwargs)

        def do_DELETE(self):
            if not self._require_local_session():
                return
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path
            if not self._require_auth():
                return

            handler_fn, path_kwargs, route = router.dispatch("DELETE", path)
            if handler_fn is None:
                return self._send_json({"ok": False, "error": "unknown endpoint"}, 404)

            if path_kwargs:
                return self._invoke_handler(handler_fn, qs, **path_kwargs)
            return self._invoke_handler(handler_fn, qs)

    def _resolve_handler(name: str):
        """Look up handler function from module namespace (supports @patch in tests)."""
        import sys

        return getattr(sys.modules["clio.ui.server"], name, None)

    router = build_router(_resolve_handler)

    # Per-project state dict and config cache (set from closure, not class default)
    Handler._project_states = {}
    Handler._config_cache = ConfigCache(config_path, on_load=auto_reindex_if_needed)
    if task_manager is not None:
        register_builtin_task_handlers(task_manager)
    Handler._task_manager = task_manager
    Handler._task_store_path = (config_path.parent if config_path is not None else output_dir) / "task-center.sqlite3"
    Handler._task_retention_days = max(0, int(config.server.task_retention_days))
    Handler._task_max_terminal_tasks = max(0, int(config.server.task_max_terminal_tasks))
    Handler._task_cleanup_interval_sec = max(0, int(config.server.task_cleanup_interval_min)) * 60.0
    Handler._api_token = api_token
    Handler.DEFAULT_PROJECT = DEFAULT_PROJECT
    Handler.project_dir = project_dir
    Handler.output_dir = output_dir
    Handler.config_path = config_path
    Handler._allowed_hosts = _loopback_hosts(bound_host, bound_port)
    Handler._enforce_local_session = enforce_local_session
    Handler._enforce_json_ct = enforce_local_session
    access_mode = getattr(config.server, "access_log_mode", DEFAULT_ACCESS_LOG_MODE)
    Handler._access_log_mode = access_mode if isinstance(access_mode, str) else DEFAULT_ACCESS_LOG_MODE
    Handler._access_log_slow_ms = _setting_int(
        getattr(config.server, "access_log_slow_ms", DEFAULT_ACCESS_LOG_SLOW_MS),
        DEFAULT_ACCESS_LOG_SLOW_MS,
    )
    Handler._access_log_body_bytes = min(
        MAX_ACCESS_LOG_BODY_BYTES,
        _setting_int(getattr(config.server, "access_log_body_bytes", 0), 0),
    )
    return Handler


def shutdown_task_manager(handler_class: Any, *, timeout: float = 5.0) -> None:
    manager = getattr(handler_class, "_task_manager", None)
    if manager is None:
        return
    manager.shutdown(timeout=timeout)
    handler_class._task_manager = None


def _loopback_hosts(host: str, port: int) -> tuple[str, ...]:
    """Hostname(s) accepted as local by the desktop session gate.

    The desktop webview is served from ``127.0.0.1:<port>``; webview/preview
    tools may also use ``localhost``. Port 0 (auto-bind) is not yet known, so a
    port-agnostic loopback list is returned alongside the configured host.
    """
    base: set[str] = {"127.0.0.1", "localhost", "::1"}
    if host:
        h = host.strip()
        if h.startswith("["):  # IPv6 literal [::1] -> ::1
            h = h[1:].split("]", 1)[0]
        elif h.count(":") == 1:  # host:port
            h = h.split(":", 1)[0]
        if h and h not in base:
            base.add(h)
    return tuple(sorted(base))


def _startup_ms(t0: float) -> str:
    import time

    return f"{(time.perf_counter() - t0) * 1000:.0f}ms"


def run(
    config: AppConfig,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    api_token: str | None = None,
) -> int:
    import secrets
    import time

    t_run = time.perf_counter()
    print("[startup] UI serve: begin")

    t0 = time.perf_counter()
    install_hooks()
    print(f"[startup] hooks installed ({_startup_ms(t0)})")

    TOKEN = api_token
    # A *blank* host previously bound 0.0.0.0 (all NICs) while being treated as
    # local/loopback, i.e. no token — silently exposing the API to the LAN.
    # Blank host is now normalized to 127.0.0.1; explicit 0.0.0.0 keeps LAN
    # exposure but with the auto-generated token above.
    host = host or "127.0.0.1"
    _is_local = host in ("127.0.0.1", "localhost", "::1")
    if TOKEN is None:
        TOKEN = "" if _is_local else secrets.token_urlsafe(32)
    if TOKEN:
        print(f"  API Token: {TOKEN}")
        print(f"  Token URL: http://{host}:{port}/?token={TOKEN}")

    # Try loading last used project config (may re-read project.yaml)
    t0 = time.perf_counter()
    active_config = resolve_last_project_config(config, config_path)
    proj = active_config.project_dir
    print(f"[startup] last project resolved → {proj if proj else '(none)'} ({_startup_ms(t0)})")

    # Scan compressed_dir for missing .vindex; may reindex (can be seconds+)
    t0 = time.perf_counter()
    print("[startup] checking video indexes…")
    did_reindex = auto_reindex_if_needed(active_config)
    print(f"[startup] index check done ({'rebuilt' if did_reindex else 'ok'}, {_startup_ms(t0)})")

    t0 = time.perf_counter()
    handler = make_handler(
        active_config,
        config_path,
        api_token=TOKEN,
        bound_host=host,
        bound_port=port,
        enforce_local_session=_is_local,
    )
    print(f"[startup] HTTP handler ready ({_startup_ms(t0)})")

    t0 = time.perf_counter()
    try:
        server = BoundedThreadingHTTPServer((host, port), handler)
    except OSError as error:
        if error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048:
            print(t("cli.serve.port_in_use", {"port": port}), file=sys.stderr)
            return 1
        raise
    print(f"[startup] bound {host}:{port} ({_startup_ms(t0)})")

    url = f"http://{host}:{port}/"
    print(f"  UI started: {url}")
    print(f"  Project directory: {active_config.project_dir or '(none)'}")
    print(f"  Output directory: {active_config.paths.output_dir}")
    print(f"[startup] ready in {_startup_ms(t_run)} (from run())")
    if not _is_local:
        if not TOKEN:
            print(f"  ⚠  Listening on {host} — exposed to LAN without auth!")
        else:
            print(f"  ⚠  Listening on {host} — exposed to LAN (auth required)")
    print("  Ctrl+C to exit")
    if open_browser:
        threading.Timer(0.5, lambda: _open_browser(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  UI closed")
    finally:
        server.shutdown()
        server.server_close()
        shutdown_task_manager(handler)
        before_stop()
    return 0


def _open_browser(url: str) -> None:
    import webbrowser

    webbrowser.open(url)

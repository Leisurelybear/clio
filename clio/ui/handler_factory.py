"""Dynamic request-handler construction for the local web UI."""

from __future__ import annotations

import contextlib
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
from clio.http_access_log import is_stream as is_stream_path
from clio.i18n import t
from clio.task_center.manager import TaskManager
from clio.task_center.store import TaskStore
from clio.ui.handler_protocol import HandlerProtocol
from clio.ui.handler_security import has_valid_api_token, validate_local_session, validate_request_origin
from clio.ui.http_server import (
    BUSY_ERROR,
    REQUEST_TIMEOUT_SEC,
    RequestBudget,
    header_bytes_too_large,
)
from clio.ui.request_body import JsonBodyResult, read_json_body
from clio.ui.route_table import POST_HANDLER_PARAMS, build_router
from clio.ui.router import Router
from clio.ui.services.config_cache import ConfigCache
from clio.ui.services.project_service import ProjectResolutionError, _load_project_json
from clio.utils import write_json_atomic

_STATE_CREATE_LOCK = threading.Lock()
_TASK_MANAGER_CREATE_LOCK = threading.Lock()


@dataclass
class _ServerState:
    run_lock: threading.Lock = field(default_factory=threading.Lock)
    run_thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    job_lock: threading.Lock = field(default_factory=threading.Lock)
    job_thread: threading.Thread | None = None
    job_cancel_event: threading.Event = field(default_factory=threading.Event)


def _setting_int(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _loopback_hosts(host: str, port: int) -> tuple[str, ...]:
    """Hostnames accepted as local by the desktop session gate."""
    del port
    base: set[str] = {"127.0.0.1", "localhost", "::1"}
    if host:
        normalized = host.strip()
        if normalized.startswith("["):
            normalized = normalized[1:].split("]", 1)[0]
        elif normalized.count(":") == 1:
            normalized = normalized.split(":", 1)[0]
        if normalized and normalized not in base:
            base.add(normalized)
    return tuple(sorted(base))


def build_handler_class(
    config: AppConfig,
    config_path: Path | None = None,
    api_token: str = "",
    bound_host: str = "127.0.0.1",
    bound_port: int = 0,
    enforce_local_session: bool = False,
    enforce_json_content_type: bool | None = None,
    enforce_request_origin: bool | None = None,
    task_manager: TaskManager | None = None,
    *,
    config_cache_factory: Callable[..., ConfigCache],
    auto_reindex: Callable[[AppConfig], bool],
    task_handler_registrar: Callable[[TaskManager], None],
    handler_resolver: Callable[[str], Callable[..., Any] | None],
    static_dir: Path,
    security_headers: tuple[tuple[str, str], ...],
    project_resolver: Callable[[dict, Path, Path | None], Path],
    project_output_resolver: Callable[[Path], Path],
    video_range_sender: Callable[[BaseHTTPRequestHandler, Path], None],
    texts_resolver: Callable[[str, Path | None, Path], Path | None],
    file_resolver: Callable[[str, str, Path | None, Path], Path | None],
) -> type[BaseHTTPRequestHandler]:
    output_dir = config.paths.output_dir
    project_dir = config.project_dir or Path.cwd()
    project_path = project_dir / "project.json"

    old_project_path = output_dir / "project.json"
    if not project_path.is_file() and old_project_path.is_file():
        with contextlib.suppress(OSError):
            shutil.copy2(old_project_path, project_path)
    if project_path.is_file():
        current = _load_project_json(project_dir)
        if current.get("name") == output_dir.name and project_dir.name != output_dir.name:
            current["name"] = project_dir.name
            write_json_atomic(project_path, current)

    default_project = {
        "currentDay": "day1",
        "source": "compressed",
        "name": project_dir.name,
        "output_dir": str(output_dir.resolve()),
        "lastEntity": None,
        "lastVideo": None,
    }

    class Handler(BaseHTTPRequestHandler, HandlerProtocol):
        timeout = REQUEST_TIMEOUT_SEC
        _static_dir: ClassVar[Path]
        _security_headers: ClassVar[tuple[tuple[str, str], ...]]
        _router: ClassVar[Router]
        _handler_resolver: ClassVar[Callable[[str], Callable[..., Any] | None]]
        _task_handler_registrar: ClassVar[Callable[[TaskManager], None]]
        _project_resolver: ClassVar[Callable[..., Path]]
        _project_output_resolver: ClassVar[Callable[..., Path]]
        _video_range_sender: ClassVar[Callable[..., None]]
        _texts_resolver: ClassVar[Callable[..., Path | None]]
        _file_resolver: ClassVar[Callable[..., Path | None]]
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
        _enforce_request_origin: bool
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

        def _call_special_handler(self, name: str, *args):
            handler_fn = self.__class__._handler_resolver(name)
            if handler_fn is None:
                return self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return handler_fn(self, *args)

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
                    self.__class__._task_handler_registrar(manager)
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
            for key, value in self.__class__._security_headers:
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
            """Reject cross-origin requests and, on loopback, foreign Host values."""
            if self.__class__._enforce_local_session:
                error = validate_local_session(self.headers, self.__class__._allowed_hosts)
            elif self.__class__._enforce_request_origin:
                error = validate_request_origin(self.headers)
            else:
                error = None
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
            target = (self.__class__._static_dir / rel).resolve()
            try:
                target.relative_to(self.__class__._static_dir.resolve())
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
            return self.__class__._project_resolver(qs, self.__class__.project_dir, self.__class__.config_path)

        def _resolve_project_input(self, qs: dict) -> Path:
            """Compat alias for _resolve_project_dir."""
            return self._resolve_project_dir(qs)

        def _get_project_output(self, qs_or_proj_dir: dict | Path) -> Path:
            proj_dir = self._resolve_project_dir(qs_or_proj_dir) if isinstance(qs_or_proj_dir, dict) else qs_or_proj_dir
            return self.__class__._project_output_resolver(proj_dir)

        def _send_video_range(self, path: Path) -> None:
            self.__class__._video_range_sender(self, path)

        def _resolve_texts(self, basename: str, proj_out: Path | None = None) -> Path | None:
            return self.__class__._texts_resolver(basename, proj_out, self.__class__.output_dir)

        def _resolve_in(self, subdir: str, basename: str, proj_out: Path | None = None) -> Path | None:
            return self.__class__._file_resolver(subdir, basename, proj_out, self.__class__.output_dir)

        # ---- HTTP method dispatchers ----

        def do_GET(self):
            if not self._require_local_session():
                return
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path

            if path in ("/", "/index.html"):
                return self._call_special_handler("handle_index")
            if path == "/favicon.ico":
                return self._call_special_handler("handle_favicon")
            if path.startswith("/static/"):
                rel = path[len("/static/") :]
                return self._call_special_handler("handle_static", rel)

            handler_fn, path_kwargs, route = self.__class__._router.dispatch("GET", path)
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
            handler_fn, path_kwargs, route = self.__class__._router.dispatch("HEAD", path)
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
            if self.__class__._router.get_policy("PUT", path).auth_required and not self._require_auth():
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

            handler_fn, path_kwargs, route = self.__class__._router.dispatch("PUT", path)
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
            if self.__class__._router.get_policy("POST", path).auth_required and not self._require_auth():
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

            handler_fn, path_kwargs, route = self.__class__._router.dispatch("POST", path)
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

            handler_fn, path_kwargs, route = self.__class__._router.dispatch("DELETE", path)
            if handler_fn is None:
                return self._send_json({"ok": False, "error": "unknown endpoint"}, 404)

            if path_kwargs:
                return self._invoke_handler(handler_fn, qs, **path_kwargs)
            return self._invoke_handler(handler_fn, qs)

    handler_cls = Handler
    handler_cls._project_states = {}
    handler_cls._config_cache = config_cache_factory(config_path, on_load=auto_reindex)
    if task_manager is not None:
        task_handler_registrar(task_manager)
    handler_cls._task_manager = task_manager
    handler_cls._task_store_path = (
        config_path.parent if config_path is not None else output_dir
    ) / "task-center.sqlite3"
    handler_cls._task_retention_days = max(0, int(config.server.task_retention_days))
    handler_cls._task_max_terminal_tasks = max(0, int(config.server.task_max_terminal_tasks))
    handler_cls._task_cleanup_interval_sec = max(0, int(config.server.task_cleanup_interval_min)) * 60.0
    handler_cls._api_token = api_token
    handler_cls.DEFAULT_PROJECT = default_project
    handler_cls.project_dir = project_dir
    handler_cls.output_dir = output_dir
    handler_cls.config_path = config_path
    handler_cls._allowed_hosts = _loopback_hosts(bound_host, bound_port)
    handler_cls._enforce_local_session = enforce_local_session
    handler_cls._enforce_request_origin = (
        enforce_local_session if enforce_request_origin is None else enforce_request_origin
    )
    handler_cls._enforce_json_ct = (
        enforce_local_session if enforce_json_content_type is None else enforce_json_content_type
    )
    access_mode = getattr(config.server, "access_log_mode", DEFAULT_ACCESS_LOG_MODE)
    handler_cls._access_log_mode = access_mode if isinstance(access_mode, str) else DEFAULT_ACCESS_LOG_MODE
    handler_cls._access_log_slow_ms = _setting_int(
        getattr(config.server, "access_log_slow_ms", DEFAULT_ACCESS_LOG_SLOW_MS),
        DEFAULT_ACCESS_LOG_SLOW_MS,
    )
    handler_cls._access_log_body_bytes = min(
        MAX_ACCESS_LOG_BODY_BYTES,
        _setting_int(getattr(config.server, "access_log_body_bytes", 0), 0),
    )
    handler_cls._static_dir = static_dir
    handler_cls._security_headers = security_headers
    handler_cls._handler_resolver = handler_resolver
    handler_cls._task_handler_registrar = task_handler_registrar
    handler_cls._project_resolver = project_resolver
    handler_cls._project_output_resolver = project_output_resolver
    handler_cls._video_range_sender = video_range_sender
    handler_cls._texts_resolver = texts_resolver
    handler_cls._file_resolver = file_resolver
    handler_cls._router = build_router(handler_resolver)
    return handler_cls

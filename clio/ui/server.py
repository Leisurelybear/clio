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
from clio.ui.handler_factory import _ServerState, build_handler_class
from clio.ui.handler_security import has_valid_api_token, validate_local_session
from clio.ui.http_server import BoundedThreadingHTTPServer
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


def make_handler(
    config: AppConfig,
    config_path: Path | None = None,
    api_token: str = "",
    bound_host: str = "127.0.0.1",
    bound_port: int = 0,
    enforce_local_session: bool = False,
    task_manager: TaskManager | None = None,
) -> type[BaseHTTPRequestHandler]:
    def _resolve_handler(name: str):
        """Resolve lazily so tests can patch handlers on ``clio.ui.server``."""
        return getattr(sys.modules["clio.ui.server"], name, None)

    return build_handler_class(
        config,
        config_path,
        api_token=api_token,
        bound_host=bound_host,
        bound_port=bound_port,
        enforce_local_session=enforce_local_session,
        task_manager=task_manager,
        config_cache_factory=ConfigCache,
        auto_reindex=auto_reindex_if_needed,
        task_handler_registrar=register_builtin_task_handlers,
        handler_resolver=_resolve_handler,
        static_dir=STATIC_DIR,
        security_headers=SECURITY_HEADERS,
        project_resolver=lambda qs, project_dir, config_path: resolve_project_dir(qs, project_dir, config_path),
        project_output_resolver=lambda project_dir: _project_output_dir(project_dir),
        video_range_sender=lambda handler, path: send_video_range(handler, path),
        texts_resolver=lambda basename, proj_out, output_dir: resolve_texts(basename, proj_out, output_dir),
        file_resolver=lambda subdir, basename, proj_out, output_dir: resolve_in(subdir, basename, proj_out, output_dir),
    )


def shutdown_task_manager(handler_class: Any, *, timeout: float = 5.0) -> None:
    manager = getattr(handler_class, "_task_manager", None)
    if manager is None:
        return
    manager.shutdown(timeout=timeout)
    handler_class._task_manager = None


def _startup_ms(t0: float) -> str:

    return f"{(time.perf_counter() - t0) * 1000:.0f}ms"


def run(
    config: AppConfig,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    api_token: str | None = None,
) -> int:
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

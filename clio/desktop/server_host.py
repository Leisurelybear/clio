# clio/desktop/server_host.py
"""Start/stop a localhost UI HTTP server for the desktop shell (non-blocking)."""

from __future__ import annotations

import json
import secrets
import threading
import urllib.request
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path

from clio.config import AppConfig
from clio.shutdown import before_stop, install_hooks
from clio.tasks.reindex import auto_reindex_if_needed
from clio.ui.http_server import BoundedThreadingHTTPServer
from clio.ui.server import make_handler, shutdown_task_manager
from clio.ui.services.project_service import resolve_last_project_config


@dataclass
class ServerHandle:
    host: str
    port: int
    server: ThreadingHTTPServer
    thread: threading.Thread
    token: str


def start_server(
    config: AppConfig,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    api_token: str | None = None,
) -> ServerHandle:
    """Bind a free (or given) port and serve the UI on a background thread.

    Mirrors ``clio.ui.server.run`` startup (token, project resolve, reindex,
    handler) but never opens a browser and never blocks the caller.

    The desktop always runs a fresh random per-launch token, even on loopback,
    so the UI has a real session boundary (a browser tab on the same machine
    cannot drive the desktop API via CSRF).
    """
    install_hooks()

    host = host or "127.0.0.1"
    token = api_token if api_token is not None else secrets.token_urlsafe(32)

    active_config = resolve_last_project_config(config, config_path)
    auto_reindex_if_needed(active_config)

    handler = make_handler(
        active_config,
        config_path,
        api_token=token,
        bound_host=host,
        bound_port=port,
        enforce_local_session=True,
    )
    server = BoundedThreadingHTTPServer((host, port), handler)
    bound_host, bound_port = server.server_address[:2]

    thread = threading.Thread(
        target=server.serve_forever,
        name="clio-http",
        daemon=True,
    )
    thread.start()

    return ServerHandle(
        host=str(bound_host),
        port=int(bound_port),
        server=server,
        thread=thread,
        token=token,
    )


def stop_server(handle: ServerHandle, timeout: float = 5.0) -> None:
    """Shut down the HTTP server and join its thread."""
    try:
        handle.server.shutdown()
    finally:
        handle.server.server_close()
        handle.thread.join(timeout=timeout)
        shutdown_task_manager(handle.server.RequestHandlerClass, timeout=timeout)
        before_stop()


def fetch_active_tasks(host: str, port: int, token: str = "") -> list[dict]:
    """Probe GET /api/tasks for queued/running/cancelling tasks.

    Returns an empty list when the server is unreachable / malformed.
    """
    try:
        url = f"http://{host}:{port}/api/tasks?visibility=all&limit=50"
        if token:
            url = f"{url}&token={token}"
        with urllib.request.urlopen(url, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return [task for task in payload.get("tasks", []) if task.get("status") in {"queued", "running", "cancelling"}]
    except (OSError, ValueError, json.JSONDecodeError):
        return []


def request_task_cancel(host: str, port: int, token: str = "", task_id: str = "") -> None:
    """POST /api/tasks/{id}/cancel (best-effort, authed on desktop)."""
    try:
        url = f"http://{host}:{port}/api/tasks/{task_id}/cancel"
        if token:
            url = f"{url}?token={token}"
        req = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except (OSError, ValueError):
        pass


def request_run_cancel(host: str, port: int, token: str = "") -> None:
    """Cancel every currently active task (best-effort desktop close path)."""
    for task in fetch_active_tasks(host, port, token):
        request_task_cancel(host, port, token, str(task.get("id", "")))

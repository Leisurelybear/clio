# clio/desktop/single_instance.py
"""Single-instance coordination for the desktop shell.

A JSON lock file (``clio.lock``) in the config dir records the first
instance's server port. A second instance decides liveness by POSTing the
focus endpoint: any HTTP response proves the first instance is alive, while a
connection error means the lock is stale and can be taken over. PID checks are
deliberately avoided because os.kill(pid, 0) is unreliable on Windows.
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path

LOCK_FILENAME = "clio.lock"
WEB_PORT = 8765
_WEB_MARKER = b"Vlog"


def lock_path(config_dir: Path) -> Path:
    return Path(config_dir) / LOCK_FILENAME


def read_lock(config_dir: Path) -> dict | None:
    p = lock_path(config_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    if not isinstance(port, int) or isinstance(port, bool):
        return None
    return data


def write_lock(config_dir: Path, port: int, pid: int, token: str | None = None) -> str:
    """Atomically write the single-instance lock owned by this launch.

    ``token`` is a per-launch random secret. When omitted one is generated and
    returned so the caller can later remove *only its own* lock (see
    ``remove_lock``). Direct ``os.replace`` makes the write atomic: a second
    instance either reads the old payload or the new one, never a truncation
    mid-write.
    """
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    token = token or secrets.token_hex(16)
    payload = (
        json.dumps(
            {"port": int(port), "pid": int(pid), "token": token},
            ensure_ascii=False,
        )
        + "\n"
    )

    tmp = lock_path(config_dir).with_name(f".{LOCK_FILENAME}.{os.getpid()}.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, lock_path(config_dir))
    return token


def owns_lock(config_dir: Path, token: str) -> bool:
    """True when the on-disk lock was created by the launch that owns ``token``."""
    data = read_lock(config_dir)
    return bool(data) and data.get("token") == token


def remove_lock(config_dir: Path, token: str | None = None) -> bool:
    """Remove the lock file, but only when the caller owns it.

    ``token`` must be the instance id returned by ``write_lock``. Without a
    matching token the current lock (possibly belonging to a *newer* launch
    after a stale takeover) is left untouched. Returns True when the file was
    actually removed.
    """
    p = lock_path(config_dir)
    if not p.exists():
        return False
    if token is not None and not owns_lock(config_dir, token):
        return False
    try:
        p.unlink()
        return True
    except OSError:
        return False


def focus_first_instance(host: str, port: int, timeout: float = 3.0) -> bool:
    """Ask the first instance to focus its window.

    Returns True when any HTTP response is received (the first instance is
    alive — including 5xx while its window is still starting up). Returns
    False only when the server is unreachable (stale lock / connection error).
    """
    try:
        req = urllib.request.Request(
            f"http://{host}:{port}/api/desktop/focus",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def is_web_running(host: str = "127.0.0.1", port: int = WEB_PORT, timeout: float = 2.0) -> bool:
    """Return True when the web UI (``serve``) is already up on the default port."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=timeout) as resp:
            if resp.status != 200:
                return False
            return _WEB_MARKER in resp.read(1024)
    except OSError:
        return False

"""Identify what is listening on a local HTTP port.

Kept free of UI/desktop imports so the ``serve`` CLI can probe before the
heavy UI modules load.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Literal

WEB_MARKER = b"Vlog"
WebProbe = Literal["clio", "other", "unreachable"]


def probe_web_ui(host: str = "127.0.0.1", port: int = 8765, timeout: float = 2.0) -> WebProbe:
    """Return ``clio`` for our UI, ``other`` for a reachable foreign server."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=timeout) as resp:
            if resp.status != 200:
                return "other"
            body = resp.read(1024)
    except urllib.error.HTTPError:
        return "other"
    except OSError:
        return "unreachable"
    return "clio" if WEB_MARKER in body else "other"

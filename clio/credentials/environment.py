"""Track dotenv-owned process entries without confusing them with inherited values."""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping

_lock = threading.RLock()
_injected: dict[str, str] = {}


def inherited_value(name: str, environ: Mapping[str, str] | None = None) -> str:
    if environ is not None:
        return environ.get(name, "")
    with _lock:
        current = os.environ.get(name, "")
        return "" if name in _injected and current == _injected[name] else current


def sync_dotenv(values: dict[str, str], *, override: bool = False, previous: dict[str, str] | None = None) -> None:
    with _lock:
        for name in previous or {}:
            if name not in values and name in _injected and os.environ.get(name) == _injected[name]:
                os.environ.pop(name, None)
                _injected.pop(name, None)
        for name, value in values.items():
            # A shell value always wins, including after a settings save.
            owned = name in _injected and os.environ.get(name) == _injected[name]
            if name in _injected and not owned:
                _injected.pop(name, None)
            if name not in os.environ or owned and override:
                os.environ[name] = value
                _injected[name] = value

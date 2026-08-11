from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_MAX = 10000
_logs: list[dict[str, Any]] = []
_sequence = 0


def write(line: str) -> None:
    global _sequence
    entry = {"ts": time.time(), "text": line, "seq": _sequence}
    _sequence += 1
    with _lock:
        _logs.append(entry)
        if len(_logs) > _MAX:
            del _logs[: len(_logs) - _MAX]


def read(offset: int = 0) -> dict[str, Any]:
    with _lock:
        entries = list(_logs)
    if offset > 0:
        result = [e for e in entries if e.get("seq", 0) >= offset]
    else:
        result = entries
    return {"logs": result, "total": _sequence}


def clear() -> None:
    global _sequence
    with _lock:
        _logs.clear()
        _sequence = 0

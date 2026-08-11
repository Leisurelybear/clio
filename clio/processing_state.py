from __future__ import annotations

import atexit
import json
import os
import threading
import time
import weakref
from pathlib import Path

from clio.schema import ARTIFACT_SCHEMA_VERSION

_STEPS = ["compress", "analyze", "voiceover", "transcribe", "plan", "label"]
_DEFAULT_FLUSH_INTERVAL_SEC = 0.5
_DEFAULT_MAX_PENDING_FLUSHES = 20
_STATE_INSTANCES: weakref.WeakSet = weakref.WeakSet()
_STATE_REGISTRY_LOCK = threading.Lock()
_ATEXIT_REGISTERED = False


def _flush_all_states() -> None:
    for state in list(_STATE_INSTANCES):
        state.flush()


def _register_state(state: ProcessingState) -> None:
    global _ATEXIT_REGISTERED
    with _STATE_REGISTRY_LOCK:
        _STATE_INSTANCES.add(state)
        if not _ATEXIT_REGISTERED:
            atexit.register(_flush_all_states)
            _ATEXIT_REGISTERED = True


class ProcessingState:
    """Per-file pipeline state matrix, persisted to output/.processing.json.

    Schema:
      version   — 1
      steps     — ordered step list
      files     — {original_stem: {step: status|null}}
    """

    def __init__(
        self,
        output_dir: Path,
        *,
        flush_interval_sec: float = _DEFAULT_FLUSH_INTERVAL_SEC,
        max_pending_flushes: int = _DEFAULT_MAX_PENDING_FLUSHES,
    ):
        self._path = output_dir / ".processing.json"
        self._lock = threading.Lock()
        self._data = self._load()
        self._flush_interval_sec = flush_interval_sec
        self._max_pending_flushes = max_pending_flushes
        self._dirty = False
        self._pending_flushes = 0
        self._last_flush_at = time.monotonic()
        self._flush_timer: threading.Timer | None = None
        _register_state(self)

    def _load(self) -> dict:
        default = {
            "_schema_version": ARTIFACT_SCHEMA_VERSION,
            "version": 1,
            "steps": list(_STEPS),
            "files": {},
        }
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return default
            if not isinstance(data, dict):
                return default
            if not isinstance(data.get("files"), dict):
                return default
            data.setdefault("_schema_version", ARTIFACT_SCHEMA_VERSION)
            return data
        return default

    def _flush_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        suffix = os.urandom(4).hex()
        tmp = self._path.parent / f"{self._path.name}.{suffix}.tmp"
        tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)
        self._dirty = False
        self._pending_flushes = 0
        self._last_flush_at = time.monotonic()

    def _should_flush_now_locked(self) -> bool:
        if not self._path.exists():
            return True
        if self._flush_interval_sec <= 0:
            return True
        if self._max_pending_flushes <= 0:
            return True
        if self._pending_flushes >= self._max_pending_flushes:
            return True
        return time.monotonic() - self._last_flush_at >= self._flush_interval_sec

    def _schedule_flush_locked(self) -> None:
        if self._flush_interval_sec <= 0 or self._flush_timer is not None:
            return
        t = threading.Thread(target=self._flush_sleeper, daemon=True)
        self._flush_timer = t
        t.start()

    def _flush_sleeper(self) -> None:
        time.sleep(self._flush_interval_sec)
        with self._lock:
            if self._flush_timer is None:
                return
            self._flush_timer = None
            if self._dirty:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_timer = None
            if self._dirty:
                self._flush_locked()

    def mark(self, file_stem: str, step: str, status: str) -> None:
        with self._lock:
            files = self._data.setdefault("files", {})
            if file_stem not in files:
                files[file_stem] = {s: None for s in _STEPS}
            files[file_stem][step] = status
            self._dirty = True
            self._pending_flushes += 1
            if self._should_flush_now_locked():
                self._flush_timer = None
                self._flush_locked()
            else:
                self._schedule_flush_locked()

    def reset_step(self, step: str) -> None:
        with self._lock:
            for entry in self._data.setdefault("files", {}).values():
                if step in entry:
                    entry[step] = None
            self._dirty = True
            self._flush_locked()

    def get_state(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

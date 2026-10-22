from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import TypedDict

from clio import session_log as _session_log
from clio.i18n import t
from clio.schema import ARTIFACT_SCHEMA_VERSION


class ProgressData(TypedDict):
    """Structure of the .progress.json file."""

    _schema_version: int
    phase: str
    current: int
    total: int
    message: str
    status: str
    started_at: str
    eta_sec: float | None
    rerun: bool
    rerun_video: str | None
    logs: list[str]


class ProgressTracker:
    """Thread-safe progress tracker that writes to output/.progress.json.

    Fields written:
      phase        — "compress" | "analyze" | "voiceover" | "plan" | "label" | "done" | "error"
      current      — items completed within current phase
      total        — total items in current phase
      message      — human-readable status line
      status       — "running" | "done" | "error"
      started_at   — ISO timestamp
      eta_sec      — estimated remaining seconds
    """

    def __init__(self, output_dir: Path, *, rerun: bool = False, rerun_video: str | None = None):
        self._path = output_dir / ".progress.json"
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._write_failed = False
        self._data: ProgressData = {
            "_schema_version": ARTIFACT_SCHEMA_VERSION,
            "phase": "",
            "current": 0,
            "total": 0,
            "message": "",
            "status": "running",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "eta_sec": None,
            "rerun": rerun,
            "rerun_video": rerun_video,
            "logs": [],
        }
        self._flush()

    def _flush(self) -> None:
        tmp: Path | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            suffix = os.urandom(4).hex()
            tmp = self._path.parent / f"{self._path.name}.{suffix}.tmp"
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as e:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            if not self._write_failed:
                print(t("progress.write_failed", {"path": self._path, "detail": e}))
                self._write_failed = True

    def update(
        self,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
        status: str | None = None,
    ) -> None:
        with self._lock:
            if phase is not None:
                if phase != self._data["phase"]:
                    self._start = time.monotonic()
                    self._data["eta_sec"] = None
                    self._data["current"] = 0
                self._data["phase"] = phase
            if current is not None:
                self._data["current"] = current
            if total is not None:
                self._data["total"] = total
            if message is not None:
                self._data["message"] = message
            if status is not None:
                self._data["status"] = status
            if self._data["total"] > 0 and self._data["current"] > 0:
                elapsed = time.monotonic() - self._start
                rate = self._data["current"] / elapsed if elapsed > 0 else 0
                remaining = self._data["total"] - self._data["current"]
                self._data["eta_sec"] = round(remaining / rate) if rate > 0 else None
            self._flush()

    def next(self, *, message: str | None = None) -> None:
        """Advance current by 1."""
        with self._lock:
            self._data["current"] += 1
            if message is not None:
                self._data["message"] = message
            if self._data["total"] > 0 and self._data["current"] > 0:
                elapsed = time.monotonic() - self._start
                rate = self._data["current"] / elapsed if elapsed > 0 else 0
                remaining = self._data["total"] - self._data["current"]
                self._data["eta_sec"] = round(remaining / rate) if rate > 0 else None
            self._flush()

    def log(self, line: str) -> None:
        """Append a log line (shown in UI log view)."""
        with self._lock:
            self._data["logs"].append(line)
            if len(self._data["logs"]) > 100:
                self._data["logs"] = self._data["logs"][-100:]
            self._flush()
        _session_log.write(line)

    def done(self, message: str = "") -> None:
        self.update(phase="done", current=0, total=0, message=message or t("progress.done"), status="done")
        self.log("✓ " + (message or t("progress.done")))

    def error(self, message: str) -> None:
        self.update(status="error", message=message)
        self.log("✗ " + message)

    def cancelled(self, message: str = "") -> None:
        self.update(
            phase="cancelled", current=0, total=0, message=message or t("progress.cancelled"), status="cancelled"
        )
        self.log("⏹ " + (message or t("progress.cancelled")))

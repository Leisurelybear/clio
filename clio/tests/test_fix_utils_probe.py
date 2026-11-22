"""Regression tests for run_probe cancellation and JSON output handling (A-07)."""

from __future__ import annotations

import json
import subprocess
import threading

import pytest

from clio import utils


class _FakeProc:
    def __init__(self, *, stdout: str = "", hang: bool = False, on_wait=None):
        self.returncode = 0
        self._stdout = stdout
        self._hang = hang
        self._on_wait = on_wait
        self.terminated = False
        self.waited = False

    def communicate(self, timeout=None):
        if self._hang:
            if self._on_wait is not None:
                self._on_wait()
            raise subprocess.TimeoutExpired("ffprobe", timeout)
        return self._stdout, ""

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return 0

    def poll(self):
        return 0


def test_run_probe_honours_cancel_while_waiting(monkeypatch):
    cancel = threading.Event()
    proc = _FakeProc(hang=True, on_wait=cancel.set)
    monkeypatch.setattr(utils, "popen_subprocess", lambda *_a, **_k: proc)

    with pytest.raises(InterruptedError):
        utils.run_probe(["ffprobe", "x"], timeout=30, cancel_event=cancel)

    assert proc.terminated
    assert proc.waited


def test_run_probe_does_not_truncate_json_output(monkeypatch):
    payload = json.dumps({"streams": [{"tags": {"k": "v" * 200}} for _ in range(50)]})
    monkeypatch.setattr(utils, "popen_subprocess", lambda *_a, **_k: _FakeProc(stdout=payload))

    result = utils.run_probe(["ffprobe", "-of", "json", "x"], max_output_bytes=64)

    assert json.loads(result.stdout) == json.loads(payload)


def test_run_probe_still_truncates_plain_text(monkeypatch):
    monkeypatch.setattr(utils, "popen_subprocess", lambda *_a, **_k: _FakeProc(stdout="x" * 100))

    result = utils.run_probe(["ffprobe", "x"], max_output_bytes=10)

    assert result.stdout == "x" * 10

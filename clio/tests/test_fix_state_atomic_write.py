"""Regression tests: progress/processing-state files use the shared atomic writer (A-23)."""

from __future__ import annotations

import json

import pytest

from clio import utils
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker


def _fail_replace(*_args, **_kwargs):
    raise PermissionError("locked")


def test_progress_write_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    monkeypatch.setattr(utils.os, "replace", _fail_replace)

    ProgressTracker(tmp_path)  # first flush fails, must be swallowed

    assert not list(tmp_path.glob("*.tmp"))


def test_processing_state_write_failure_leaves_no_temp_file(tmp_path, monkeypatch):
    state = ProcessingState(tmp_path, flush_interval_sec=0)
    monkeypatch.setattr(utils.os, "replace", _fail_replace)

    with pytest.raises(PermissionError):
        state.mark("clip", "compress", "done")  # first write flushes immediately

    assert not list(tmp_path.glob("*.tmp"))


def test_progress_file_is_still_compact_json(tmp_path):
    ProgressTracker(tmp_path)

    raw = (tmp_path / ".progress.json").read_text(encoding="utf-8")
    assert json.loads(raw)["status"] == "running"

"""Regression tests: corrupt or partial token usage files never lose history silently."""

from __future__ import annotations

import json

from clio.ai.base import TokenUsage
from clio.ai.token_usage import _EMPTY_STATS, FileTokenUsageStore

USAGE = TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3)


def test_corrupt_file_is_moved_aside_before_record(tmp_path):
    store = FileTokenUsageStore(str(tmp_path))
    store._path.write_text("{not json", encoding="utf-8")

    store.record("analyze", "m", USAGE)

    corrupt = [p for p in tmp_path.iterdir() if p.name.startswith(".token_usage.json.corrupt.")]
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == "{not json"
    assert store.get_stats()["total"]["total_tokens"] == 3


def test_wrong_shape_is_moved_aside(tmp_path):
    store = FileTokenUsageStore(str(tmp_path))
    store._path.write_text(json.dumps({"total": {}}), encoding="utf-8")

    assert store.get_stats() == _EMPTY_STATS
    assert any(p.name.startswith(".token_usage.json.corrupt.") for p in tmp_path.iterdir())


def test_record_backfills_missing_sections(tmp_path):
    store = FileTokenUsageStore(str(tmp_path))
    partial = {
        "total": {"total_tokens": 7},
        "by_model": {"m": {"calls": 1}},
        "history": [{"task": "old"}],
    }
    store._path.write_text(json.dumps(partial), encoding="utf-8")

    store.record("analyze", "m", USAGE)

    stats = store.get_stats()
    assert stats["total"]["total_tokens"] == 10
    assert stats["total"]["prompt_tokens"] == 1
    assert stats["by_model"]["m"]["calls"] == 2
    assert stats["by_task"]["analyze"]["calls"] == 1
    assert len(stats["history"]) == 2

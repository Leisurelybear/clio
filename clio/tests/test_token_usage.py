from __future__ import annotations

import copy
from unittest.mock import patch

from clio.ai.base import TokenUsage
from clio.ai.token_usage import (
    _EMPTY_STATS,
    FileTokenUsageStore,
    _merge_stats,
    estimate_cost_usd,
)
from clio.utils import write_json_atomic


def test_merge_stats_accumulates_by_model():
    stats = copy.deepcopy(_EMPTY_STATS)
    _merge_stats(stats, "video_analyze", "gemini", 10, 20, 30)
    assert stats["by_model"]["gemini"] == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "calls": 1,
        "cost_usd": 0.0,
    }


def test_merge_stats_accumulates_by_task():
    stats = copy.deepcopy(_EMPTY_STATS)
    _merge_stats(stats, "voiceover", "deepseek", 5, 15, 20)
    assert stats["by_task"]["voiceover"] == {
        "prompt_tokens": 5,
        "completion_tokens": 15,
        "total_tokens": 20,
        "calls": 1,
        "cost_usd": 0.0,
    }


def test_merge_stats_multiple_calls_same_model():
    stats = copy.deepcopy(_EMPTY_STATS)
    _merge_stats(stats, "video_analyze", "gemini", 10, 20, 30)
    _merge_stats(stats, "voiceover", "gemini", 5, 5, 10)
    assert stats["by_model"]["gemini"] == {
        "prompt_tokens": 15,
        "completion_tokens": 25,
        "total_tokens": 40,
        "calls": 2,
        "cost_usd": 0.0,
    }


def test_merge_stats_updates_total():
    stats = copy.deepcopy(_EMPTY_STATS)
    _merge_stats(stats, "task1", "model1", 100, 200, 300)
    _merge_stats(stats, "task2", "model2", 10, 20, 30)
    assert stats["total"] == {
        "prompt_tokens": 110,
        "completion_tokens": 220,
        "total_tokens": 330,
        "cost_usd": 0.0,
    }


def test_merge_stats_separate_models():
    stats = copy.deepcopy(_EMPTY_STATS)
    _merge_stats(stats, "video_analyze", "gemini", 10, 20, 30)
    _merge_stats(stats, "voiceover", "deepseek", 5, 15, 20)
    assert stats["by_model"]["gemini"]["calls"] == 1
    assert stats["by_model"]["deepseek"]["calls"] == 1
    assert stats["by_model"]["gemini"]["total_tokens"] == 30
    assert stats["by_model"]["deepseek"]["total_tokens"] == 20


class TestFileTokenUsageStore:
    def test_init_sets_path(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        assert str(store._path) == str(tmp_path / ".token_usage.json")

    def test_record_creates_file(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        with patch("clio.ai.token_usage.write_json_atomic") as mock_write:
            store.record("video_analyze", "gemini-2.5-flash", usage)

            mock_write.assert_called_once()
            path_arg = mock_write.call_args[0][0]
            data_arg = mock_write.call_args[0][1]

            assert str(path_arg) == str(tmp_path / ".token_usage.json")
            assert len(data_arg["history"]) == 1
            entry = data_arg["history"][0]
            assert entry["task"] == "video_analyze"
            assert entry["model"] == "gemini-2.5-flash"
            assert entry["prompt_tokens"] == 10
            assert entry["completion_tokens"] == 20
            assert entry["total_tokens"] == 30
            assert entry["provider"] == ""
            assert entry["prompt_version"] == ""
            assert entry["cost_usd"] == 0.0

    def test_record_updates_stats(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        with patch("clio.ai.token_usage.write_json_atomic") as mock_write:
            store.record("video_analyze", "gemini-2.5-flash", usage)

            data = mock_write.call_args[0][1]
            assert data["total"]["total_tokens"] == 30
            assert data["by_model"]["gemini-2.5-flash"]["calls"] == 1
            assert data["by_task"]["video_analyze"]["calls"] == 1

    def test_record_appends_to_existing(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        initial = copy.deepcopy(_EMPTY_STATS)
        initial["history"].append(
            {
                "timestamp": "2024-01-01T00:00:00",
                "task": "video_analyze",
                "model": "gemini",
                "prompt_tokens": 5,
                "completion_tokens": 5,
                "total_tokens": 10,
            }
        )
        _merge_stats(initial, "video_analyze", "gemini", 5, 5, 10)
        write_json_atomic(store._path, initial)

        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        with patch("clio.ai.token_usage.write_json_atomic") as mock_write:
            store.record("voiceover", "deepseek-chat", usage)

            data = mock_write.call_args[0][1]
            assert len(data["history"]) == 2
            assert data["total"]["total_tokens"] == 40
            assert data["by_model"]["deepseek-chat"]["calls"] == 1

    def test_get_stats_returns_current(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        initial = copy.deepcopy(_EMPTY_STATS)
        initial["history"].append(
            {
                "timestamp": "2024-01-01T00:00:00",
                "task": "video_analyze",
                "model": "gemini",
                "prompt_tokens": 5,
                "completion_tokens": 5,
                "total_tokens": 10,
            }
        )
        _merge_stats(initial, "video_analyze", "gemini", 5, 5, 10)
        write_json_atomic(store._path, initial)

        stats = store.get_stats()
        assert stats["total"]["total_tokens"] == 10
        assert len(stats["history"]) == 1
        assert stats["history"][0]["task"] == "video_analyze"

    def test_get_stats_after_multiple_records(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        with patch("clio.ai.token_usage.write_json_atomic"):
            store.record("video_analyze", "gemini-2.5-flash", usage)

        stats = store.get_stats()
        assert stats == _EMPTY_STATS

    def test_read_raw_returns_empty_for_missing_file(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        result = store._read_raw()
        assert result == _EMPTY_STATS

    def test_read_raw_returns_empty_for_corrupted_json(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        store._path.write_text("not valid json", encoding="utf-8")
        result = store._read_raw()
        assert result == _EMPTY_STATS

    def test_read_raw_returns_content_for_valid_file(self, tmp_path):
        store = FileTokenUsageStore(str(tmp_path))
        data = copy.deepcopy(_EMPTY_STATS)
        data["total"]["total_tokens"] = 42
        write_json_atomic(store._path, data)

        result = store._read_raw()
        assert result["total"]["total_tokens"] == 42


def test_estimate_cost_usd_uses_split_prices():
    assert estimate_cost_usd(1_000_000, 500_000, 2.0, 4.0) == 4.0
    assert estimate_cost_usd(10, 20, 0.0, 0.0) == 0.0


def test_merge_stats_accumulates_cost():
    stats = copy.deepcopy(_EMPTY_STATS)
    _merge_stats(stats, "voiceover", "deepseek-chat", 10, 20, 30, cost_usd=0.0012)
    _merge_stats(stats, "voiceover", "deepseek-chat", 5, 5, 10, cost_usd=0.0004)
    assert stats["total"]["cost_usd"] == 0.0016
    assert stats["by_model"]["deepseek-chat"]["cost_usd"] == 0.0016
    assert stats["by_task"]["voiceover"]["cost_usd"] == 0.0016


def test_record_persists_provider_prompt_version_and_cost(tmp_path):
    store = FileTokenUsageStore(str(tmp_path))
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)

    with patch("clio.ai.token_usage.write_json_atomic") as mock_write:
        store.record(
            "video_analyze",
            "gemini-2.5-flash",
            usage,
            provider="gemini",
            prompt_version="abc123def456",
            cost_usd=0.0007,
        )

        data = mock_write.call_args[0][1]
        entry = data["history"][0]
        assert entry["provider"] == "gemini"
        assert entry["prompt_version"] == "abc123def456"
        assert entry["cost_usd"] == 0.0007
        assert data["total"]["cost_usd"] == 0.0007
        assert data["by_model"]["gemini-2.5-flash"]["cost_usd"] == 0.0007
        assert data["by_task"]["video_analyze"]["cost_usd"] == 0.0007


def test_read_raw_backfills_cost_for_legacy_files(tmp_path):
    store = FileTokenUsageStore(str(tmp_path))
    legacy = {
        "total": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        "by_model": {"gemini": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10, "calls": 1}},
        "by_task": {"video_analyze": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10, "calls": 1}},
        "history": [],
    }
    write_json_atomic(store._path, legacy)

    stats = store.get_stats()
    assert stats["total"]["cost_usd"] == 0.0
    assert stats["by_model"]["gemini"]["cost_usd"] == 0.0
    assert stats["by_task"]["video_analyze"]["cost_usd"] == 0.0

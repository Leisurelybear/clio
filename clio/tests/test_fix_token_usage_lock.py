"""Regression tests: concurrent token stores on one project never lose records."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from clio.ai.base import TokenUsage
from clio.ai.token_usage import FileTokenUsageStore


def test_stores_for_same_dir_share_one_lock(tmp_path):
    assert FileTokenUsageStore(str(tmp_path))._lock is FileTokenUsageStore(str(tmp_path))._lock


def test_parallel_records_from_separate_instances_are_all_kept(tmp_path):
    usage = TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    def _record(index: int) -> None:
        FileTokenUsageStore(str(tmp_path)).record("analyze" if index % 2 else "refine", "m", usage)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_record, range(40)))

    stats = FileTokenUsageStore(str(tmp_path)).get_stats()
    assert len(stats["history"]) == 40
    assert stats["total"]["total_tokens"] == 80
    assert stats["by_model"]["m"]["calls"] == 40

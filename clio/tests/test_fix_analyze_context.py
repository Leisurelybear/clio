"""Regression tests for analyze.py trip-context handling (A-09 / A-10)."""

from __future__ import annotations

import threading

import clio.analyze as analyze_mod


def test_store_trip_context_is_safe_under_concurrent_eviction():
    analyze_mod._trip_context_cache.clear()
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def worker(n: int) -> None:
        try:
            start.wait()
            for i in range(500):
                analyze_mod._store_trip_context("/proj", f"/proj@{n}-{i}", "text")
        except Exception as exc:  # noqa: BLE001 - surface any worker failure in the assert below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len([k for k in analyze_mod._trip_context_cache if k.startswith("/proj@")]) == 1
    analyze_mod._trip_context_cache.clear()

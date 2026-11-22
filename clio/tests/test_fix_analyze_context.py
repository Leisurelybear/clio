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


def _default_context(tmp_path, monkeypatch):
    default_ctx = tmp_path / "default-trip-context.md"
    default_ctx.write_text("default context", encoding="utf-8")
    monkeypatch.setattr("clio.resources.find_resource", lambda relative: default_ctx)
    return default_ctx


def test_empty_project_dir_does_not_read_cwd_template(tmp_path, monkeypatch):
    analyze_mod._trip_context_cache.clear()
    cwd = tmp_path / "cwd"
    (cwd / "templates").mkdir(parents=True)
    (cwd / "templates" / "trip_context.md").write_text("cwd context", encoding="utf-8")
    monkeypatch.chdir(cwd)
    _default_context(tmp_path, monkeypatch)

    assert analyze_mod._read_trip_context("") == "default context"
    analyze_mod._trip_context_cache.clear()


def test_non_utf8_project_template_falls_back_to_default(tmp_path, monkeypatch):
    analyze_mod._trip_context_cache.clear()
    project = tmp_path / "project"
    (project / "templates").mkdir(parents=True)
    (project / "templates" / "trip_context.md").write_bytes("海边旅行".encode("gbk"))
    _default_context(tmp_path, monkeypatch)

    assert analyze_mod._read_trip_context(str(project)) == "default context"
    analyze_mod._trip_context_cache.clear()


def test_project_template_with_bom_is_read(tmp_path, monkeypatch):
    analyze_mod._trip_context_cache.clear()
    project = tmp_path / "project"
    (project / "templates").mkdir(parents=True)
    (project / "templates" / "trip_context.md").write_bytes(b"\xef\xbb\xbfproject context")
    _default_context(tmp_path, monkeypatch)

    assert analyze_mod._read_trip_context(str(project)) == "project context"
    analyze_mod._trip_context_cache.clear()

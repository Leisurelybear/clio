from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from clio.analyze_windows import (
    AnalyzeWindow,
    load_window_cache,
    save_window_cache,
    window_cache_dir,
)
from clio.config import AppConfig
from clio.tasks.analyze import run_analyze_all


def _cfg(tmp_path: Path) -> AppConfig:
    cfg = cast(
        AppConfig,
        SimpleNamespace(
            paths=SimpleNamespace(
                output_dir=tmp_path / "output",
                ffmpeg="ffmpeg",
                ffprobe="ffprobe",
            ),
            compressed_dir=tmp_path / "output" / "compressed",
            texts_dir=tmp_path / "output" / "texts",
            summary_csv=tmp_path / "output" / "summary.csv",
            naming=SimpleNamespace(index_width=3),
            analyze=SimpleNamespace(
                skip_existing=False,
                max_analyze_duration_min=0,
                window_max_min=15,
                window_overlap_sec=20,
                max_workers=1,
            ),
            ai=SimpleNamespace(context="", tasks={}, providers={}),
            compress=SimpleNamespace(),
            plan=SimpleNamespace(max_clips_per_day=10, target_duration_sec=300),
            script=SimpleNamespace(target_words=150),
            project_dir=tmp_path / "project",
        ),
    )
    assert cfg.project_dir is not None
    cfg.project_dir.mkdir(parents=True, exist_ok=True)
    cfg.compressed_dir.mkdir(parents=True, exist_ok=True)
    cfg.texts_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _add_original(cfg) -> Path:
    from clio.tasks._video_loader import load_selected_videos, save_selected_videos

    src = Path(cfg.project_dir) / "GL010695.mp4"
    src.write_bytes(b"\x00" * 1000)
    existing = load_selected_videos(cfg.project_dir)
    if src.resolve() not in {p.resolve() for p in existing}:
        existing.append(src)
    save_selected_videos(cfg.project_dir, existing)
    return src


def _common_mocks(monkeypatch) -> None:
    monkeypatch.setattr("clio.tasks._helpers.resolve_binary", lambda *a: "ffprobe")
    monkeypatch.setattr("clio.tasks._helpers.probe_video_info", lambda *a, **kw: {})
    monkeypatch.setattr("clio.tasks.analyze.resolve_binary", lambda *a: "ffprobe")
    monkeypatch.setattr("clio.tasks.analyze._build_stem", lambda idx, title, cfg: f"{idx:03d}_{title}")
    monkeypatch.setattr("clio.tasks.analyze._write_text_file", lambda *a: None)
    monkeypatch.setattr("clio.tasks.analyze.is_legacy_split_path", lambda p: False)


def test_window_cache_roundtrip(tmp_path: Path) -> None:
    cache = window_cache_dir(tmp_path, "001_GL010695")
    window = AnalyzeWindow(1, 880.0, 1780.0)
    analysis = {"title": "W2", "timeline": [{"start": 900, "end": 910, "text": "x"}]}

    save_window_cache(
        cache,
        window=window,
        lineage="abc",
        source_size=123,
        source_mtime_ns=456,
        analysis=analysis,
    )

    assert (
        load_window_cache(
            cache,
            window=window,
            lineage="abc",
            source_size=123,
            source_mtime_ns=456,
        )
        == analysis
    )


def test_window_cache_rejects_lineage_and_source_changes(tmp_path: Path) -> None:
    cache = window_cache_dir(tmp_path, "001_GL010695")
    window = AnalyzeWindow(0, 0.0, 900.0)
    save_window_cache(
        cache,
        window=window,
        lineage="abc",
        source_size=123,
        source_mtime_ns=456,
        analysis={"title": "W1"},
    )

    assert load_window_cache(cache, window=window, lineage="other", source_size=123, source_mtime_ns=456) is None
    assert load_window_cache(cache, window=window, lineage="abc", source_size=124, source_mtime_ns=456) is None
    assert load_window_cache(cache, window=window, lineage="abc", source_size=123, source_mtime_ns=457) is None


def test_window_cache_ignores_corrupt_or_wrong_window(tmp_path: Path) -> None:
    cache = window_cache_dir(tmp_path, "001_GL010695")
    window = AnalyzeWindow(0, 0.0, 900.0)
    path = save_window_cache(
        cache,
        window=window,
        lineage="abc",
        source_size=123,
        source_mtime_ns=456,
        analysis={"title": "W1"},
    )

    path.write_text("{not json", encoding="utf-8")
    assert load_window_cache(cache, window=window, lineage="abc", source_size=123, source_mtime_ns=456) is None

    save_window_cache(
        cache,
        window=window,
        lineage="abc",
        source_size=123,
        source_mtime_ns=456,
        analysis={"title": "W1"},
    )
    assert (
        load_window_cache(
            cache,
            window=AnalyzeWindow(1, 880.0, 1780.0),
            lineage="abc",
            source_size=123,
            source_mtime_ns=456,
        )
        is None
    )


def test_failed_window_run_resumes_from_cache(monkeypatch, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _add_original(cfg)
    compressed = cfg.compressed_dir / "001_GL010695.mp4"
    compressed.write_bytes(b"video")

    _common_mocks(monkeypatch)
    monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 2400.0)

    slice_calls: list[int] = []

    def fake_slice(*, source, window, dest_dir, ffmpeg, ffprobe=None, run_ffmpeg=None, cancel_event=None):
        slice_calls.append(window.index)
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / f"{source.stem}_w{window.index:02d}.mp4"
        out.write_bytes(b"x")
        return out

    monkeypatch.setattr("clio.tasks.analyze.slice_window_video", fake_slice)

    summarize_calls: list[dict] = []

    def fake_summary(analysis, *args, **kwargs):
        summarize_calls.append(analysis)
        return {**analysis, "title": "Whole", "summary": "Full", "location": "X"}

    monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", fake_summary)

    analyze_calls: list[int] = []

    def fail_second(*args, **kwargs):
        analyze_calls.append(len(analyze_calls))
        if len(analyze_calls) >= 2:
            raise RuntimeError("boom")
        return {"title": "W1", "summary": "s1", "timeline": [{"start": 5, "end": 10, "text": "e1"}], "location": "X"}

    monkeypatch.setattr("clio.tasks.analyze.analyze_video", fail_second)

    cache = window_cache_dir(cfg.paths.output_dir, compressed.stem)
    with pytest.raises(RuntimeError):
        run_analyze_all(cfg)
    assert len(list(cache.glob("*.json"))) == 1

    analyze_calls.clear()
    slice_calls.clear()

    def succeed(*args, **kwargs):
        analyze_calls.append(len(analyze_calls))
        return {
            "title": f"W{len(analyze_calls)}",
            "summary": "s",
            "timeline": [{"start": 5, "end": 10, "text": "e"}],
            "location": "X",
        }

    monkeypatch.setattr("clio.tasks.analyze.analyze_video", succeed)

    records = run_analyze_all(cfg)

    assert len(records) == 1
    assert len(analyze_calls) == 2
    assert slice_calls == [1, 2]
    assert len(summarize_calls) == 1
    assert not list(cache.glob("*.json"))
    assert len(list(cfg.texts_dir.glob("*.json"))) == 1


def test_successful_run_clears_stale_window_cache(monkeypatch, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _add_original(cfg)
    compressed = cfg.compressed_dir / "001_GL010695.mp4"
    compressed.write_bytes(b"video")
    cache = window_cache_dir(cfg.paths.output_dir, compressed.stem)
    save_window_cache(
        cache,
        window=AnalyzeWindow(0, 0.0, 900.0),
        lineage="stale",
        source_size=1,
        source_mtime_ns=1,
        analysis={"title": "stale"},
    )

    _common_mocks(monkeypatch)
    monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 2400.0)
    monkeypatch.setattr(
        "clio.tasks.analyze.slice_window_video",
        lambda *, source, window, dest_dir, **kwargs: (dest_dir.mkdir(parents=True, exist_ok=True), dest_dir / "w.mp4")[
            1
        ],
    )
    monkeypatch.setattr(
        "clio.tasks.analyze.analyze_video",
        lambda *a, **kw: {"title": "W", "summary": "s", "timeline": [], "location": "X"},
    )
    monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", lambda analysis, *a, **kw: analysis)

    records = run_analyze_all(cfg)

    assert len(records) == 1
    assert not cache.exists()
    assert len(list(cfg.texts_dir.glob("*.json"))) == 1

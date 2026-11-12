"""Tests for clio/tasks/analyze.py — run_analyze_all."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from clio.analyze_windows import select_sparse_retry_gap
from clio.config import AppConfig
from clio.tasks.analyze import run_analyze_all
from clio.vmeta import VideoMeta


def _cfg(tmp_path: Path) -> AppConfig:
    cfg = SimpleNamespace(
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
            max_analyze_duration_min=30,
            window_max_min=15,
            window_overlap_sec=20,
            media_resolution="medium",
            max_workers=1,
        ),
        ai=SimpleNamespace(context=""),
        compress=SimpleNamespace(fps=3),
        plan=SimpleNamespace(max_clips_per_day=10, target_duration_sec=300),
        script=SimpleNamespace(target_words=150),
        project_dir=tmp_path / "project",
    )
    Path(cfg.project_dir).mkdir(parents=True, exist_ok=True)
    cfg.compressed_dir.mkdir(parents=True, exist_ok=True)
    cfg.texts_dir.mkdir(parents=True, exist_ok=True)
    return cast(AppConfig, cfg)


def _add_original(cfg, name: str = "GL010695.mp4"):
    from clio.tasks._video_loader import load_selected_videos, save_selected_videos

    Path(cfg.project_dir).mkdir(parents=True, exist_ok=True)
    src = Path(cfg.project_dir) / name
    src.write_bytes(b"\x00" * 1000)
    existing = load_selected_videos(cfg.project_dir)
    if src.resolve() not in {p.resolve() for p in existing}:
        existing.append(src)
    save_selected_videos(cfg.project_dir, existing)
    return src


def _common_mocks(monkeypatch):
    """Shared mocks for analyz tests — resolve_binary, probe_video_info."""
    # _write_csv (in _helpers.py) calls resolve_binary + probe_video_info
    monkeypatch.setattr("clio.tasks._helpers.resolve_binary", lambda *a: "ffprobe")
    monkeypatch.setattr("clio.tasks._helpers.probe_video_info", lambda *a, **kw: {})
    # run_analyze_all (in analyze.py) also calls resolve_binary for duration gate
    monkeypatch.setattr("clio.tasks.analyze.resolve_binary", lambda *a: "ffprobe")
    monkeypatch.setattr("clio.tasks.analyze.detect_scene_boundaries", lambda *a, **kw: [])
    monkeypatch.setattr("clio.tasks.analyze.select_sparse_retry_gap", lambda *a, **kw: None)
    # Prevent AI calls
    monkeypatch.setattr("clio.tasks.analyze._build_stem", lambda idx, title, cfg: f"{idx:03d}_{title}")
    monkeypatch.setattr("clio.tasks.analyze._write_text_file", lambda *a: None)


def test_effective_context_falls_back_when_gpmf_enrichment_fails(monkeypatch, tmp_path: Path, capsys):
    from clio.tasks.analyze import _effective_analysis_context

    cfg = _cfg(tmp_path)
    original = tmp_path / "video.mp4"
    original.write_bytes(b"video")

    def fail(*args, **kwargs):
        raise ValueError("broken sidecar")

    monkeypatch.setattr("clio.gpmf.merge_telemetry_into_context", fail)

    assert _effective_analysis_context(cfg, original, "base context") == "base context"
    assert "继续使用基础 context" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("analyze", "window_max_min", 16),
        ("analyze", "window_overlap_sec", 25),
        ("analyze", "media_resolution", "high"),
        ("compress", "codec", "libx265"),
        ("compress", "fps", 30),
        ("compress", "remove_audio", True),
        ("compress", "crf", 28),
        ("compress", "target_size_mb", 8),
        ("compress", "max_width", 1280),
    ],
)
def test_analysis_lineage_changes_with_window_and_compression_settings(
    tmp_path: Path, section: str, field: str, value: object
):
    from clio.tasks.analyze import _analysis_lineage_fingerprint

    cfg = _cfg(tmp_path)
    base = _analysis_lineage_fingerprint(cfg)
    setattr(getattr(cfg, section), field, value)

    assert _analysis_lineage_fingerprint(cfg) != base


def test_window_summary_lineage_changes_only_when_multi_window(tmp_path: Path):
    from clio.config import TaskConfig
    from clio.tasks.analyze import _analysis_lineage_fingerprint

    cfg = _cfg(tmp_path)
    ai = cast(Any, cfg.ai)
    ai.tasks = {"refine_text": TaskConfig(provider="text", model="model-a")}
    ai.providers = {"text": SimpleNamespace(type="openai", base_url="", max_tokens=0)}
    single_before = _analysis_lineage_fingerprint(cfg)
    multi_before = _analysis_lineage_fingerprint(cfg, multi_window=True)

    cfg.ai.tasks["refine_text"] = TaskConfig(provider="text", model="model-b")

    assert _analysis_lineage_fingerprint(cfg) == single_before
    assert _analysis_lineage_fingerprint(cfg, multi_window=True) != multi_before


def test_analysis_logs_timeline_coverage(monkeypatch, tmp_path: Path, capsys):
    cfg = _cfg(tmp_path)
    _add_original(cfg, "GL010695.mp4")
    (cfg.compressed_dir / "001_GL010695.mp4").write_bytes(b"video")

    _common_mocks(monkeypatch)
    monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 100.0)
    monkeypatch.setattr("clio.tasks.analyze.extract_cover_frame", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "clio.tasks.analyze.analyze_video",
        lambda *args, **kwargs: {
            "title": "Coverage",
            "summary": "s",
            "location": "x",
            "timeline": [
                {"start": "00:10", "end": "00:30", "description": "a"},
                {"start": "00:20", "end": "00:40", "description": "b"},
                {"start": "01:20", "end": "01:30", "description": "c"},
            ],
        },
    )

    records = run_analyze_all(cfg)

    assert len(records) == 1
    assert "[analyze] 001_GL010695.mp4: rows=3 coverage=40.0% max_gap=40.0s" in capsys.readouterr().out


def test_build_day_labels_uses_capture_dates_and_filename_fallback(monkeypatch, tmp_path: Path):
    from clio.tasks.analyze import _build_day_labels

    cfg = _cfg(tmp_path)
    first = tmp_path / "20261108_120000.mp4"
    second = tmp_path / "20261109_090000.mp4"
    unknown = tmp_path / "GL010683.mp4"
    for path in (first, second, unknown):
        path.write_bytes(b"video")

    def fake_probe(cmd, **kwargs):
        path = Path(cmd[-1])
        date = "2026-11-08T12:00:00Z" if path == first else "2026-11-09T09:00:00Z"
        return type("ProbeResult", (), {"stdout": json.dumps({"format": {"tags": {"creation_time": date}}})})()

    monkeypatch.setattr("clio.tasks.analyze.resolve_binary", lambda *args: "ffprobe")
    monkeypatch.setattr("clio.tasks.analyze.run_probe", fake_probe)
    items = [(tmp_path / "001_first.mp4", first, "001"), (tmp_path / "002_second.mp4", second, "002")]

    labels = _build_day_labels(cfg, items)

    assert labels[str(first.resolve())] == "day1"
    assert labels[str(second.resolve())] == "day2"


def test_selected_analysis_keeps_project_day_mapping(monkeypatch, tmp_path: Path):
    cfg = _cfg(tmp_path)
    first = _add_original(cfg, "20261108_120000.mp4")
    _add_original(cfg, "20261109_090000.mp4")
    (cfg.compressed_dir / "001_20261108_120000.mp4").write_bytes(b"video")
    (cfg.compressed_dir / "002_20261109_090000.mp4").write_bytes(b"video")

    _common_mocks(monkeypatch)
    monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)

    def fake_probe(cmd, **kwargs):
        source = Path(cmd[-1])
        date = "2026-11-08T12:00:00Z" if source == first else "2026-11-09T09:00:00Z"
        return type("ProbeResult", (), {"stdout": json.dumps({"format": {"tags": {"creation_time": date}}})})()

    monkeypatch.setattr("clio.tasks.analyze.run_probe", fake_probe)
    monkeypatch.setattr(
        "clio.tasks.analyze.analyze_video",
        lambda *args, **kwargs: {"title": "selected", "summary": "s", "location": "x"},
    )

    records = run_analyze_all(cfg, files=["002_20261109_090000"])

    assert len(records) == 1
    saved = next(cfg.texts_dir.glob("*.json"))
    assert json.loads(saved.read_text(encoding="utf-8"))["day_label"] == "day2"


def test_same_named_originals_only_analyze_selected_path(monkeypatch, tmp_path: Path):
    from clio.tasks._video_loader import save_selected_videos

    cfg = _cfg(tmp_path)
    a_dir = tmp_path / "a"
    b_dir = tmp_path / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    a = a_dir / "clip.mp4"
    b = b_dir / "clip.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    assert cfg.project_dir is not None
    save_selected_videos(cfg.project_dir, [a, b])

    comp_a = cfg.compressed_dir / "001_clip.mp4"
    comp_b = cfg.compressed_dir / "002_clip.mp4"
    comp_a.write_bytes(b"ca")
    comp_b.write_bytes(b"cb")
    VideoMeta.build(a, comp_a, 60.0, 60.0).write(comp_a)
    VideoMeta.build(b, comp_b, 60.0, 60.0).write(comp_b)

    _common_mocks(monkeypatch)
    monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
    seen: list[str] = []

    def _fake_analyze(video_path, *args, **kwargs):
        seen.append(str(video_path))
        return {"title": "clip", "summary": "s", "location": "x"}

    monkeypatch.setattr("clio.tasks.analyze.analyze_video", _fake_analyze)

    records = run_analyze_all(cfg, files=[str(a)])

    assert len(records) == 1
    assert seen == [str(comp_a)]


def test_failure_counter_is_thread_safe():
    from clio.tasks.analyze import _FailureCounter

    counter = _FailureCounter()
    barrier = threading.Barrier(8)

    def increment() -> None:
        barrier.wait()
        for _ in range(1000):
            counter.increment()

    threads = [threading.Thread(target=increment) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counter.value == 8000


class TestRunAnalyzeAll:
    def test_analyze_single_file(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {
                "title": "Test Clip",
                "summary": "A test",
                "location": "Paris",
                "source_file": "GL010695.mp4",
            },
        )

        records = run_analyze_all(cfg)
        assert len(records) == 1
        assert records[0].index == 1

    def test_low_compress_fps_warns_once_per_batch(self, monkeypatch, tmp_path: Path, capsys):
        from clio.i18n import t
        from clio.progress import ProgressTracker

        cfg = _cfg(tmp_path)
        cast(Any, cfg.compress).fps = 1
        for name in ("GL010695.mp4", "GL010696.mp4"):
            _add_original(cfg, name)
        (cfg.compressed_dir / "001_GL010695.mp4").write_bytes(b"video")
        (cfg.compressed_dir / "002_GL010696.mp4").write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {"title": "Test Clip", "summary": "A test", "location": "Paris"},
        )
        tracker = ProgressTracker(tmp_path / "tracker")

        records = run_analyze_all(cfg, tracker=tracker)

        warning = t("task.analyze.low_compress_fps", {"fps": 1})
        assert len(records) == 2
        assert capsys.readouterr().out.count(warning) == 1
        progress = json.loads((tmp_path / "tracker" / ".progress.json").read_text(encoding="utf-8"))
        assert progress["logs"].count(warning) == 1

    def test_skip_existing(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)
        existing_json = cfg.texts_dir / "001_Test_Clip.json"
        existing_json.write_text(
            json.dumps({"title": "Test Clip", "summary": "A test", "source_file": "GL010695.mp4"}), encoding="utf-8"
        )

        _common_mocks(monkeypatch)
        analyze_called = False

        def _analyze(*a):
            nonlocal analyze_called
            analyze_called = True
            return {"title": "New"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        cfg.analyze.skip_existing = True
        records = run_analyze_all(cfg)
        assert len(records) == 1
        assert analyze_called is False

    def test_skip_invalidated_when_lineage_changes(self, monkeypatch, tmp_path: Path):
        """Changing the prompt/model grants a new _lineage and re-analyzes."""
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)

        from clio.tasks.analyze import _analysis_lineage_fingerprint

        stale_lineage = {"title": "Old", "source_file": "GL010695.mp4", "_lineage": "stale-old-lineage"}
        existing_json = cfg.texts_dir / "001_Old.json"
        existing_json.write_text(json.dumps(stale_lineage), encoding="utf-8")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a, **kw: 60.0)

        def _analyze(*a, **kw):
            return {
                "title": "Fresh",
                "summary": "re-analyzed",
                "location": "Tokyo",
                "source_file": "GL010695.mp4",
            }

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        cfg.analyze.skip_existing = True
        records = run_analyze_all(cfg)
        assert len(records) == 1
        analysis = records[0].analysis
        assert analysis is not None
        assert analysis["title"] == "Fresh"

        # Old stale files are removed after the fresh one is committed.
        assert not existing_json.exists()

        saved = sorted(cfg.texts_dir.glob("*.json"))
        assert len(saved) == 1
        assert saved[0].name != existing_json.name
        assert json.loads(saved[0].read_text(encoding="utf-8"))["_lineage"] == _analysis_lineage_fingerprint(cfg)

        # Second run now skips (lineage matches).
        analyze_calls: list = []

        def _analyze2(*a, **kw):
            analyze_calls.append(a)
            return {"title": "Fresh2", "source_file": "GL010695.mp4"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze2)
        records2 = run_analyze_all(cfg)
        assert len(records2) == 1
        assert analyze_calls == []

    def test_duration_gate_skips_long_legacy_segment(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        # Legacy segment name so is_legacy_split_path is true
        comp = cfg.compressed_dir / "001_GL010695_seg01.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 3600.0)
        analyze_called = False

        def _analyze(*a, **kw):
            nonlocal analyze_called
            analyze_called = True
            return {"title": "Should not be called"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        cfg.analyze.max_analyze_duration_min = 30
        records = run_analyze_all(cfg)
        assert len(records) == 0
        assert analyze_called is False

    def test_duration_gate_blocks_windowed_whole_file(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 30
        cfg.analyze.window_max_min = 15
        cfg.analyze.window_overlap_sec = 20
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 2400.0)
        monkeypatch.setattr("clio.tasks.analyze.is_legacy_split_path", lambda p: False)

        analyze_called = False

        def fake_analyze(*args, **kwargs):
            nonlocal analyze_called
            analyze_called = True
            return {"title": "W", "summary": "s", "timeline": [], "location": "X"}

        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            fake_analyze,
        )

        records = run_analyze_all(cfg)
        assert records == []
        assert analyze_called is False

    def test_duration_gate_allows_short_video(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {"title": "Short Clip", "summary": "A short test", "location": "Paris"},
        )

        cfg.analyze.max_analyze_duration_min = 30
        records = run_analyze_all(cfg)
        assert len(records) == 1
        assert records[0].index == 1

    def test_no_matching_original(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        comp = cfg.compressed_dir / "001_NOMATCH.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        analyze_called = False

        def _analyze(*a):
            nonlocal analyze_called
            analyze_called = True
            return {"title": "x"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        records = run_analyze_all(cfg)
        assert len(records) == 0
        assert analyze_called is False

    def test_empty_compressed_dir(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _common_mocks(monkeypatch)
        records = run_analyze_all(cfg)
        assert len(records) == 0

    def test_files_filter(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        pairs = [("GL010683", "001_GL010683"), ("GL010684", "002_GL010684"), ("GL010685", "003_GL010685")]
        for orig_stem, comp_stem in pairs:
            _add_original(cfg, f"{orig_stem}.mp4")
            (cfg.compressed_dir / f"{comp_stem}.mp4").write_bytes(b"\x00" * 100)
        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {"title": "x", "summary": "x", "location": "x"},
        )

        records = run_analyze_all(cfg, files=["002_GL010684"])
        assert len(records) == 1
        compressed_path = records[0].compressed_path
        assert compressed_path is not None
        assert compressed_path.name == "002_GL010684.mp4"

    def test_files_filter_merges_summary_csv_not_truncate(self, monkeypatch, tmp_path: Path):
        """Selection re-analyze must not drop other rows from summary.csv (I3 family)."""
        import csv

        cfg = _cfg(tmp_path)
        pairs = [("GL010683", "001_GL010683"), ("GL010684", "002_GL010684")]
        for orig_stem, comp_stem in pairs:
            _add_original(cfg, f"{orig_stem}.mp4")
            (cfg.compressed_dir / f"{comp_stem}.mp4").write_bytes(b"\x00" * 100)
        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {"title": "x", "summary": "x", "location": "x"},
        )

        # Full analyze → 2 CSV rows
        run_analyze_all(cfg)
        with cfg.summary_csv.open(encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        stems_before = {r["stem"] for r in rows}

        # Re-analyze one file only
        run_analyze_all(cfg, files=["002_GL010684"])
        with cfg.summary_csv.open(encoding="utf-8-sig") as f:
            rows2 = list(csv.DictReader(f))
        stems_after = {r["stem"] for r in rows2}
        assert len(rows2) == 2, f"expected merge keep 2 rows, got {len(rows2)}: {stems_after}"
        assert stems_before == stems_after

    def test_slow_video_does_not_block_worker_refill(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_workers = 2
        for name in ("GL010695.mp4", "GL010696.mp4", "GL010697.mp4"):
            _add_original(cfg, name)
        for index, name in enumerate(("GL010695", "GL010696", "GL010697"), start=1):
            (cfg.compressed_dir / f"{index:03d}_{name}.mp4").write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze._build_day_labels", lambda *args: {})
        second_finished = threading.Event()
        third_started = threading.Event()
        started: list[str] = []
        started_lock = threading.Lock()

        def fake_process(compressed, *args, **kwargs):
            name = compressed.name
            with started_lock:
                started.append(name)
            if name == "001_GL010695.mp4":
                assert third_started.wait(2), "third video never started"
            elif name == "002_GL010696.mp4":
                second_finished.set()
            elif name == "003_GL010697.mp4":
                assert second_finished.is_set()
                third_started.set()
            return None

        monkeypatch.setattr("clio.tasks.analyze._process_video_item", fake_process)

        run_analyze_all(cfg)

        assert set(started) == {
            "001_GL010695.mp4",
            "002_GL010696.mp4",
            "003_GL010697.mp4",
        }

    def test_single_file_with_vindex_includes_all_segments(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        src = _add_original(cfg, "GL010695.mp4")
        comp1 = cfg.compressed_dir / "001_GL010695_seg01.mp4"
        comp1.write_bytes(b"\x00" * 100)
        comp2 = cfg.compressed_dir / "002_GL010695_seg02.mp4"
        comp2.write_bytes(b"\x00" * 100)

        from clio.vmeta import SegmentEntry, VideoIndex

        segs = [
            SegmentEntry(
                index="001",
                filename="001_GL010695_seg01.mp4",
                offset_sec=0.0,
                duration_sec=30.0,
                segment_number=1,
                total_segments=2,
            ),
            SegmentEntry(
                index="002",
                filename="002_GL010695_seg02.mp4",
                offset_sec=30.0,
                duration_sec=30.0,
                segment_number=2,
                total_segments=2,
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=60.0, segments=segs)
        vindex.write(cfg.compressed_dir)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 30.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {"title": "Seg", "summary": "x", "location": "x", "source_file": "GL010695.mp4"},
        )

        records = run_analyze_all(cfg, single_file=src)
        assert len(records) == 2
        first_path = records[0].compressed_path
        second_path = records[1].compressed_path
        assert first_path is not None
        assert second_path is not None
        assert first_path.name == "001_GL010695_seg01.mp4"
        assert second_path.name == "002_GL010695_seg02.mp4"

    def test_overwrite_flag(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)
        existing = cfg.texts_dir / "001_Test.json"
        existing.write_text(
            json.dumps({"title": "x", "summary": "x", "location": "x", "source_file": "GL010695.mp4"}),
            encoding="utf-8",
        )
        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        analyze_called = False

        def _analyze(*a, **kw):
            nonlocal analyze_called
            analyze_called = True
            return {"title": "overwritten"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        cfg.analyze.skip_existing = True
        records = run_analyze_all(cfg, overwrite=True)
        assert len(records) == 1
        assert analyze_called is True
        assert not existing.exists()

    def test_overwrite_removes_all_old_titles_after_success(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)
        old_files = [cfg.texts_dir / "001_Old.json", cfg.texts_dir / "001_Older.json"]
        for path in old_files:
            path.write_text(
                json.dumps({"title": path.stem, "source_file": "GL010695.mp4"}),
                encoding="utf-8",
            )
            path.with_suffix(".txt").write_text("old", encoding="utf-8")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {"title": "Fresh", "summary": "new", "location": "X"},
        )

        records = run_analyze_all(cfg, overwrite=True)

        assert len(records) == 1
        assert sorted(path.name for path in cfg.texts_dir.glob("*.json")) == ["001_Fresh.json"]
        assert not any(path.with_suffix(".txt").exists() for path in old_files)

    def test_overwrite_failure_keeps_old_results(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)
        existing = cfg.texts_dir / "001_Old.json"
        existing.write_text(json.dumps({"title": "Old", "source_file": "GL010695.mp4"}), encoding="utf-8")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 60.0)
        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("failed"))
        )

        with pytest.raises(RuntimeError):
            run_analyze_all(cfg, overwrite=True)

        assert existing.exists()

    def test_multi_window_merges_and_writes_one_json(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        cfg.analyze.window_max_min = 15
        cfg.analyze.window_overlap_sec = 20
        cast(Any, cfg.ai).tasks = {"refine_text": SimpleNamespace(provider="text", model="model")}
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 2400.0)
        monkeypatch.setattr("clio.tasks.analyze.is_legacy_split_path", lambda p: False)

        call_args: list[tuple[str, dict]] = []
        summary_calls: list[dict] = []

        def _summarize(analysis, *args, **kwargs):
            summary_calls.append(analysis)
            result = dict(analysis)
            result.update(
                title="Whole trip",
                summary="Full journey",
                location="X and Y",
                mood="varied",
                suggested_use="journey",
            )
            return result

        monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", _summarize)

        def _analyze(path, *a, **kw):
            call_args.append((str(path), kw))
            return {
                "title": f"W{len(call_args)}",
                "summary": f"sum{len(call_args)}",
                "timeline": [{"start": 5, "end": 10, "text": f"e{len(call_args)}"}],
                "location": "X",
            }

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        records = run_analyze_all(cfg)
        assert len(records) == 1
        assert len(call_args) >= 3
        assert {path for path, _kwargs in call_args} == {str(comp)}
        assert [(kwargs["video_start_sec"], kwargs["video_end_sec"]) for _path, kwargs in call_args[:3]] == [
            (0.0, 900.0),
            (880.0, 1780.0),
            (1760.0, 2400.0),
        ]
        assert all(kwargs["video_fps"] == 3 for _path, kwargs in call_args)
        assert all(kwargs["media_resolution"] == "medium" for _path, kwargs in call_args)
        assert len(summary_calls) == 1
        assert records[0].analysis is not None
        assert records[0].analysis["title"] == "Whole trip"
        assert records[0].analysis["summary"] == "Full journey"
        assert records[0].analysis["location"] == "X and Y"
        assert records[0].analysis["timeline"] == summary_calls[0]["timeline"]
        assert records[0].analysis["highlights"] == summary_calls[0]["highlights"]
        assert len(records[0].analysis.get("analyze_windows", [])) >= 3
        texts = list(cfg.texts_dir.glob("*.json"))
        assert len(texts) == 1
        data = json.loads(texts[0].read_text(encoding="utf-8"))
        assert data["timeline"][0]["start"] == 5
        assert any(t["start"] >= 880 for t in data["timeline"])

    def test_single_window_does_not_summarize(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
        calls: list[dict] = []

        def fake_analyze(path, *args, **kwargs):
            calls.append(kwargs)
            return {"title": "Short", "summary": "One window", "timeline": []}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)
        monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", lambda *a, **kw: pytest.fail("unexpected"))

        records = run_analyze_all(cfg)

        assert len(records) == 1
        assert len(calls) == 1
        assert calls[0]["video_start_sec"] == 0.0
        assert calls[0]["video_end_sec"] == 60.0
        assert calls[0]["video_fps"] == 3
        assert calls[0]["media_resolution"] == "medium"

    def test_sparse_single_window_retries_once_and_saves_merged_timeline(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.select_sparse_retry_gap", select_sparse_retry_gap)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
        calls: list[tuple[float | None, float | None]] = []

        def fake_analyze(path, *args, **kwargs):
            calls.append((kwargs["video_start_sec"], kwargs["video_end_sec"]))
            if len(calls) == 1:
                return {
                    "title": "Sparse",
                    "summary": "base",
                    "location": "X",
                    "timeline": [{"start": 0, "end": 10, "text": "base"}],
                }
            return {
                "title": "Completion",
                "summary": "retry",
                "location": "X",
                "timeline": [{"start": 5, "end": 15, "text": "retry"}],
                "key_moments": [{"start": 5, "end": 8, "text": "moment"}],
            }

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg)

        assert len(records) == 1
        assert calls == [(0.0, 60.0), (10.0, 60.0)]
        analysis = records[0].analysis
        assert analysis is not None
        assert analysis["summary"] == "base\nretry"
        assert [(item["start"], item["text"]) for item in analysis["timeline"]] == [
            (0, "base"),
            (15, "retry"),
        ]
        assert analysis["key_moments"][0]["start"] == 15
        assert analysis["key_moments"][0]["text"] == "moment"

    def test_dense_single_window_does_not_retry(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.select_sparse_retry_gap", select_sparse_retry_gap)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
        calls: list[tuple[float | None, float | None]] = []

        def fake_analyze(path, *args, **kwargs):
            calls.append((kwargs["video_start_sec"], kwargs["video_end_sec"]))
            return {
                "title": "Dense",
                "summary": "covered",
                "location": "X",
                "timeline": [{"start": 0, "end": 60, "text": "full"}],
            }

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg)

        assert len(records) == 1
        assert calls == [(0.0, 60.0)]

    def test_multi_window_sparse_window_retries_once(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.select_sparse_retry_gap", select_sparse_retry_gap)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 2400.0)
        monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", lambda analysis, *a, **kw: analysis)
        calls: list[tuple[float | None, float | None]] = []
        first_window_requested = False

        def fake_analyze(path, *args, **kwargs):
            nonlocal first_window_requested
            span = (kwargs["video_start_sec"], kwargs["video_end_sec"])
            calls.append(span)
            if span == (0.0, 900.0) and not first_window_requested:
                first_window_requested = True
                return {
                    "title": "Sparse",
                    "summary": "base",
                    "location": "X",
                    "timeline": [{"start": 0, "end": 10, "text": "base"}],
                }
            if span == (10.0, 900.0):
                return {
                    "title": "Completion",
                    "summary": "retry",
                    "location": "X",
                    "timeline": [{"start": 5, "end": 15, "text": "retry"}],
                }
            assert span is not None
            return {
                "title": "Dense",
                "summary": "covered",
                "location": "X",
                "timeline": [{"start": 0, "end": span[1] - span[0], "text": "full"}],
            }

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg)

        assert len(records) == 1
        assert len(calls) == 4
        assert calls.count((10.0, 900.0)) == 1
        analysis = records[0].analysis
        assert analysis is not None
        assert any(item.get("text") == "retry" for item in analysis["timeline"])

    def test_sparse_retry_failure_preserves_original_analysis(self, monkeypatch, tmp_path: Path, capsys):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.select_sparse_retry_gap", select_sparse_retry_gap)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
        calls = 0

        def fake_analyze(path, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "title": "Sparse",
                    "summary": "base",
                    "location": "X",
                    "timeline": [{"start": 0, "end": 10, "text": "base"}],
                }
            raise ValueError("completion failed")

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg)

        assert len(records) == 1
        assert calls == 2
        analysis = records[0].analysis
        assert analysis is not None
        assert analysis["timeline"] == [{"start": 0, "end": 10, "text": "base"}]
        assert "completion failed" in capsys.readouterr().out

    def test_sparse_retry_cancellation_propagates(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")
        cancel_event = threading.Event()

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.select_sparse_retry_gap", select_sparse_retry_gap)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
        calls = 0

        def fake_analyze(path, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "title": "Sparse",
                    "summary": "base",
                    "location": "X",
                    "timeline": [{"start": 0, "end": 10, "text": "base"}],
                }
            cancel_event.set()
            raise RuntimeError("stopped")

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg, cancel_event=cancel_event)

        assert records == []
        assert calls == 2
        assert list(cfg.texts_dir.glob("*.json")) == []

    def test_single_window_passes_detected_scene_boundaries(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
        detected: list[tuple[str, float, str]] = []

        def fake_detect(path, duration, ffmpeg, **kwargs):
            detected.append((str(path), duration, str(ffmpeg)))
            return [0.0, 20.0, 60.0]

        monkeypatch.setattr("clio.tasks.analyze.detect_scene_boundaries", fake_detect)
        calls: list[dict] = []

        def fake_analyze(path, *args, **kwargs):
            calls.append(kwargs)
            return {"title": "Short", "summary": "One window", "timeline": []}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg)

        assert len(records) == 1
        assert detected == [(str(comp), 60.0, "ffmpeg")]
        assert calls[0]["scene_boundaries"] == [0.0, 20.0, 60.0]

    def test_multi_window_slices_scene_boundaries_to_each_window(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        cfg.analyze.window_max_min = 15
        cfg.analyze.window_overlap_sec = 20
        cast(Any, cfg.ai).tasks = {"refine_text": SimpleNamespace(provider="text", model="model")}
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"video")

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 2400.0)
        monkeypatch.setattr("clio.tasks.analyze.is_legacy_split_path", lambda path: False)
        monkeypatch.setattr(
            "clio.tasks.analyze.detect_scene_boundaries",
            lambda *args, **kwargs: [0.0, 100.0, 930.0, 1800.0, 2330.0, 2400.0],
        )
        monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", lambda analysis, *a, **kw: analysis)
        calls: list[dict] = []

        def fake_analyze(path, *args, **kwargs):
            calls.append(kwargs)
            return {"title": f"W{len(calls)}", "summary": "s", "timeline": [], "location": "X"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        run_analyze_all(cfg)

        assert calls[0]["scene_boundaries"] == [0.0, 100.0, 900.0]
        assert calls[1]["scene_boundaries"] == [0.0, 50.0, 900.0]
        assert calls[2]["scene_boundaries"] == [0.0, 40.0, 570.0, 640.0]

    def test_window_failure_writes_nothing(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        cfg.analyze.window_max_min = 15
        cfg.analyze.window_overlap_sec = 20
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 2400.0)
        monkeypatch.setattr("clio.tasks.analyze.is_legacy_split_path", lambda p: False)

        n = 0

        def _analyze(path, *a, **kw):
            nonlocal n
            n += 1
            if n >= 2:
                raise RuntimeError("boom")
            return {"title": "ok", "summary": "s", "timeline": [], "location": "X"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        import pytest

        with pytest.raises(RuntimeError, match="失败"):
            run_analyze_all(cfg)
        assert list(cfg.texts_dir.glob("*.json")) == []

    def test_window_summary_failure_writes_nothing(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        cfg.analyze.window_max_min = 15
        cfg.analyze.window_overlap_sec = 20
        _add_original(cfg, "GL010695.mp4")
        comp = cfg.compressed_dir / "001_GL010695.mp4"
        comp.write_bytes(b"\x00" * 100)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 2400.0)
        monkeypatch.setattr("clio.tasks.analyze.is_legacy_split_path", lambda p: False)

        monkeypatch.setattr(
            "clio.tasks.analyze.analyze_video",
            lambda *a, **kw: {"title": "partial", "summary": "partial", "timeline": []},
        )

        def fail_summary(*args, **kwargs):
            raise RuntimeError("summary failed")

        monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", fail_summary)

        import pytest

        with pytest.raises(RuntimeError, match="失败"):
            run_analyze_all(cfg)
        assert list(cfg.texts_dir.glob("*.json")) == []

    def test_hybrid_directory_prefers_canonical_whole(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        source = _add_original(cfg, "GL010695.mp4")
        segment = cfg.compressed_dir / "001_GL010695_seg01.mp4"
        whole = cfg.compressed_dir / "003_GL010695.mp4"
        segment.write_bytes(b"segment")
        whole.write_bytes(b"whole")
        VideoMeta.build(source, whole, 60.0, 60.0, split_info=None).write(whole)

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 60.0)
        analyzed_paths = []

        def fake_analyze(path, *args, **kwargs):
            analyzed_paths.append(Path(path).name)
            return {"title": "Whole", "summary": "s", "timeline": [], "location": "X"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg)

        assert len(records) == 1
        assert analyzed_paths == [whole.name]

    def test_window_cancel_is_not_reported_as_ai_failure(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        cfg.analyze.max_analyze_duration_min = 0
        source = _add_original(cfg, "GL010695.mp4")
        compressed = cfg.compressed_dir / "001_GL010695.mp4"
        compressed.write_bytes(b"video")
        VideoMeta.build(source, compressed, 2400.0, 2400.0, split_info=None).write(compressed)
        cancel_event = threading.Event()

        _common_mocks(monkeypatch)
        monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *args: 2400.0)

        calls = 0

        def fake_analyze(*args, **kwargs):
            nonlocal calls
            calls += 1
            cancel_event.set()
            return {"title": "W", "summary": "s", "timeline": [], "location": "X"}

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg, cancel_event=cancel_event)

        assert records == []
        assert calls == 1

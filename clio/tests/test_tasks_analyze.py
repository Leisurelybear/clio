"""Tests for clio/tasks/analyze.py — run_analyze_all."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from clio.tasks.analyze import run_analyze_all
from clio.vmeta import VideoMeta


def _cfg(tmp_path: Path) -> SimpleNamespace:
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
            max_workers=1,
        ),
        ai=SimpleNamespace(context=""),
        compress=SimpleNamespace(),
        plan=SimpleNamespace(max_clips_per_day=10, target_duration_sec=300),
        script=SimpleNamespace(target_words=150),
        project_dir=tmp_path / "project",
    )
    Path(cfg.project_dir).mkdir(parents=True, exist_ok=True)
    cfg.compressed_dir.mkdir(parents=True, exist_ok=True)
    cfg.texts_dir.mkdir(parents=True, exist_ok=True)
    return cfg


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
    # Prevent AI calls
    monkeypatch.setattr("clio.tasks.analyze._build_stem", lambda idx, title, cfg: f"{idx:03d}_{title}")
    monkeypatch.setattr("clio.tasks.analyze._write_text_file", lambda *a: None)


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
        assert records[0].analysis["title"] == "Fresh"

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
        assert records[0].compressed_path.name == "002_GL010684.mp4"

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
        assert records[0].compressed_path.name == "001_GL010695_seg01.mp4"
        assert records[1].compressed_path.name == "002_GL010695_seg02.mp4"

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

    def test_multi_window_merges_and_writes_one_json(self, monkeypatch, tmp_path: Path):
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

        def fake_slice(*, source, window, dest_dir, ffmpeg, ffprobe=None, run_ffmpeg=None, cancel_event=None):
            dest_dir.mkdir(parents=True, exist_ok=True)
            out = dest_dir / f"{source.stem}_w{window.index:02d}.mp4"
            out.write_bytes(b"x")
            return out

        monkeypatch.setattr("clio.tasks.analyze.slice_window_video", fake_slice)

        call_paths: list[str] = []

        def _analyze(path, *a, **kw):
            call_paths.append(str(path))
            return {
                "title": f"W{len(call_paths)}",
                "summary": f"sum{len(call_paths)}",
                "timeline": [{"start": 5, "end": 10, "text": f"e{len(call_paths)}"}],
                "location": "X",
            }

        monkeypatch.setattr("clio.tasks.analyze.analyze_video", _analyze)

        records = run_analyze_all(cfg)
        assert len(records) == 1
        assert len(call_paths) >= 3
        assert records[0].analysis is not None
        assert records[0].analysis["title"] == "W1"
        assert len(records[0].analysis.get("analyze_windows", [])) >= 3
        texts = list(cfg.texts_dir.glob("*.json"))
        assert len(texts) == 1
        data = json.loads(texts[0].read_text(encoding="utf-8"))
        assert data["timeline"][0]["start"] == 5
        assert any(t["start"] >= 880 for t in data["timeline"])

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

        def fake_slice(*, source, window, dest_dir, ffmpeg, ffprobe=None, run_ffmpeg=None, cancel_event=None):
            dest_dir.mkdir(parents=True, exist_ok=True)
            out = dest_dir / f"{source.stem}_w{window.index:02d}.mp4"
            out.write_bytes(b"x")
            return out

        monkeypatch.setattr("clio.tasks.analyze.slice_window_video", fake_slice)

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

        def fake_slice(*, source, window, dest_dir, ffmpeg, ffprobe=None, run_ffmpeg=None, cancel_event=None):
            dest_dir.mkdir(parents=True, exist_ok=True)
            out = dest_dir / f"{source.stem}_w{window.index:02d}.mp4"
            out.write_bytes(b"window")
            return out

        calls = 0

        def fake_analyze(*args, **kwargs):
            nonlocal calls
            calls += 1
            cancel_event.set()
            return {"title": "W", "summary": "s", "timeline": [], "location": "X"}

        monkeypatch.setattr("clio.tasks.analyze.slice_window_video", fake_slice)
        monkeypatch.setattr("clio.tasks.analyze.analyze_video", fake_analyze)

        records = run_analyze_all(cfg, cancel_event=cancel_event)

        assert records == []
        assert calls == 1

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from clio.analyze_windows import (
    WINDOW_CACHE_VERSION,
    AnalyzeWindow,
    load_window_cache,
    save_window_cache,
    window_cache_dir,
)
from clio.config import AppConfig
from clio.tasks.analyze import _analyze_with_optional_windows, run_analyze_all


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
            transcripts_dir=tmp_path / "output" / "transcripts",
            summary_csv=tmp_path / "output" / "summary.csv",
            naming=SimpleNamespace(index_width=3),
            analyze=SimpleNamespace(
                skip_existing=False,
                max_analyze_duration_min=0,
                window_max_min=15,
                window_overlap_sec=20,
                media_resolution="medium",
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
    cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr("clio.tasks.analyze.select_sparse_retry_gap", lambda *a, **kw: None)
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


def test_window_cache_version_is_current(tmp_path: Path) -> None:
    assert WINDOW_CACHE_VERSION == 3
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
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = WINDOW_CACHE_VERSION - 1
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_window_cache(cache, window=window, lineage="abc", source_size=123, source_mtime_ns=456) is None


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

    summarize_calls: list[dict] = []

    def fake_summary(analysis, *args, **kwargs):
        summarize_calls.append(analysis)
        return {**analysis, "title": "Whole", "summary": "Full", "location": "X"}

    monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", fake_summary)

    analyze_calls: list[tuple[str, float | None, float | None, float | None, str | None]] = []

    def fail_second(path, *args, **kwargs):
        analyze_calls.append(
            (
                str(path),
                kwargs["video_start_sec"],
                kwargs["video_end_sec"],
                kwargs["video_fps"],
                kwargs["media_resolution"],
            )
        )
        if len(analyze_calls) >= 2:
            raise RuntimeError("boom")
        return {"title": "W1", "summary": "s1", "timeline": [{"start": 5, "end": 10, "text": "e1"}], "location": "X"}

    monkeypatch.setattr("clio.tasks.analyze.analyze_video", fail_second)

    cache = window_cache_dir(cfg.paths.output_dir, compressed.stem)
    failed_run = run_analyze_all(cfg)
    assert failed_run == []
    assert failed_run.failed == 1
    assert len(list(cache.glob("*.json"))) == 1

    analyze_calls.clear()

    def succeed(path, *args, **kwargs):
        analyze_calls.append(
            (
                str(path),
                kwargs["video_start_sec"],
                kwargs["video_end_sec"],
                kwargs["video_fps"],
                kwargs["media_resolution"],
            )
        )
        return {
            "title": f"W{len(analyze_calls)}",
            "summary": "s",
            "timeline": [{"start": 5, "end": 10, "text": "e"}],
            "location": "X",
        }

    monkeypatch.setattr("clio.tasks.analyze.analyze_video", succeed)

    records = run_analyze_all(cfg)

    assert len(records) == 1
    assert analyze_calls == [
        (str(compressed), 880.0, 1780.0, 3.0, "medium"),
        (str(compressed), 1760.0, 2400.0, 3.0, "medium"),
    ]
    assert len(summarize_calls) == 1
    assert not list(cache.glob("*.json"))
    assert len(list(cfg.texts_dir.glob("*.json"))) == 1


def test_window_prompts_receive_only_relative_window_transcript(monkeypatch, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _add_original(cfg)
    compressed = cfg.compressed_dir / "001_GL010695.mp4"
    compressed.write_bytes(b"video")
    (cfg.transcripts_dir / "001_GL010695_transcript.json").write_text(
        json.dumps(
            {
                "source_stem": "GL010695",
                "segments": [
                    {"start": 10.0, "end": 20.0, "text": "first"},
                    {"start": 890.0, "end": 910.0, "text": "overlap"},
                    {"start": 1000.0, "end": 1005.0, "text": "second"},
                    {"start": 1700.0, "end": 1800.0, "text": "third"},
                    {"start": 2300.0, "end": 2350.0, "text": "fourth"},
                ],
            }
        ),
        encoding="utf-8",
    )

    _common_mocks(monkeypatch)
    monkeypatch.setattr("clio.tasks.analyze.get_duration_sec", lambda *a: 2400.0)
    monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", lambda analysis, *a, **kw: analysis)

    calls: list[tuple[float, float, list[dict]]] = []

    def capture(path, *args, **kwargs):
        context = json.loads(kwargs["transcript_context"])
        assert context["source_stem"] == "GL010695"
        calls.append((kwargs["video_start_sec"], kwargs["video_end_sec"], context["segments"]))
        return {"title": "W", "summary": "s", "timeline": [], "location": "X"}

    monkeypatch.setattr("clio.tasks.analyze.analyze_video", capture)

    records = run_analyze_all(cfg)

    assert len(records) == 1
    assert [(start, end, [(seg["start"], seg["end"]) for seg in segments]) for start, end, segments in calls] == [
        (0.0, 900.0, [(10.0, 20.0), (890.0, 900.0)]),
        (880.0, 1780.0, [(10.0, 30.0), (120.0, 125.0), (820.0, 900.0)]),
        (1760.0, 2400.0, [(0.0, 40.0), (540.0, 590.0)]),
    ]
    assert [[seg["text"] for seg in segments] for _, _, segments in calls] == [
        ["first", "overlap"],
        ["overlap", "second", "third"],
        ["third", "fourth"],
    ]


def test_sparse_retry_uses_only_relative_transcript_for_gap(monkeypatch, tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    compressed = cfg.compressed_dir / "001_GL010695.mp4"
    compressed.write_bytes(b"video")
    transcript = json.dumps(
        {
            "segments": [
                {"start": 100.0, "end": 200.0, "text": "covered"},
                {"start": 350.0, "end": 400.0, "text": "gap"},
            ]
        }
    )
    monkeypatch.setattr("clio.tasks.analyze.detect_scene_boundaries", lambda *a, **kw: [])

    calls: list[tuple[float, float, list[dict]]] = []

    def capture(path, *args, **kwargs):
        segments = json.loads(kwargs["transcript_context"])["segments"]
        calls.append((kwargs["video_start_sec"], kwargs["video_end_sec"], segments))
        if len(calls) == 1:
            return {"timeline": [{"start": 0, "end": 300, "text": "covered"}]}
        return {"timeline": [{"start": 10, "end": 20, "text": "retry"}]}

    monkeypatch.setattr("clio.tasks.analyze.analyze_video", capture)

    result = _analyze_with_optional_windows(
        compressed=compressed,
        duration_sec=600.0,
        config=cfg,
        token_store=None,
        cancel_event=None,
        effective_context=None,
        task_prompts=None,
        transcript_context=transcript,
        lineage="",
    )

    assert result["timeline"]
    assert [
        (start, end, [(seg["start"], seg["end"], seg["text"]) for seg in segments]) for start, end, segments in calls
    ] == [
        (0.0, 600.0, [(100.0, 200.0, "covered"), (350.0, 400.0, "gap")]),
        (300.0, 600.0, [(50.0, 100.0, "gap")]),
    ]


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
        "clio.tasks.analyze.analyze_video",
        lambda *a, **kw: {"title": "W", "summary": "s", "timeline": [], "location": "X"},
    )
    monkeypatch.setattr("clio.tasks.analyze.summarize_window_analysis", lambda analysis, *a, **kw: analysis)

    records = run_analyze_all(cfg)

    assert len(records) == 1
    assert not cache.exists()
    assert len(list(cfg.texts_dir.glob("*.json"))) == 1

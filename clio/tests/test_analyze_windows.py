"""Tests for clio/analyze_windows.py."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from clio.analyze_windows import (
    AnalyzeWindow,
    TimelineGap,
    build_analyze_windows,
    cleanup_analyze_windows_dir,
    measure_timeline_coverage,
    merge_timeline_completion,
    merge_window_analyses,
    select_sparse_retry_gap,
    shift_analysis_times,
    slice_transcript_context,
    slice_window_video,
    timeline_gaps,
)


class TestBuildAnalyzeWindows:
    def test_single_when_short(self):
        ws = build_analyze_windows(600, window_max_min=15, overlap_sec=20)
        assert len(ws) == 1
        assert ws[0].start_sec == 0
        assert ws[0].end_sec == 600

    def test_multi_with_overlap(self):
        ws = build_analyze_windows(2400, window_max_min=15, overlap_sec=20)
        assert len(ws) >= 3
        assert ws[0].start_sec == 0
        assert ws[0].end_sec == 900
        assert ws[1].start_sec == 900 - 20
        assert ws[-1].end_sec == 2400
        for a, b in pairwise(ws):
            assert b.start_sec < a.end_sec

    def test_excessive_window_count_fails_closed(self):
        with pytest.raises(ValueError, match="分析窗数量过多"):
            build_analyze_windows(1200, window_max_min=1, overlap_sec=59)


class TestTimelineCoverage:
    def test_merges_overlaps_and_reports_edge_gaps(self):
        coverage = measure_timeline_coverage(
            {
                "timeline": [
                    {"start": "00:10", "end": "00:30"},
                    {"start": "00:20", "end": "00:40"},
                    {"start": "01:20", "end": "01:30"},
                ]
            },
            100.0,
        )

        assert coverage.rows == 3
        assert coverage.coverage_ratio == pytest.approx(0.4)
        assert coverage.max_gap_sec == pytest.approx(40.0)

    def test_empty_timeline_reports_full_duration_gap(self):
        coverage = measure_timeline_coverage({"timeline": []}, 120.0)

        assert coverage.rows == 0
        assert coverage.coverage_ratio == 0.0
        assert coverage.max_gap_sec == 120.0

    def test_invalid_and_out_of_bounds_rows_are_excluded(self):
        coverage = measure_timeline_coverage(
            {
                "timeline": [
                    {"start": -5, "end": 10},
                    {"start": "bad", "end": 20},
                    {"start": 90, "end": 120},
                    {"start": 95, "end": 105},
                ]
            },
            100.0,
        )

        assert coverage.rows == 4
        assert coverage.coverage_ratio == pytest.approx(0.2)
        assert coverage.max_gap_sec == pytest.approx(80.0)

    @pytest.mark.parametrize("duration", [0.0, -1.0, float("nan")])
    def test_non_positive_or_invalid_duration_is_safe(self, duration):
        coverage = measure_timeline_coverage({"timeline": [{"start": 0, "end": 10}]}, duration)

        assert coverage.rows == 1
        assert coverage.coverage_ratio == 0.0
        assert coverage.max_gap_sec == 0.0

    def test_timeline_gaps_clip_edges_merge_overlaps_and_ignore_invalid_rows(self):
        analysis = {
            "timeline": [
                {"start": -10, "end": 10},
                {"start": 10, "end": 20},
                {"start": "00:00:30", "end": "00:00:50"},
                {"start": 40, "end": 45},
                {"start": "bad", "end": 60},
                {"start": 70, "end": 60},
                {"start": 80, "end": 200},
            ]
        }

        gaps = timeline_gaps(analysis, 0.0, 100.0)

        actual = [(gap.start_sec, gap.end_sec) for gap in gaps]
        assert len(actual) == 2
        assert actual[0] == pytest.approx((20.0, 30.0))
        assert actual[1] == pytest.approx((50.0, 80.0))

    @pytest.mark.parametrize(("start", "end"), [(100.0, 100.0), (200.0, 100.0), (float("nan"), 100.0)])
    def test_timeline_gaps_invalid_bounds_are_empty(self, start, end):
        assert timeline_gaps({"timeline": [{"start": 0, "end": 10}]}, start, end) == []


class TestSparseRetrySelection:
    def test_dense_timeline_has_no_retry(self):
        analysis = {"timeline": [{"start": 0, "end": 50}, {"start": 50, "end": 100}]}

        assert select_sparse_retry_gap(analysis, 0.0, 100.0) is None

    def test_small_gap_has_no_retry(self):
        analysis = {"timeline": [{"start": 0, "end": 75}]}

        assert select_sparse_retry_gap(analysis, 0.0, 100.0) is None

    def test_low_coverage_with_largest_gap_selects_that_gap(self):
        analysis = {"timeline": [{"start": 0, "end": 10}, {"start": 30, "end": 70}]}

        gap = select_sparse_retry_gap(analysis, 0.0, 100.0)

        assert gap == TimelineGap(70.0, 100.0)

    def test_large_gap_retries_even_when_overall_coverage_is_high(self):
        analysis = {"timeline": [{"start": 0, "end": 455}, {"start": 545, "end": 1000}]}

        gap = select_sparse_retry_gap(analysis, 0.0, 1000.0)

        assert gap == TimelineGap(455.0, 545.0)

    def test_below_threshold_coverage_chooses_largest_gap(self):
        analysis = {"timeline": [{"start": 0, "end": 50}, {"start": 130, "end": 150}]}

        gap = select_sparse_retry_gap(analysis, 0.0, 200.0)

        assert gap == TimelineGap(50.0, 130.0)

    def test_empty_timeline_retries_the_whole_window(self):
        gap = select_sparse_retry_gap({"timeline": []}, 0.0, 100.0)

        assert gap == TimelineGap(0.0, 100.0)


class TestSliceTranscriptContext:
    def test_clips_to_window_and_rebases_timestamps(self):
        transcript = json.dumps(
            {
                "source_stem": "GL010695",
                "segments": [
                    {"start": -10.0, "end": 870.0, "text": "before"},
                    {"start": 870.0, "end": 910.0, "text": "overlap", "avg_logprob": -0.2, "low_confidence": True},
                    {"start": 1000.0, "end": 1005.0, "text": "middle", "avg_logprob": -0.1},
                    {"start": 1700.0, "end": 1800.0, "text": "after"},
                ],
            }
        )

        sliced = json.loads(slice_transcript_context(transcript, 880.0, 1780.0))

        assert sliced["source_stem"] == "GL010695"
        assert sliced["segments"] == [
            {"start": 0.0, "end": 30.0, "text": "overlap", "avg_logprob": -0.2, "low_confidence": True},
            {"start": 120.0, "end": 125.0, "text": "middle", "avg_logprob": -0.1},
            {"start": 820.0, "end": 900.0, "text": "after"},
        ]

    def test_returns_empty_without_positive_intersection_or_valid_json(self):
        transcript = json.dumps({"segments": [{"start": 0.0, "end": 10.0, "text": "outside"}]})

        assert slice_transcript_context(transcript, 10.0, 20.0) == ""
        assert slice_transcript_context("{not json", 0.0, 20.0) == ""


class TestShiftAndMerge:
    def test_shift_timeline_numeric(self):
        raw: dict[str, Any] = {"title": "t", "summary": "s", "timeline": [{"start": 10, "end": 20, "text": "a"}]}
        out = shift_analysis_times(raw, 100)
        assert out["timeline"][0]["start"] == 110
        assert out["timeline"][0]["end"] == 120
        assert raw["timeline"][0]["start"] == 10

    @pytest.mark.parametrize(
        ("timestamp", "offset_sec", "expected"),
        [("00:00:01.250", 120, "00:02:01.250"), ("00:01.5", 60, "01:01.5")],
    )
    def test_shift_timestamp_string_preserves_fractional_seconds(self, timestamp, offset_sec, expected):
        raw = {"timeline": [{"start": timestamp}]}

        out = shift_analysis_times(raw, offset_sec)

        assert out["timeline"][0]["start"] == expected
        assert raw["timeline"][0]["start"] == timestamp

    def test_merge_prefers_title_window0_and_sorts_timeline(self):
        w0 = AnalyzeWindow(0, 0, 900)
        w1 = AnalyzeWindow(1, 880, 1800)
        a0 = {
            "title": "A",
            "summary": "s0",
            "timeline": [{"start": 10, "end": 20, "text": "early"}],
            "highlights": ["h1"],
            "location": "X",
        }
        a1 = {
            "title": "B",
            "summary": "s1",
            "timeline": [{"start": 900, "end": 910, "text": "late"}],
            "highlights": ["h1", "h2"],
            "location": "Y",
        }
        merged = merge_window_analyses([(w0, a0), (w1, a1)], overlap_sec=20)
        assert merged["title"] == "A"
        assert "s0" in merged["summary"] and "s1" in merged["summary"]
        assert len(merged["timeline"]) == 2
        assert merged["timeline"][0]["start"] <= merged["timeline"][1]["start"]
        assert set(merged["highlights"]) == {"h1", "h2"}
        assert len(merged["analyze_windows"]) == 2

    def test_merge_dedupes_overlap_near_duplicates(self):
        w0 = AnalyzeWindow(0, 0, 900)
        w1 = AnalyzeWindow(1, 880, 1800)
        a0 = {
            "title": "A",
            "summary": "s0",
            "timeline": [{"start": 890, "end": 900, "text": "dup"}],
        }
        a1 = {
            "title": "B",
            "summary": "s1",
            "timeline": [{"start": 892, "end": 902, "text": "dup"}],
        }
        merged = merge_window_analyses([(w0, a0), (w1, a1)], overlap_sec=20)
        assert sum(1 for t in merged["timeline"] if t.get("text") == "dup") == 1

    def test_merge_keeps_similar_events_outside_overlap(self):
        w0 = AnalyzeWindow(0, 0, 900)
        w1 = AnalyzeWindow(1, 880, 1800)
        a0 = {
            "title": "A",
            "summary": "s0",
            "timeline": [{"start": 120, "end": 125, "text": "过马路"}],
        }
        a1 = {
            "title": "B",
            "summary": "s1",
            "timeline": [{"start": 124, "end": 130, "text": "过马路"}],
        }
        merged = merge_window_analyses([(w0, a0), (w1, a1)], overlap_sec=20)
        assert sum(1 for t in merged["timeline"] if t.get("text") == "过马路") == 2

    def test_merge_dedupes_paraphrases_with_intervening_event(self):
        w0 = AnalyzeWindow(0, 0, 900)
        w1 = AnalyzeWindow(1, 880, 1800)
        a0 = {
            "title": "A",
            "summary": "s0",
            "timeline": [{"start": 890, "end": 900, "text": "海边悠闲散步"}],
        }
        a1 = {
            "title": "B",
            "summary": "s1",
            "timeline": [
                {"start": 891, "end": 895, "text": "路边车辆经过"},
                {"start": 892, "end": 902, "text": "在海边悠闲地散步"},
            ],
        }

        merged = merge_window_analyses([(w0, a0), (w1, a1)], overlap_sec=20)

        assert len(merged["timeline"]) == 2
        assert any(item["text"] == "在海边悠闲地散步" for item in merged["timeline"])

    def test_merge_combines_timeline_fields_and_key_moments(self):
        w0 = AnalyzeWindow(0, 0, 900)
        w1 = AnalyzeWindow(1, 880, 1800)
        a0 = {
            "title": "A",
            "summary": "s0",
            "timeline": [
                {
                    "start": 890,
                    "end": 900,
                    "description": "集市挑奶酪",
                    "score": 3,
                    "camera": "fixed",
                    "tags": ["food"],
                }
            ],
            "key_moments": [
                {
                    "start": 890,
                    "end": 900,
                    "title": "集市挑奶酪",
                    "why": "人物互动",
                    "score": 3,
                }
            ],
        }
        a1 = {
            "title": "B",
            "summary": "s1",
            "timeline": [
                {
                    "start": 890,
                    "end": 902,
                    "description": "集市挑奶酪并看招牌",
                    "score": 5,
                    "camera": "pan",
                    "quality": "ok",
                    "tags": ["market", "Food"],
                }
            ],
            "key_moments": [
                {
                    "start": 892,
                    "end": 902,
                    "title": "集市挑奶酪看招牌",
                    "why": "人物互动与招牌文字",
                    "score": 5,
                }
            ],
        }

        merged = merge_window_analyses([(w0, a0), (w1, a1)], overlap_sec=20)

        assert len(merged["timeline"]) == 1
        assert merged["timeline"][0]["score"] == 5
        assert merged["timeline"][0]["camera"] == "pan"
        assert merged["timeline"][0]["quality"] == "ok"
        assert merged["timeline"][0]["tags"] == ["market", "Food"]
        assert len(merged["key_moments"]) == 1
        assert merged["key_moments"][0]["score"] == 5

    def test_merge_timeline_completion_shifts_filters_dedupes_and_preserves_base(self):
        base: dict[str, Any] = {
            "title": "base",
            "summary": "base summary",
            "location": "未知",
            "highlights": ["base"],
            "timeline": [
                {"start": 0, "end": 10, "text": "before"},
                {"start": 120, "end": 125, "text": "same", "score": 5},
            ],
            "key_moments": [{"start": 0, "text": "base moment"}],
        }
        completion: dict[str, Any] = {
            "summary": "gap summary",
            "location": "Gap",
            "highlights": ["new", {"text": "moment"}],
            "timeline": [
                {"start": 20, "end": 25, "text": "same", "score": 4},
                {"start": 5, "end": 15, "text": "new"},
                {"start": 200, "end": 210, "text": "outside"},
            ],
            "key_moments": [{"start": 5, "end": 8, "text": "retry moment"}],
        }

        merged = merge_timeline_completion(base, completion, TimelineGap(100.0, 150.0))

        assert merged["title"] == "base"
        assert merged["summary"] == "base summary\ngap summary"
        assert merged["location"] == "Gap"
        assert merged["highlights"] == ["base", "new", {"text": "moment"}]
        assert [(item["start"], item["text"]) for item in merged["timeline"]] == [
            (0, "before"),
            (105, "new"),
            (120, "same"),
        ]
        assert merged["timeline"][2]["score"] == 5
        assert [(item["start"], item["text"]) for item in merged["key_moments"]] == [
            (0, "base moment"),
            (105, "retry moment"),
        ]
        assert base["timeline"][0]["text"] == "before"


class TestSliceWindowVideo:
    def test_slice_window_video_invokes_ffmpeg(self, tmp_path: Path):
        src = tmp_path / "001_GL.mp4"
        src.write_bytes(b"\x00" * 10)
        dest = tmp_path / ".analyze_windows"
        calls = []

        def fake_run(args, ffmpeg, **kw):
            calls.append(args)
            Path(args[-1]).write_bytes(b"x")

        out = slice_window_video(
            source=src,
            window=AnalyzeWindow(0, 0, 60),
            dest_dir=dest,
            ffmpeg="ffmpeg",
            max_bytes=10 * 1024 * 1024,
            run_ffmpeg=fake_run,
        )
        assert out.is_file()
        assert "w00" in out.name
        assert "-ss" in calls[0]
        assert "-c" in calls[0]

    def test_slice_falls_back_to_reencode_on_copy_fail(self, tmp_path: Path):
        src = tmp_path / "001_GL.mp4"
        src.write_bytes(b"\x00" * 10)
        dest = tmp_path / ".analyze_windows"
        calls = []

        def fake_run(args, ffmpeg, **kw):
            calls.append(list(args))
            if "-c" in args and args[args.index("-c") + 1] == "copy":
                raise RuntimeError("copy failed")
            Path(args[-1]).write_bytes(b"reencoded")

        out = slice_window_video(
            source=src,
            window=AnalyzeWindow(1, 100, 160),
            dest_dir=dest,
            ffmpeg="ffmpeg",
            max_bytes=10 * 1024 * 1024,
            run_ffmpeg=fake_run,
        )
        assert out.is_file()
        assert len(calls) == 2
        assert "libx264" in calls[1]
        assert "-an" not in calls[1]
        assert calls[1][calls[1].index("-c:a") + 1] == "aac"
        assert calls[1][calls[1].index("-ac") + 1] == "1"

    def test_slice_reencodes_when_copy_duration_drifts(self, monkeypatch, tmp_path: Path):
        src = tmp_path / "001_GL.mp4"
        src.write_bytes(b"source")
        dest = tmp_path / ".analyze_windows"
        calls = []
        durations = iter([62.0, 60.0])

        def fake_run(args, ffmpeg, **kw):
            calls.append(args)
            Path(args[-1]).write_bytes(b"video")

        monkeypatch.setattr("clio.analyze_windows.get_duration_sec", lambda *args: next(durations))

        out = slice_window_video(
            source=src,
            window=AnalyzeWindow(0, 0, 60),
            dest_dir=dest,
            ffmpeg="ffmpeg",
            max_bytes=10 * 1024 * 1024,
            ffprobe="ffprobe",
            run_ffmpeg=fake_run,
        )

        assert out.is_file()
        assert len(calls) == 2
        assert "libx264" in calls[1]

    def test_slice_cancel_during_copy_does_not_reencode(self, tmp_path: Path):
        """InterruptedError on stream-copy must not fall through to re-encode (C1)."""
        src = tmp_path / "001_GL.mp4"
        src.write_bytes(b"\x00" * 10)
        dest = tmp_path / ".analyze_windows"
        calls: list[list] = []

        def fake_run(args, ffmpeg, **kw):
            calls.append(list(args))
            Path(args[-1]).write_bytes(b"partial")
            if "-c" in args and args[args.index("-c") + 1] == "copy":
                raise InterruptedError("ffmpeg 被用户取消")
            # If re-encode is wrongly attempted, leave a marker file content.
            Path(args[-1]).write_bytes(b"reencoded-after-cancel")

        with pytest.raises(InterruptedError, match="用户取消"):
            slice_window_video(
                source=src,
                window=AnalyzeWindow(0, 0, 60),
                dest_dir=dest,
                ffmpeg="ffmpeg",
                max_bytes=10 * 1024 * 1024,
                run_ffmpeg=fake_run,
            )

        assert len(calls) == 1, f"expected only copy call, got {len(calls)}: {calls}"
        assert not any(dest.glob("*.mp4")), "partial slice must be unlinked on cancel"

    def test_slice_shrinks_when_over_custom_cap(self, tmp_path: Path):
        src = tmp_path / "001_GL.mp4"
        src.write_bytes(b"\x00" * 10)
        dest = tmp_path / ".analyze_windows"
        calls = []

        def fake_run(args, ffmpeg, **kw):
            calls.append(list(args))
            outp = Path(args[-1])
            # Stay oversized until shrink path (has -vf scale)
            joined = " ".join(str(a) for a in args)
            if "-vf" in args or "scale=" in joined:
                outp.write_bytes(b"small")
            else:
                outp.write_bytes(b"x" * 2048)

        out = slice_window_video(
            source=src,
            window=AnalyzeWindow(0, 0, 60),
            dest_dir=dest,
            ffmpeg="ffmpeg",
            max_bytes=1024,
            run_ffmpeg=fake_run,
        )
        assert out.is_file()
        assert out.stat().st_size < 1024
        assert any("-vf" in c for c in calls)
        shrink = next(c for c in calls if "-vf" in c)
        assert "-an" not in shrink
        assert shrink[shrink.index("-c:a") + 1] == "aac"
        assert shrink[shrink.index("-ac") + 1] == "1"

    def test_cleanup_removes_window_files(self, tmp_path: Path):
        dest = tmp_path / ".analyze_windows"
        dest.mkdir()
        f = dest / "001_GL_w00_0-60.mp4"
        f.write_bytes(b"x")
        other = dest / "keep.txt"
        other.write_text("y", encoding="utf-8")
        cleanup_analyze_windows_dir(dest)
        assert not f.exists()
        assert other.exists()

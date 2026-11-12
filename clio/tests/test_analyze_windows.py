"""Tests for clio/analyze_windows.py."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest

from clio.analyze_windows import (
    AnalyzeWindow,
    build_analyze_windows,
    cleanup_analyze_windows_dir,
    measure_timeline_coverage,
    merge_window_analyses,
    shift_analysis_times,
    slice_window_video,
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
            run_ffmpeg=fake_run,
        )
        assert out.is_file()
        assert len(calls) == 2
        assert "libx264" in calls[1]

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
                run_ffmpeg=fake_run,
            )

        assert len(calls) == 1, f"expected only copy call, got {len(calls)}: {calls}"
        assert not any(dest.glob("*.mp4")), "partial slice must be unlinked on cancel"

    def test_slice_shrinks_when_over_200mb(self, tmp_path: Path):
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
                outp.write_bytes(b"x" * (201 * 1024 * 1024))

        out = slice_window_video(
            source=src,
            window=AnalyzeWindow(0, 0, 60),
            dest_dir=dest,
            ffmpeg="ffmpeg",
            run_ffmpeg=fake_run,
        )
        assert out.is_file()
        assert out.stat().st_size < 200 * 1024 * 1024
        assert any("-vf" in c for c in calls)

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

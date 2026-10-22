"""Tests for clio/cut.py — time parsing and cutting logic."""

from __future__ import annotations

from unittest import mock

import pytest

from clio.cut import _to_seconds, cut_one, parse_time_range

# ── _to_seconds ─────────────────────────────────────────────────────


class TestToSeconds:
    def test_empty_string(self):
        assert _to_seconds("") == 0.0

    def test_just_seconds(self):
        assert _to_seconds("30") == 30.0

    def test_just_seconds_float(self):
        assert _to_seconds("30.5") == 30.5

    @pytest.mark.parametrize(
        "input,expected",
        [
            ("00:00", 0.0),
            ("00:15", 15.0),
            ("01:00", 60.0),
            ("01:30", 90.0),
            ("10:00", 600.0),
        ],
    )
    def test_mm_ss(self, input, expected):
        assert _to_seconds(input) == expected

    @pytest.mark.parametrize(
        "input,expected",
        [
            ("00:00:00", 0.0),
            ("00:01:30", 90.0),
            ("01:00:00", 3600.0),
            ("01:02:03", 3723.0),
            ("10:00:00", 36000.0),
        ],
    )
    def test_hh_mm_ss(self, input, expected):
        assert _to_seconds(input) == expected

    def test_strips_whitespace(self):
        assert _to_seconds("  01:30  ") == 90.0

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="无法解析"):
            _to_seconds("not-a-time")

    @pytest.mark.parametrize("value", ["00:70", "01:60", "00:00:60", "-1", "inf", "nan", "1:2:3:4"])
    def test_rejects_out_of_range_or_non_finite_components(self, value):
        with pytest.raises(ValueError, match="无法解析"):
            _to_seconds(value)


# ── parse_time_range ────────────────────────────────────────────────


class TestParseTimeRange:
    def test_basic(self):
        assert parse_time_range("00:00-00:20") == (0.0, 20.0)

    def test_with_spaces(self):
        assert parse_time_range("  01:00 - 02:30  ") == (60.0, 150.0)

    def test_mm_ss_to_hh_mm_ss(self):
        assert parse_time_range("00:00-01:00:00") == (0.0, 3600.0)

    def test_seconds_notation(self):
        assert parse_time_range("10-30") == (10.0, 30.0)

    def test_no_separator_raises(self):
        with pytest.raises(ValueError, match="无法解析"):
            parse_time_range("invalid")

    def test_single_value_raises(self):
        with pytest.raises(ValueError, match="无法解析"):
            parse_time_range("00:00")

    def test_three_parts_raises(self):
        with pytest.raises(ValueError, match="无法解析"):
            parse_time_range("00:00-00:00-00:00")


# ── cut_one ─────────────────────────────────────────────────────


class TestCutOne:
    def test_cut_one_uses_t_duration(self, tmp_path):
        src = tmp_path / "input.mp4"
        src.write_text("fake")
        out = tmp_path / "output.mp4"
        with mock.patch("clio.cut.run_ffmpeg") as mock_run:
            cut_one(src, out, 10.0, 30.0, "ffmpeg", reencode=False)
        args = mock_run.call_args[0][0]
        assert "-t" in args
        assert "-to" not in args
        assert str(20.0) in args

    def test_cut_one_reencode(self, tmp_path):
        src = tmp_path / "input.mp4"
        src.write_text("fake")
        out = tmp_path / "output.mp4"
        with mock.patch("clio.cut.run_ffmpeg") as mock_run:
            cut_one(src, out, 0, 10, "ffmpeg", reencode=True)
        args = mock_run.call_args[0][0]
        assert "-c:v" in args
        assert "libx264" in args

    def test_cut_one_stream_copy(self, tmp_path):
        src = tmp_path / "input.mp4"
        src.write_text("fake")
        out = tmp_path / "output.mp4"
        with mock.patch("clio.cut.run_ffmpeg") as mock_run:
            cut_one(src, out, 5, 15, "ffmpeg", reencode=False)
        args = mock_run.call_args[0][0]
        assert "-c" in args
        assert "copy" in args

    def test_cut_one_rejects_negative_duration(self, tmp_path):
        src = tmp_path / "input.mp4"
        src.write_text("fake")
        out = tmp_path / "output.mp4"
        with pytest.raises(ValueError, match="无效裁剪范围"):
            cut_one(src, out, 30, 10, "ffmpeg", reencode=False)

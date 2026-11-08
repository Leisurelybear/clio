"""Tests for clio/tasks/cover.py - cover frame extraction validation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clio.tasks.cover import _normalize_timestamp, extract_cover_frame

ANALYSIS = {"cover_timestamp": "00:08"}


def _cfg(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(paths=SimpleNamespace(output_dir=tmp_path / "output", ffmpeg="ffmpeg"))


def _video(tmp_path: Path, name: str = "clip.mp4") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x00" * 64)
    return path


def _run_ffmpeg_writing(payload: bytes | None, seen: list[str] | None = None):
    def fake_ffmpeg(cmd, ffmpeg):
        if seen is not None:
            seen.extend(cmd)
        if payload is not None:
            Path(cmd[-1]).write_bytes(payload)

    return fake_ffmpeg


class TestNormalizeTimestamp:
    def test_minutes_seconds(self):
        assert _normalize_timestamp("1:05") == "00:01:05.000"

    def test_hours_minutes_seconds(self):
        assert _normalize_timestamp("01:02:03.5") == "01:02:03.500"

    def test_rejects_invalid(self):
        assert _normalize_timestamp("") is None
        assert _normalize_timestamp("nope") is None
        assert _normalize_timestamp("-1:05") is None


class TestExtractCoverFrame:
    def test_missing_timestamp_skips(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        video = _video(tmp_path)

        with patch("clio.tasks.cover.run_ffmpeg") as run:
            assert extract_cover_frame(cfg, video, {}, "001_clip") is None

        run.assert_not_called()

    def test_creates_cover(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        video = _video(tmp_path)

        with (
            patch("clio.tasks.cover.resolve_binary", return_value="ffmpeg"),
            patch("clio.tasks.cover.run_ffmpeg", side_effect=_run_ffmpeg_writing(b"jpeg-bytes")),
        ):
            result = extract_cover_frame(cfg, video, ANALYSIS, "001_clip")

        assert result == cfg.paths.output_dir / "covers" / "001_clip.jpg"
        assert result.read_bytes() == b"jpeg-bytes"

    def test_missing_output_reports_failure(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        video = _video(tmp_path)

        with (
            patch("clio.tasks.cover.resolve_binary", return_value="ffmpeg"),
            patch("clio.tasks.cover.run_ffmpeg", side_effect=_run_ffmpeg_writing(None)),
        ):
            assert extract_cover_frame(cfg, video, ANALYSIS, "001_clip") is None

    def test_empty_output_is_removed(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        video = _video(tmp_path)

        with (
            patch("clio.tasks.cover.resolve_binary", return_value="ffmpeg"),
            patch("clio.tasks.cover.run_ffmpeg", side_effect=_run_ffmpeg_writing(b"")),
        ):
            assert extract_cover_frame(cfg, video, ANALYSIS, "001_clip") is None

        assert not (cfg.paths.output_dir / "covers" / "001_clip.jpg").exists()

    def test_stale_cover_is_never_returned(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        covers = cfg.paths.output_dir / "covers"
        covers.mkdir(parents=True)
        stale = covers / "001_clip.jpg"
        stale.write_bytes(b"old")
        video = _video(tmp_path)

        with (
            patch("clio.tasks.cover.resolve_binary", return_value="ffmpeg"),
            patch("clio.tasks.cover.run_ffmpeg", side_effect=_run_ffmpeg_writing(None)),
        ):
            assert extract_cover_frame(cfg, video, ANALYSIS, "001_clip") is None

        assert not stale.exists()

    def test_failure_removes_partial_output(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        video = _video(tmp_path)

        def fake_ffmpeg(cmd, ffmpeg):
            Path(cmd[-1]).write_bytes(b"partial")
            raise RuntimeError("ffmpeg exploded")

        with (
            patch("clio.tasks.cover.resolve_binary", return_value="ffmpeg"),
            patch("clio.tasks.cover.run_ffmpeg", side_effect=fake_ffmpeg),
        ):
            assert extract_cover_frame(cfg, video, ANALYSIS, "001_clip") is None

        assert not (cfg.paths.output_dir / "covers" / "001_clip.jpg").exists()

    def test_uses_fallback_when_preferred_source_missing(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        missing = tmp_path / "original.mp4"
        fallback = _video(tmp_path, "compressed.mp4")
        seen: list[str] = []

        with (
            patch("clio.tasks.cover.resolve_binary", return_value="ffmpeg"),
            patch(
                "clio.tasks.cover.run_ffmpeg",
                side_effect=_run_ffmpeg_writing(b"jpeg-bytes", seen),
            ),
        ):
            result = extract_cover_frame(cfg, missing, ANALYSIS, "001_clip", fallback_path=fallback)

        assert result is not None
        assert str(fallback) in seen
        assert str(missing) not in seen

    def test_missing_preferred_and_fallback_skips(self, tmp_path: Path):
        cfg = _cfg(tmp_path)
        missing = tmp_path / "original.mp4"
        also_missing = tmp_path / "compressed.mp4"

        with patch("clio.tasks.cover.run_ffmpeg") as run:
            assert extract_cover_frame(cfg, missing, ANALYSIS, "001_clip", fallback_path=also_missing) is None

        run.assert_not_called()

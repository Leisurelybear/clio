"""Tests for clio/compress.py — compress_video."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from clio.compress import compress_video


def _default_config() -> SimpleNamespace:
    return SimpleNamespace(
        paths=SimpleNamespace(ffmpeg="ffmpeg", ffprobe="ffprobe"),
        compress=SimpleNamespace(
            target_size_mb=5,
            max_width=640,
            fps=15,
            codec="libx264",
            remove_audio=True,
            crf=23,
        ),
    )


def _mock_ffmpeg(ffmpeg_calls: list) -> callable:
    """Create a mock run_ffmpeg that records calls and creates the output file."""

    def _run(args, ff, progress_callback=None, **kwargs):
        ffmpeg_calls.append(args)
        # Create the output file (last arg after -y) so stat() works
        if "-y" in args:
            out_path = Path(args[-1])
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"\x00" * 50_000)

    return _run


class TestCompressVideo:
    def test_zero_duration_raises(self, monkeypatch, tmp_path: Path):
        src = tmp_path / "input.mp4"
        src.write_bytes(b"")
        out = tmp_path / "out.mp4"
        monkeypatch.setattr("clio.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.compress.get_duration_sec", lambda *a: 0.0)

        with pytest.raises(ValueError, match="无法读取视频时长"):
            compress_video(src, out, _default_config())

    def test_negative_duration_raises(self, monkeypatch, tmp_path: Path):
        src = tmp_path / "input.mp4"
        src.write_bytes(b"")
        out = tmp_path / "out.mp4"
        monkeypatch.setattr("clio.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.compress.get_duration_sec", lambda *a: -1.0)

        with pytest.raises(ValueError, match="无法读取视频时长"):
            compress_video(src, out, _default_config())

    def _run_compress(self, monkeypatch, tmp_path, **cfg_overrides):
        src = tmp_path / "input.mp4"
        src.write_bytes(b"\x00" * 100_000)
        out = tmp_path / "out.mp4"
        monkeypatch.setattr("clio.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.compress.get_duration_sec", lambda *a: 60.0)
        ffmpeg_calls = []
        monkeypatch.setattr("clio.compress.run_ffmpeg", _mock_ffmpeg(ffmpeg_calls))
        cfg = _default_config()
        for k, v in cfg_overrides.items():
            setattr(cfg.compress, k, v)
        result = compress_video(src, out, cfg)
        return result, out, ffmpeg_calls

    def test_basic_compress_no_audio(self, monkeypatch, tmp_path: Path):
        result, out, calls = self._run_compress(monkeypatch, tmp_path)
        assert result == out
        assert len(calls) == 1
        args = calls[0]
        vf_idx = args.index("-vf")
        assert "scale" in args[vf_idx + 1]
        assert "fps=15" in args[vf_idx + 1]
        assert "-an" in args
        assert "-b:v" in args

    def test_compress_with_audio(self, monkeypatch, tmp_path: Path):
        result, out, calls = self._run_compress(monkeypatch, tmp_path, remove_audio=False)
        assert result == out
        args = calls[0]
        assert "-an" not in args
        assert "-b:v" in args

    def test_crf_mode(self, monkeypatch, tmp_path: Path):
        result, out, calls = self._run_compress(monkeypatch, tmp_path, target_size_mb=0)
        assert result == out
        args = calls[0]
        assert "-b:v" not in args
        crf_idx = args.index("-crf")
        assert args[crf_idx + 1] == "23"

    def test_bitrate_floor(self, monkeypatch, tmp_path: Path):
        src = tmp_path / "input.mp4"
        src.write_bytes(b"\x00" * 100_000)
        out = tmp_path / "out.mp4"
        monkeypatch.setattr("clio.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.compress.get_duration_sec", lambda *a: 1.0)
        ffmpeg_calls = []
        monkeypatch.setattr("clio.compress.run_ffmpeg", _mock_ffmpeg(ffmpeg_calls))
        cfg = _default_config()
        cfg.compress.target_size_mb = 1
        compress_video(src, out, cfg)
        args = ffmpeg_calls[0]
        bv_idx = args.index("-b:v")
        assert int(args[bv_idx + 1]) >= 100_000

    def test_cancel_during_compress(self, monkeypatch, tmp_path: Path):
        """cancel_event 被设置时 run_ffmpeg 应抛出 InterruptedError"""
        from threading import Event

        src = tmp_path / "input.mp4"
        src.write_bytes(b"\x00" * 100_000)
        out = tmp_path / "out.mp4"
        monkeypatch.setattr("clio.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.compress.get_duration_sec", lambda *a: 60.0)
        cancel_event = Event()
        cancel_event.set()

        def _raise_on_cancel(args, ffmpeg, progress_callback=None, cancel_event=None, **kw):
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("ffmpeg 被用户取消")
            # Should not reach here

        monkeypatch.setattr("clio.compress.run_ffmpeg", _raise_on_cancel)
        with pytest.raises(InterruptedError, match="ffmpeg 被用户取消"):
            compress_video(src, out, _default_config(), cancel_event=cancel_event)

    def test_failed_encode_preserves_existing_target(self, monkeypatch, tmp_path: Path):
        """A failed encode must not destroy the last valid compressed output."""
        src = tmp_path / "input.mp4"
        src.write_bytes(b"\x00" * 100_000)
        out = tmp_path / "out.mp4"
        out.write_bytes(b"previous-good")
        monkeypatch.setattr("clio.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.compress.get_duration_sec", lambda *a: 60.0)

        def _boom(args, ffmpeg, progress_callback=None, **kw):
            raise RuntimeError("ffmpeg 超时 fork 失败")

        monkeypatch.setattr("clio.compress.run_ffmpeg", _boom)
        with pytest.raises(RuntimeError, match="超时"):
            compress_video(src, out, _default_config())

        assert out.read_bytes() == b"previous-good"
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".part")]
        assert leftovers == []

    def test_cancel_leaves_no_part_temp(self, monkeypatch, tmp_path: Path):
        """Interrupted encode must clean up its temp file and keep target absent."""
        from threading import Event

        src = tmp_path / "input.mp4"
        src.write_bytes(b"\x00" * 100_000)
        out = tmp_path / "out.mp4"
        monkeypatch.setattr("clio.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.compress.get_duration_sec", lambda *a: 60.0)
        cancel_event = Event()
        cancel_event.set()

        def _raise_on_cancel(args, ffmpeg, progress_callback=None, cancel_event=None, **kw):
            raise InterruptedError("cancelled")

        monkeypatch.setattr("clio.compress.run_ffmpeg", _raise_on_cancel)
        with pytest.raises(InterruptedError):
            compress_video(src, out, _default_config(), cancel_event=cancel_event)

        assert not out.exists()
        assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".part")] == []

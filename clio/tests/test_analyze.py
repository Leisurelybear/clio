"""Tests for clio/tasks/analyze.py — pure logic only."""

from __future__ import annotations

from pathlib import Path

from clio.tasks._helpers import _path_key
from clio.tasks.analyze import _resolve_original
from clio.vmeta import VideoMeta


class TestResolveOriginal:
    def test_direct_match_mp4(self, tmp_path: Path):
        video = tmp_path / "GL010683.mp4"
        video.write_bytes(b"")
        result = _resolve_original("001_GL010683", stem_cache={"gl010683": video})
        assert result == video

    def test_direct_match_mov(self, tmp_path: Path):
        video = tmp_path / "GL010683.mov"
        video.write_bytes(b"")
        result = _resolve_original("001_GL010683", stem_cache={"gl010683": video})
        assert result == video

    def test_direct_match_mkv(self, tmp_path: Path):
        video = tmp_path / "GL010683.mkv"
        video.write_bytes(b"")
        result = _resolve_original("001_GL010683", stem_cache={"gl010683": video})
        assert result == video

    def test_direct_match_mts(self, tmp_path: Path):
        video = tmp_path / "GL010683.mts"
        video.write_bytes(b"")
        result = _resolve_original("001_GL010683", stem_cache={"gl010683": video})
        assert result == video

    def test_direct_match_m2ts(self, tmp_path: Path):
        video = tmp_path / "GL010683.m2ts"
        video.write_bytes(b"")
        result = _resolve_original("001_GL010683", stem_cache={"gl010683": video})
        assert result == video

    def test_no_match(self, tmp_path: Path):
        result = _resolve_original("001_NOFILE", stem_cache={})
        assert result is None

    def test_segment_match(self, tmp_path: Path):
        video = tmp_path / "GL010683.mp4"
        video.write_bytes(b"")
        result = _resolve_original("001_GL010683_seg01", stem_cache={"gl010683": video})
        assert result == video

    def test_segment_match_mov(self, tmp_path: Path):
        video = tmp_path / "GL010683.mov"
        video.write_bytes(b"")
        result = _resolve_original("001_GL010683_seg02", stem_cache={"gl010683": video})
        assert result == video

    def test_segment_no_original(self, tmp_path: Path):
        result = _resolve_original("001_GL010683_seg01", stem_cache={})
        assert result is None

    def test_empty_dir(self, tmp_path: Path):
        result = _resolve_original("001_GL010683", stem_cache={})
        assert result is None

    def test_sidecar_source_outside_selection_is_rejected(self, tmp_path: Path):
        selected = tmp_path / "a" / "clip.mp4"
        other = tmp_path / "b" / "clip.mp4"
        selected.parent.mkdir()
        other.parent.mkdir()
        selected.write_bytes(b"a")
        other.write_bytes(b"b")
        compressed = tmp_path / "001_clip.mp4"
        compressed.write_bytes(b"c")
        VideoMeta.build(other, compressed, 10.0, 10.0).write(compressed)

        result = _resolve_original(
            compressed.stem,
            stem_cache={"clip": selected},
            compressed_path=compressed,
            selected_paths=frozenset({_path_key(selected)}),
        )

        assert result is None

    def test_sidecar_source_in_selection_wins_over_duplicate_stem(self, tmp_path: Path):
        selected = tmp_path / "a" / "clip.mp4"
        other = tmp_path / "b" / "clip.mp4"
        selected.parent.mkdir()
        other.parent.mkdir()
        selected.write_bytes(b"a")
        other.write_bytes(b"b")
        compressed = tmp_path / "001_clip.mp4"
        compressed.write_bytes(b"c")
        VideoMeta.build(selected, compressed, 10.0, 10.0).write(compressed)

        result = _resolve_original(
            compressed.stem,
            stem_cache={"clip": None},
            compressed_path=compressed,
            selected_paths=frozenset({_path_key(selected)}),
        )

        assert result == selected.resolve()

    def test_stale_sidecar_falls_back_to_unique_stem_cache(self, tmp_path: Path):
        selected = tmp_path / "new" / "clip.mp4"
        selected.parent.mkdir()
        selected.write_bytes(b"new")
        old = tmp_path / "old" / "clip.mp4"
        old.parent.mkdir()
        old.write_bytes(b"old")
        compressed = tmp_path / "001_clip.mp4"
        compressed.write_bytes(b"c")
        VideoMeta.build(old, compressed, 10.0, 10.0).write(compressed)
        old.unlink()

        result = _resolve_original(
            compressed.stem,
            stem_cache={"clip": selected},
            compressed_path=compressed,
            selected_paths=frozenset({_path_key(selected)}),
        )

        assert result == selected

    def test_duplicate_stem_cache_resolves_to_none(self, tmp_path: Path):
        result = _resolve_original("001_clip", stem_cache={"clip": None})
        assert result is None

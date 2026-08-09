"""Tests for clio/identity.py — MediaIdentity, resolve_identity, load_identity."""

from __future__ import annotations

from pathlib import Path

from clio.identity import (
    MediaIdentity,
    _extract_original_stem,
    _identity_to_dict,
    is_legacy_split_identity,
    is_legacy_split_path,
    is_legacy_split_stem,
    legacy_segment_offset_sec,
    load_identity,
    prefer_canonical_compressed,
    resolve_identity,
)
from clio.vmeta import SegmentEntry, SplitInfo, VideoIndex, VideoMeta


class TestExtractOriginalStem:
    def test_simple_compressed(self):
        assert _extract_original_stem("001_GL010683") == "GL010683"

    def test_with_seg_suffix(self):
        assert _extract_original_stem("001_GL010683_seg01") == "GL010683"

    def test_with_part_alias(self):
        assert _extract_original_stem("001_GL010683_part02") == "GL010683"

    def test_no_prefix(self):
        assert _extract_original_stem("GL010683") == "GL010683"

    def test_seg_no_prefix(self):
        assert _extract_original_stem("GL010683_seg01") == "GL010683"


class TestLoadIdentity:
    def test_v2_identity(self):
        data = {
            "media_identity": {
                "original_stem": "GL010683",
                "original_path": "/videos/GL010683.mp4",
                "compressed_stem": "001_GL010683",
                "compressed_path": "/compressed/001_GL010683.mp4",
                "index": "001",
                "segment_index": None,
                "segment_offset_sec": 0.0,
                "segment_duration_sec": None,
            }
        }
        identity = load_identity(data)
        assert identity is not None
        assert identity.original_stem == "GL010683"
        assert identity.original_path == "/videos/GL010683.mp4"
        assert identity.compressed_stem == "001_GL010683"
        assert identity.compressed_path == "/compressed/001_GL010683.mp4"
        assert identity.index == "001"
        assert identity.segment_index is None
        assert identity.segment_offset_sec == 0.0
        assert identity.segment_duration_sec is None

    def test_v2_with_segment(self):
        data = {
            "media_identity": {
                "original_stem": "GL010683",
                "original_path": "/videos/GL010683.mp4",
                "compressed_stem": "001_GL010683_seg01",
                "compressed_path": "/compressed/001_GL010683_seg01.mp4",
                "index": "001",
                "segment_index": 1,
                "segment_offset_sec": 0.0,
                "segment_duration_sec": 40.0,
            }
        }
        identity = load_identity(data)
        assert identity is not None
        assert identity.segment_index == 1
        assert identity.segment_offset_sec == 0.0
        assert identity.segment_duration_sec == 40.0

    def test_v1_no_identity(self):
        data = {"version": 1, "content": "some text"}
        assert load_identity(data) is None

    def test_empty_dict(self):
        assert load_identity({}) is None

    def test_corrupted_identity(self):
        data = {
            "media_identity": {
                "original_stem": "GL010683",
            }
        }
        assert load_identity(data) is None


class TestResolveIdentityWithVmeta:
    def test_non_split_vmeta(self, tmp_path: Path):
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        compressed = tmp_path / "001_GL010683.mp4"
        compressed.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(
            source=src,
            target=compressed,
            source_duration=120.0,
            target_duration=120.0,
        )
        meta.write(compressed)

        identity = resolve_identity(compressed, tmp_path, "001")
        assert identity.original_stem == "GL010683"
        assert identity.original_path == str(src.resolve())
        assert identity.compressed_stem == "001_GL010683"
        assert identity.compressed_path == str(compressed.resolve())
        assert identity.index == "001"
        assert identity.segment_index is None
        assert identity.segment_offset_sec == 0.0
        assert identity.segment_duration_sec is None

    def test_split_vmeta(self, tmp_path: Path):
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        compressed = tmp_path / "001_GL010683_seg01.mp4"
        compressed.write_bytes(b"\x00" * 500)
        si = SplitInfo(
            original_stem="GL010683",
            segment_index=1,
            total_segments=3,
            offset_sec=0.0,
            segment_duration_sec=40.0,
        )
        meta = VideoMeta.build(
            source=src,
            target=compressed,
            source_duration=120.0,
            target_duration=40.0,
            split_info=si,
        )
        meta.write(compressed)

        identity = resolve_identity(compressed, tmp_path, "001")
        assert identity.original_stem == "GL010683"
        assert identity.segment_index == 1
        assert identity.segment_offset_sec == 0.0
        assert identity.segment_duration_sec == 40.0


class TestResolveIdentityFallback:
    def test_filename_only(self, tmp_path: Path):
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        compressed = tmp_path / "001_GL010683.mp4"
        compressed.write_bytes(b"\x00" * 500)

        identity = resolve_identity(compressed, tmp_path, "001")
        assert identity.original_stem == "GL010683"
        assert identity.original_path == str(src.resolve())
        assert identity.compressed_stem == "001_GL010683"
        assert identity.index == "001"
        assert identity.segment_index is None

    def test_segmented_filename_no_vmeta(self, tmp_path: Path):
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        compressed = tmp_path / "001_GL010683_seg02.mp4"
        compressed.write_bytes(b"\x00" * 500)

        identity = resolve_identity(compressed, tmp_path, "001")
        assert identity.original_stem == "GL010683"
        assert identity.segment_index == 2
        assert identity.segment_offset_sec == 0.0
        assert identity.segment_duration_sec is None

    def test_original_not_found(self, tmp_path: Path):
        compressed = tmp_path / "001_GL010683.mp4"
        compressed.write_bytes(b"\x00" * 500)

        identity = resolve_identity(compressed, tmp_path, "001")
        assert identity.original_stem == "GL010683"
        assert identity.original_path == ""


class TestResolveIdentityWithVindex:
    def test_vindex_provides_segment_info(self, tmp_path: Path):
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        compressed = tmp_path / "002_GL010683_seg02.mp4"
        compressed.write_bytes(b"\x00" * 500)
        segs = [
            SegmentEntry(
                index="001",
                filename="001_GL010683_seg01.mp4",
                offset_sec=0.0,
                duration_sec=40.0,
                segment_number=1,
                total_segments=2,
            ),
            SegmentEntry(
                index="002",
                filename="002_GL010683_seg02.mp4",
                offset_sec=40.0,
                duration_sec=40.0,
                segment_number=2,
                total_segments=2,
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=80.0, segments=segs)
        vindex.write(tmp_path)

        identity = resolve_identity(compressed, tmp_path, "002")
        assert identity.original_stem == "GL010683"
        assert identity.segment_index == 2
        assert identity.segment_offset_sec == 40.0
        assert identity.segment_duration_sec == 40.0

    def test_unrelated_vindex_not_accepted(self, tmp_path: Path):
        """P1-P1-10: a fallback vindex that does not list the current file must
        not be used; resolution falls through to filename parsing (offset 0).
        """
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        compressed = tmp_path / "001_GL010683_seg02.mp4"
        compressed.write_bytes(b"\x00" * 500)
        # Vindex only lists 002_* segments, never 001_GL010683_seg02.mp4.
        segs = [
            SegmentEntry(
                index="002",
                filename="002_GL010683_seg02.mp4",
                offset_sec=40.0,
                duration_sec=40.0,
                segment_number=2,
                total_segments=2,
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=80.0, segments=segs)
        vindex.write(tmp_path)

        identity = resolve_identity(compressed, tmp_path, "001")
        assert identity.original_stem == "GL010683"
        assert identity.segment_index == 2
        assert identity.segment_offset_sec == 0.0
        assert identity.segment_duration_sec is None


class TestIdentityToDict:
    def test_round_trip(self):
        identity = MediaIdentity(
            original_stem="GL010683",
            original_path="/videos/GL010683.mp4",
            compressed_stem="001_GL010683_seg01",
            compressed_path="/compressed/001_GL010683_seg01.mp4",
            index="001",
            segment_index=1,
            segment_offset_sec=60.0,
            segment_duration_sec=60.0,
        )
        d = _identity_to_dict(identity)
        assert isinstance(d, dict)
        assert d["original_stem"] == "GL010683"
        assert d["segment_index"] == 1
        assert d["segment_offset_sec"] == 60.0
        assert d["segment_duration_sec"] == 60.0

    def test_minimal_fields(self):
        identity = MediaIdentity(
            original_stem="GL010683",
            original_path="",
            compressed_stem="001_GL010683",
            compressed_path="/compressed/001_GL010683.mp4",
            index="001",
        )
        d = _identity_to_dict(identity)
        assert d["segment_index"] is None
        assert d["segment_offset_sec"] == 0.0
        assert d["segment_duration_sec"] is None


class TestIsLegacySplit:
    def test_stem_plain(self):
        assert is_legacy_split_stem("001_GL010683") is False

    def test_stem_seg(self):
        assert is_legacy_split_stem("001_GL010683_seg01") is True

    def test_stem_part_alias(self):
        assert is_legacy_split_stem("001_GL010683_part02") is True

    def test_path_vmeta_split_info(self, tmp_path: Path):
        src = tmp_path / "GL.mp4"
        src.write_bytes(b"\x00" * 100)
        compressed = tmp_path / "001_GL.mp4"
        compressed.write_bytes(b"\x00" * 50)
        meta = VideoMeta.build(
            source=src,
            target=compressed,
            source_duration=100.0,
            target_duration=50.0,
            split_info=SplitInfo(
                original_stem="GL",
                segment_index=1,
                total_segments=2,
                offset_sec=0.0,
                segment_duration_sec=50.0,
            ),
        )
        meta.write(compressed)
        assert is_legacy_split_path(compressed) is True

    def test_path_plain_vmeta(self, tmp_path: Path):
        src = tmp_path / "GL.mp4"
        src.write_bytes(b"\x00" * 100)
        compressed = tmp_path / "001_GL.mp4"
        compressed.write_bytes(b"\x00" * 50)
        VideoMeta.build(
            source=src,
            target=compressed,
            source_duration=100.0,
            target_duration=100.0,
        ).write(compressed)
        assert is_legacy_split_path(compressed) is False

    def test_natural_part_suffix_with_non_split_vmeta(self, tmp_path: Path):
        source = tmp_path / "holiday_part01.mov"
        compressed = tmp_path / "001_holiday_part01.mp4"
        source.write_bytes(b"source")
        compressed.write_bytes(b"compressed")
        VideoMeta.build(source, compressed, 2400.0, 2400.0, split_info=None).write(compressed)

        assert is_legacy_split_path(compressed) is False
        identity = resolve_identity(compressed, "001", project_dir=tmp_path)
        assert identity.original_stem == "holiday_part01"
        assert identity.segment_index is None

    def test_canonical_whole_filters_legacy_leftovers(self, tmp_path: Path):
        source = tmp_path / "holiday.mov"
        whole = tmp_path / "003_holiday.mp4"
        segment = tmp_path / "001_holiday_seg01.mp4"
        source.write_bytes(b"source")
        whole.write_bytes(b"whole")
        segment.write_bytes(b"segment")
        VideoMeta.build(source, whole, 2400.0, 2400.0, split_info=None).write(whole)

        assert prefer_canonical_compressed([segment, whole]) == [whole]

    def test_identity_helpers(self):
        plain = MediaIdentity(
            original_stem="GL",
            original_path="/GL.mp4",
            compressed_stem="001_GL",
            compressed_path="/c/001_GL.mp4",
            index="001",
        )
        split = MediaIdentity(
            original_stem="GL",
            original_path="/GL.mp4",
            compressed_stem="001_GL_seg02",
            compressed_path="/c/001_GL_seg02.mp4",
            index="001",
            segment_index=2,
            segment_offset_sec=900.0,
            segment_duration_sec=900.0,
        )
        assert is_legacy_split_identity(plain) is False
        assert is_legacy_split_identity(split) is True
        assert legacy_segment_offset_sec(plain) == 0.0
        assert legacy_segment_offset_sec(split) == 900.0
        assert legacy_segment_offset_sec(None) == 0.0

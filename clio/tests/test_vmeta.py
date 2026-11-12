"""Tests for clio/vmeta.py — .vmeta / .vindex sidecar files."""

from __future__ import annotations

from pathlib import Path

from clio.vmeta import (
    VINDEX_EXT,
    VMETA_EXT,
    SegmentEntry,
    SplitInfo,
    VideoIndex,
    VideoMeta,
)


class TestVideoMeta:
    def test_vmeta_write_read_roundtrip(self, tmp_path: Path):
        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_source.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(
            source=src,
            target=tgt,
            source_duration=120.5,
            target_duration=120.3,
            compress_settings={"max_width": 640, "fps": 15},
        )
        path = meta.write(tgt)
        assert path == tgt.with_suffix(VMETA_EXT)

        loaded = VideoMeta.read(tgt)
        assert loaded is not None
        assert loaded.source_path == str(src.resolve())
        assert loaded.target_path == tgt.name
        assert loaded.source_duration_sec == 120.5
        assert loaded.target_duration_sec == 120.3
        assert loaded.compress_settings == {"max_width": 640, "fps": 15}
        assert loaded.is_original is False
        assert loaded.is_split_segment is False

    def test_vmeta_read_missing_returns_none(self, tmp_path: Path):
        missing = tmp_path / "nonexistent.mp4"
        assert VideoMeta.read(missing) is None

    def test_vmeta_read_corrupted_returns_none(self, tmp_path: Path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"\x00" * 100)
        meta_path = bad.with_suffix(VMETA_EXT)
        meta_path.write_text("{invalid json", encoding="utf-8")
        assert VideoMeta.read(bad) is None

    def test_vmeta_read_non_object_returns_none(self, tmp_path: Path):
        bad = tmp_path / "bad.mp4"
        bad.with_suffix(VMETA_EXT).write_text("[]", encoding="utf-8")
        assert VideoMeta.read(bad) is None

    def test_vmeta_read_missing_required_field_returns_none(self, tmp_path: Path):
        bad = tmp_path / "bad.mp4"
        bad.with_suffix(VMETA_EXT).write_text('{"data": {}}', encoding="utf-8")
        assert VideoMeta.read(bad) is None

    def test_vmeta_is_stale_mtime_change(self, tmp_path: Path):
        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_source.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=10, target_duration=10)
        assert not meta.is_stale(src)
        modified = tmp_path / "modified.mp4"
        modified.write_bytes(b"\x00" * 1000)
        import os

        os.utime(str(modified), (0, 0))
        assert meta.is_stale(modified)

    def test_vmeta_is_stale_size_change(self, tmp_path: Path):
        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_source.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=10, target_duration=10)
        assert not meta.is_stale(src)
        src.write_bytes(b"\x00" * 2000)
        assert meta.is_stale(src)

    def test_vmeta_is_stale_file_missing(self, tmp_path: Path):
        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_source.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=10, target_duration=10)
        assert not meta.is_stale(src)
        missing = tmp_path / "nope.mp4"
        assert meta.is_stale(missing)

    def test_vmeta_is_stale_target_hash_change(self, tmp_path: Path):
        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_source.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=10, target_duration=10)
        assert not meta.is_stale(src, tgt)
        tgt.write_bytes(b"\x01" * 500)
        assert meta.is_stale(src, tgt)

    def test_vmeta_is_stale_target_size_change(self, tmp_path: Path):
        src = tmp_path / "source.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_source.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=10, target_duration=10)
        tgt.write_bytes(b"\x00" * 600)
        assert meta.is_stale(src, tgt)

    def test_vmeta_split_info_roundtrip(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_src_seg01.mp4"
        tgt.write_bytes(b"\x00" * 500)
        si = SplitInfo(
            original_stem="src",
            segment_index=1,
            total_segments=3,
            offset_sec=0.0,
            segment_duration_sec=40.0,
        )
        meta = VideoMeta.build(
            source=src,
            target=tgt,
            source_duration=120.0,
            target_duration=40.0,
            split_info=si,
        )
        meta.write(tgt)
        loaded = VideoMeta.read(tgt)
        assert loaded is not None
        assert loaded.is_split_segment
        assert loaded.split_info is not None
        assert loaded.split_info.original_stem == "src"
        assert loaded.split_info.segment_index == 1
        assert loaded.split_info.total_segments == 3
        assert loaded.split_info.offset_sec == 0.0
        assert loaded.split_info.segment_duration_sec == 40.0

    def test_vmeta_split_info_none_roundtrip(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        tgt = tmp_path / "001_src.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=10, target_duration=10)
        meta.write(tgt)
        loaded = VideoMeta.read(tgt)
        assert loaded is not None
        assert not loaded.is_split_segment
        assert loaded.split_info is None


class TestVideoIndex:
    def test_vindex_write_read_roundtrip(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        segs = [
            SegmentEntry(
                index="001",
                filename="001_src_seg01.mp4",
                offset_sec=0.0,
                duration_sec=40.0,
                segment_number=1,
                total_segments=3,
            ),
            SegmentEntry(
                index="002",
                filename="002_src_seg02.mp4",
                offset_sec=40.0,
                duration_sec=40.0,
                segment_number=2,
                total_segments=3,
            ),
            SegmentEntry(
                index="003",
                filename="003_src_seg03.mp4",
                offset_sec=80.0,
                duration_sec=40.0,
                segment_number=3,
                total_segments=3,
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=120.0, segments=segs)
        path = vindex.write(tmp_path)
        assert path == tmp_path / f"src{VINDEX_EXT}"

        loaded = VideoIndex.read("src", tmp_path)
        assert loaded is not None
        assert loaded.source_stem == "src"
        assert loaded.source_path == str(src.resolve())
        assert loaded.source_duration_sec == 120.0
        assert loaded.is_split
        assert len(loaded.segments) == 3
        assert loaded.segments[1].index == "002"
        assert loaded.segments[1].filename == "002_src_seg02.mp4"

    def test_vindex_read_missing_returns_none(self, tmp_path: Path):
        assert VideoIndex.read("nonexistent", tmp_path) is None

    def test_vindex_read_non_object_returns_none(self, tmp_path: Path):
        (tmp_path / f"bad{VINDEX_EXT}").write_text("[]", encoding="utf-8")
        assert VideoIndex.read("bad", tmp_path) is None

    def test_vindex_read_invalid_segment_returns_none(self, tmp_path: Path):
        (tmp_path / f"bad{VINDEX_EXT}").write_text(
            '{"source_stem": "bad", "source_path": "bad.mp4", "source_size": 1,'
            ' "source_modifyTime": 1, "segments": [{}]}',
            encoding="utf-8",
        )
        assert VideoIndex.read("bad", tmp_path) is None

    def test_vindex_compressed_paths_filters_missing(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        exist = tmp_path / "001_src_seg01.mp4"
        exist.write_bytes(b"\x00" * 100)
        segs = [
            SegmentEntry(
                index="001",
                filename="001_src_seg01.mp4",
                offset_sec=0.0,
                duration_sec=40.0,
                segment_number=1,
                total_segments=2,
            ),
            SegmentEntry(
                index="002",
                filename="002_src_seg02.mp4",
                offset_sec=40.0,
                duration_sec=40.0,
                segment_number=2,
                total_segments=2,
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=80.0, segments=segs)
        vindex.write(tmp_path)
        loaded = VideoIndex.read("src", tmp_path)
        assert loaded is not None
        paths = loaded.compressed_paths(tmp_path)
        assert len(paths) == 1
        assert paths[0].name == "001_src_seg01.mp4"

    def test_vindex_is_split_true_for_multi_segment(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        segs = [
            SegmentEntry(
                index="001", filename="001_x.mp4", offset_sec=0.0, duration_sec=10.0, segment_number=1, total_segments=2
            ),
            SegmentEntry(
                index="002",
                filename="002_x.mp4",
                offset_sec=10.0,
                duration_sec=10.0,
                segment_number=2,
                total_segments=2,
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=20.0, segments=segs)
        assert vindex.is_split

    def test_vindex_is_split_false_for_single(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        segs = [
            SegmentEntry(
                index="001", filename="001_x.mp4", offset_sec=0.0, duration_sec=10.0, segment_number=1, total_segments=1
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=10.0, segments=segs)
        assert not vindex.is_split

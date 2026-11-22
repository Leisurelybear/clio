"""Regression tests for B-03b: same-stem originals in different folders each get a .vindex."""

from __future__ import annotations

from pathlib import Path

from clio.identity import resolve_identity
from clio.vmeta import VINDEX_EXT, SegmentEntry, VideoIndex


def _seg(filename: str) -> SegmentEntry:
    return SegmentEntry(
        index=filename.split("_", 1)[0],
        filename=filename,
        offset_sec=0.0,
        duration_sec=10.0,
        segment_number=1,
        total_segments=1,
    )


def _two_sources(tmp_path: Path) -> tuple[Path, Path]:
    src_a = tmp_path / "a" / "GL010683.mp4"
    src_b = tmp_path / "b" / "GL010683.mp4"
    for src in (src_a, src_b):
        src.parent.mkdir(parents=True)
        src.write_text(f"source {src.parent.name}")
    return src_a, src_b


class TestVideoIndexSameStem:
    def test_second_source_gets_hash_suffixed_index(self, tmp_path: Path) -> None:
        src_a, src_b = _two_sources(tmp_path)
        comp = tmp_path / "compressed"
        comp.mkdir()
        path_a = VideoIndex.build(src_a, 10.0, [_seg("001_GL010683.mp4")]).write(comp)
        path_b = VideoIndex.build(src_b, 10.0, [_seg("002_GL010683.mp4")]).write(comp)

        assert path_a == comp / f"GL010683{VINDEX_EXT}"
        assert path_b != path_a
        assert path_b.name.startswith("GL010683.") and path_b.name.endswith(VINDEX_EXT)
        # Rewriting either source keeps its own file instead of clobbering the other.
        assert VideoIndex.build(src_a, 10.0, [_seg("001_GL010683.mp4")]).write(comp) == path_a
        assert VideoIndex.build(src_b, 10.0, [_seg("002_GL010683.mp4")]).write(comp) == path_b

        legacy = VideoIndex.read("GL010683", comp)
        assert legacy is not None and legacy.source_path == str(src_a.resolve())
        by_seg = VideoIndex.read("GL010683", comp, segment_filename="002_GL010683.mp4")
        assert by_seg is not None and by_seg.source_path == str(src_b.resolve())
        by_src = VideoIndex.read("GL010683", comp, source_path=src_b)
        assert by_src is not None and [s.filename for s in by_src.segments] == ["002_GL010683.mp4"]
        assert VideoIndex.read("GL010683", comp, segment_filename="999_GL010683.mp4") is None
        assert len(VideoIndex.read_all("GL010683", comp)) == 2
        # verify-style lookup by the sidecar's own stem still works.
        assert VideoIndex.read(path_b.name[: -len(VINDEX_EXT)], comp) is not None

    def test_identity_resolves_second_source_from_vindex(self, tmp_path: Path) -> None:
        src_a, src_b = _two_sources(tmp_path)
        comp = tmp_path / "compressed"
        comp.mkdir()
        (comp / "002_GL010683.mp4").write_text("comp b")
        VideoIndex.build(src_a, 10.0, [_seg("001_GL010683.mp4")]).write(comp)
        VideoIndex.build(src_b, 10.0, [_seg("002_GL010683.mp4")]).write(comp)

        identity = resolve_identity(comp / "002_GL010683.mp4", "002")
        assert identity.original_path == str(src_b.resolve())

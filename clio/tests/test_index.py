"""Tests for clio/index.py — ArtifactIndex, ArtifactGroup."""

from __future__ import annotations

import json
from pathlib import Path

from clio.identity import MediaIdentity, _identity_to_dict
from clio.vmeta import SegmentEntry, VideoIndex, VideoMeta


def _make_text_json(path: Path, compressed_stem: str, index: str, title: str):
    """Create a v2 text JSON file with media_identity."""
    identity = MediaIdentity(
        original_stem="GL010683",
        original_path=str(path.parent.parent / "GL010683.mp4"),
        compressed_stem=compressed_stem,
        compressed_path=str(path.parent.parent / "compressed" / f"{compressed_stem}.mp4"),
        index=index,
        segment_index=None,
        segment_offset_sec=0.0,
        segment_duration_sec=None,
    )
    data = {
        "version": 2,
        "title": title,
        "compressed_file": f"{compressed_stem}.mp4",
        "media_identity": _identity_to_dict(identity),
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_script_json(path: Path, text_stem: str):
    """Create a voiceover JSON file."""
    data = {"version": 2, "text": f"Voiceover for {text_stem}"}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _make_transcript_json(path: Path, compressed_stem: str):
    """Create a transcription JSON file with media_identity."""
    identity = MediaIdentity(
        original_stem="GL010683",
        original_path="",
        compressed_stem=compressed_stem,
        compressed_path="",
        index=compressed_stem.split("_")[0],
        segment_index=None,
        segment_offset_sec=0.0,
        segment_duration_sec=None,
    )
    data = {
        "version": 2,
        "segments": [],
        "media_identity": _identity_to_dict(identity),
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestArtifactIndexBuild:
    """Verify ArtifactIndex scans directories and builds correct maps."""

    def _make_index(self, tmp_path: Path, **kwargs):
        from clio.index import ArtifactIndex

        compressed_dir = kwargs.pop("compressed_dir", tmp_path / "compressed")
        texts_dir = kwargs.pop("texts_dir", tmp_path / "texts")
        scripts_dir = kwargs.pop("scripts_dir", tmp_path / "scripts")
        transcripts_dir = kwargs.pop("transcripts_dir", tmp_path / "transcripts")
        covers_dir = kwargs.pop("covers_dir", tmp_path / "covers")
        return ArtifactIndex(
            output_dir=tmp_path,
            project_dir=tmp_path,
            compressed_dir=compressed_dir or tmp_path / "compressed",
            texts_dir=texts_dir or tmp_path / "texts",
            scripts_dir=scripts_dir or tmp_path / "scripts",
            transcripts_dir=transcripts_dir or tmp_path / "transcripts",
            covers_dir=covers_dir or tmp_path / "covers",
            **kwargs,
        )

    def test_build_and_lookup_by_compressed_stem(self, tmp_path: Path):
        compressed_dir = tmp_path / "compressed"
        texts_dir = tmp_path / "texts"
        scripts_dir = tmp_path / "scripts"
        transcripts_dir = tmp_path / "transcripts"
        covers_dir = tmp_path / "covers"
        for d in [compressed_dir, texts_dir, scripts_dir, transcripts_dir, covers_dir]:
            d.mkdir(parents=True)

        # Create compressed video
        compressed_path = compressed_dir / "001_GL010683.mp4"
        compressed_path.write_bytes(b"\x00" * 100)

        # Create .vmeta
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        meta = VideoMeta.build(source=src, target=compressed_path, source_duration=120.0, target_duration=120.0)
        meta.write(compressed_path)

        # Create text JSON with identity
        text_path = texts_dir / "001_Sunset_Beach.json"
        _make_text_json(text_path, "001_GL010683", "001", "Sunset Beach")

        # Create script JSON
        script_path = scripts_dir / "001_Sunset_Beach_voiceover.json"
        _make_script_json(script_path, "001_Sunset_Beach")

        # Create transcript JSON
        transcript_path = transcripts_dir / "001_GL010683_transcript.json"
        _make_transcript_json(transcript_path, "001_GL010683")

        # Create cover
        cover_path = covers_dir / "001_Sunset_Beach.jpg"
        cover_path.write_bytes(b"\x00" * 50)

        # --- Test ---
        from clio.index import ArtifactIndex

        index = ArtifactIndex(
            output_dir=tmp_path,
            project_dir=tmp_path,
            compressed_dir=compressed_dir,
            texts_dir=texts_dir,
            scripts_dir=scripts_dir,
            transcripts_dir=transcripts_dir,
            covers_dir=covers_dir,
        )
        index.build()

        group = index.lookup(compressed_stem="001_GL010683")
        assert group is not None
        assert group.compressed.path == compressed_path
        assert group.compressed.stem == "001_GL010683"
        assert len(group.texts) == 1
        assert group.texts[0].path == text_path
        assert group.texts[0].title == "Sunset Beach"
        assert group.script is not None
        assert group.script.path == script_path
        assert group.transcript is not None
        assert group.transcript.path == transcript_path
        assert group.cover is not None
        assert group.cover.path == cover_path

    def test_lookup_by_original_stem(self, tmp_path: Path):
        compressed_dir = tmp_path / "compressed"
        texts_dir = tmp_path / "texts"
        for d in [compressed_dir, texts_dir]:
            d.mkdir(parents=True)

        compressed_path = compressed_dir / "001_GL010683.mp4"
        compressed_path.write_bytes(b"\x00" * 100)
        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 1000)
        meta = VideoMeta.build(source=src, target=compressed_path, source_duration=120.0, target_duration=120.0)
        meta.write(compressed_path)

        text_path = texts_dir / "001_Sunset_Beach.json"
        _make_text_json(text_path, "001_GL010683", "001", "Sunset Beach")

        index = self._make_index(tmp_path)
        index.build()

        groups = index.lookup(original_stem="GL010683")
        assert groups is not None
        assert isinstance(groups, list)
        assert len(groups) >= 1
        assert groups[0].compressed.stem == "001_GL010683"

    def test_all_groups(self, tmp_path: Path):
        compressed_dir = tmp_path / "compressed"
        texts_dir = tmp_path / "texts"
        for d in [compressed_dir, texts_dir]:
            d.mkdir(parents=True)

        for i, stem in enumerate(["GL010683", "GL010684"], 1):
            idx = f"{i:03d}"
            cp = compressed_dir / f"{idx}_{stem}.mp4"
            cp.write_bytes(b"\x00" * 100)
            s = tmp_path / f"{stem}.mp4"
            s.write_bytes(b"\x00" * 1000)
            meta = VideoMeta.build(source=s, target=cp, source_duration=120.0, target_duration=120.0)
            meta.write(cp)
            _make_text_json(texts_dir / f"{idx}_Title_{i}.json", f"{idx}_{stem}", idx, f"Title {i}")

        index = self._make_index(tmp_path)
        index.build()

        groups = index.all_groups()
        assert len(groups) == 2

    def test_empty_dirs(self, tmp_path: Path):
        index = self._make_index(tmp_path)
        index.build()
        assert index.all_groups() == []
        assert index.lookup(compressed_stem="anything") is None

    def test_v1_text_fallback(self, tmp_path: Path):
        """Text JSON without media_identity, using compressed_file field."""
        compressed_dir = tmp_path / "compressed"
        texts_dir = tmp_path / "texts"
        for d in [compressed_dir, texts_dir]:
            d.mkdir(parents=True)

        compressed_path = compressed_dir / "001_GL010683.mp4"
        compressed_path.write_bytes(b"\x00" * 100)

        text_path = texts_dir / "001_Sunset_Beach.json"
        text_path.write_text(
            json.dumps({"version": 1, "title": "Sunset Beach", "compressed_file": "001_GL010683.mp4"}),
            encoding="utf-8",
        )

        index = self._make_index(tmp_path)
        index.build()

        group = index.lookup(compressed_stem="001_GL010683")
        assert group is not None
        assert len(group.texts) == 1
        assert group.texts[0].title == "Sunset Beach"

    def test_legacy_index_token_does_not_match_1_to_10(self, tmp_path: Path):
        compressed_dir = tmp_path / "compressed"
        texts_dir = tmp_path / "texts"
        compressed_dir.mkdir()
        texts_dir.mkdir()
        (compressed_dir / "10_ten.mp4").write_bytes(b"video")
        (compressed_dir / "1_one.mp4").write_bytes(b"video")
        (texts_dir / "1_analysis.json").write_text(json.dumps({"title": "one"}), encoding="utf-8")

        index = self._make_index(tmp_path)
        index.build()

        one = index.lookup(compressed_stem="1_one")
        ten = index.lookup(compressed_stem="10_ten")
        assert one is not None and [item.title for item in one.texts] == ["one"]
        assert ten is not None and ten.texts == []

    def test_equivalent_index_ambiguity_is_not_auto_assigned(self, tmp_path: Path):
        compressed_dir = tmp_path / "compressed"
        texts_dir = tmp_path / "texts"
        compressed_dir.mkdir()
        texts_dir.mkdir()
        (compressed_dir / "1_one.mp4").write_bytes(b"video")
        (compressed_dir / "001_other.mp4").write_bytes(b"video")
        (texts_dir / "1_analysis.json").write_text(json.dumps({"title": "ambiguous"}), encoding="utf-8")

        index = self._make_index(tmp_path)
        index.build()

        assert all(group.texts == [] for group in index.all_groups())
        assert index.ambiguous_artifacts() == (texts_dir / "1_analysis.json",)

    def test_legacy_split_artifacts(self, tmp_path: Path):
        """One original mapped to multiple compressed segments."""
        compressed_dir = tmp_path / "compressed"
        texts_dir = tmp_path / "texts"
        for d in [compressed_dir, texts_dir]:
            d.mkdir(parents=True)

        src = tmp_path / "GL010683.mp4"
        src.write_bytes(b"\x00" * 2000)

        segs = [
            SegmentEntry(
                index="001",
                filename="001_GL010683_seg01.mp4",
                offset_sec=0.0,
                duration_sec=40.0,
                segment_number=1,
                total_segments=3,
            ),
            SegmentEntry(
                index="002",
                filename="002_GL010683_seg02.mp4",
                offset_sec=40.0,
                duration_sec=40.0,
                segment_number=2,
                total_segments=3,
            ),
            SegmentEntry(
                index="003",
                filename="003_GL010683_seg03.mp4",
                offset_sec=80.0,
                duration_sec=40.0,
                segment_number=3,
                total_segments=3,
            ),
        ]
        vindex = VideoIndex.build(source=src, source_duration=120.0, segments=segs)
        vindex.write(compressed_dir)

        for seg in segs:
            cp = compressed_dir / seg.filename
            cp.write_bytes(b"\x00" * 100)

        _make_text_json(texts_dir / "001_Sunset_Beach_seg01.json", "001_GL010683_seg01", "001", "Sunset Beach 1")
        _make_text_json(texts_dir / "002_Sunset_Beach_seg02.json", "002_GL010683_seg02", "002", "Sunset Beach 2")

        index = self._make_index(tmp_path)
        index.build()

        # All 3 compressed files should be in groups
        groups = index.all_groups()
        assert len(groups) == 3

        # Lookup by original stem should return 3 groups
        origin_groups = index.lookup(original_stem="GL010683")
        assert origin_groups is not None
        assert len(origin_groups) == 3

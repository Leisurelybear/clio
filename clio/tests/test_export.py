"""Tests for clio.export package."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.export import export_plan
from clio.export.jianying import (
    _build_index_to_source,
    _build_materials,
    _build_tracks,
    _resolve_video,
    _resolve_video_by_prefix,
    _to_microseconds,
    export_plan_to_jianying,
)

# ── export/__init__.py ──────────────────────────────────────────────


class TestExportPlan:
    def test_jianying_delegates_to_exporter(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text("{}")
        output_dir = tmp_path / "output"
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        mock_fn = MagicMock(return_value=output_dir / "draft")
        with patch("clio.export.FORMAT_REGISTRY", {"jianying": mock_fn}):
            result = export_plan("jianying", plan_path, output_dir, input_dir, "day1")

        mock_fn.assert_called_once_with(plan_path, output_dir, input_dir, "day1", project_dir=None)
        assert result == output_dir / "draft"

    def test_unknown_format_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown export format: unknown"):
            export_plan("unknown", Path("x.json"), Path("out"), Path("in"))


# ── export/jianying.py helpers ─────────────────────────────────────


class TestToMicroseconds:
    def test_converts_correctly(self) -> None:
        assert _to_microseconds(1.0) == 1_000_000
        assert _to_microseconds(0.0) == 0
        assert _to_microseconds(1.5) == 1_500_000
        assert _to_microseconds(0.001) == 1_000


class TestResolveVideoByPrefix:
    def test_no_ffprobe_returns_none(self) -> None:
        assert _resolve_video_by_prefix("001", [], None) is None

    def test_finds_video_by_prefix(self, tmp_path: Path) -> None:
        video = tmp_path / "001_GL010683.mp4"
        video.write_text("fake")
        videos = [video]

        with patch("clio.export.jianying.get_duration_sec", return_value=60.0):
            result = _resolve_video_by_prefix("001", videos, "ffprobe")

        assert result is not None
        path, duration = result
        assert path == video.resolve()
        assert duration == 60_000_000

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        result = _resolve_video_by_prefix("001", [], "ffprobe")
        assert result is None

    def test_matches_unpadded_index_with_custom_width(self, tmp_path: Path) -> None:
        video = tmp_path / "0001_GL010683.mp4"
        video.write_text("fake")
        with patch("clio.export.jianying.get_duration_sec", return_value=60.0):
            result = _resolve_video_by_prefix("1", [video], "ffprobe", index_width=4)
        assert result is not None
        assert result[0] == video.resolve()


class TestBuildIndexToSource:
    def test_missing_dir_returns_empty_dict(self, tmp_path: Path) -> None:
        d = tmp_path / "nonexistent"
        assert _build_index_to_source(d) == {}

    def test_builds_mapping(self, tmp_path: Path) -> None:
        texts_dir = tmp_path / "texts"
        texts_dir.mkdir()
        (texts_dir / "001.json").write_text(json.dumps({"index": 1, "source_file": "GL010683.mp4"}), encoding="utf-8")

        mapping = _build_index_to_source(texts_dir)

        assert mapping.get("1") == "GL010683"
        assert mapping.get("001") == "GL010683"

    def test_builds_mapping_for_custom_index_width(self, tmp_path: Path) -> None:
        texts_dir = tmp_path / "texts"
        texts_dir.mkdir()
        (texts_dir / "0001.json").write_text(json.dumps({"index": 1, "source_file": "GL010683.mp4"}), encoding="utf-8")

        mapping = _build_index_to_source(texts_dir, index_width=4)

        assert mapping["1"] == "GL010683"
        assert mapping["0001"] == "GL010683"

    def test_skips_invalid_json(self, tmp_path: Path) -> None:
        texts_dir = tmp_path / "texts"
        texts_dir.mkdir()
        (texts_dir / "bad.json").write_text("not json", encoding="utf-8")

        assert _build_index_to_source(texts_dir) == {}

    def test_skips_missing_fields(self, tmp_path: Path) -> None:
        texts_dir = tmp_path / "texts"
        texts_dir.mkdir()
        (texts_dir / "no_idx.json").write_text(json.dumps({"source_file": "GL010683.mp4"}), encoding="utf-8")
        (texts_dir / "no_src.json").write_text(json.dumps({"index": 1}), encoding="utf-8")

        assert _build_index_to_source(texts_dir) == {}


class TestResolveVideo:
    def test_no_ffprobe_returns_none(self) -> None:
        assert _resolve_video("GL010683", [], None) is None

    def test_finds_by_stem_case_insensitive(self, tmp_path: Path) -> None:
        video = tmp_path / "GL010683.mp4"
        video.write_text("fake")
        videos = [video]

        with patch("clio.export.jianying.get_duration_sec", return_value=60.0):
            result = _resolve_video("gl010683", videos, "ffprobe")

        assert result is not None
        path, duration = result
        assert path == video.resolve()
        assert duration == 60_000_000

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        result = _resolve_video("NONEXISTENT", [], "ffprobe")
        assert result is None


class TestBuildMaterials:
    def test_empty_sequence(self, tmp_path: Path) -> None:
        plan_data = {"sequence": []}
        materials, idx_map, text_ids = _build_materials(plan_data, [], "ffprobe", {})
        assert len(materials["videos"]) == 0
        assert len(materials["texts"]) == 0
        assert idx_map == {}
        assert text_ids == {}

    def test_builds_video_and_text(self, tmp_path: Path) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "voiceover_hint": "Hello world"},
            ]
        }
        index_to_source = {"001": "GL010683"}

        with patch("clio.export.jianying._resolve_video") as mock_resolve:
            mock_resolve.return_value = (tmp_path / "GL010683.mp4", 60_000_000)

            materials, idx_map, text_ids = _build_materials(plan_data, [], "ffprobe", index_to_source)

        assert len(materials["videos"]) == 1
        assert len(materials["texts"]) == 1
        assert idx_map["001"] == materials["videos"][0]["id"]
        assert text_ids[0] == materials["texts"][0]["id"]

    def test_skips_segment_when_video_not_found(self, tmp_path: Path) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "voiceover_hint": "Text only"},
            ]
        }

        with patch("clio.export.jianying._resolve_video", return_value=None):
            materials, idx_map, text_ids = _build_materials(plan_data, [], "ffprobe", {"001": "GL010683"})

        assert len(materials["videos"]) == 0
        assert len(materials["texts"]) == 0
        assert idx_map == {}
        assert text_ids == {}

    def test_fallback_to_prefix_matching(self, tmp_path: Path) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "voiceover_hint": ""},
            ]
        }
        input_dir = tmp_path / "input"
        input_dir.mkdir()

        with patch("clio.export.jianying._resolve_video_by_prefix") as mock_prefix:
            mock_prefix.return_value = (tmp_path / "001_GL010683.mp4", 60_000_000)

            materials, idx_map, text_ids = _build_materials(plan_data, [], "ffprobe", {})

        assert len(materials["videos"]) == 1
        mock_prefix.assert_called_once_with("001", [], "ffprobe", index_width=3)


class TestBuildTracks:
    def test_builds_video_and_text_tracks(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "00:00-00:10", "voiceover_hint": "Hello"},
                {"index": "001", "use_timeline": "00:10-00:20", "voiceover_hint": ""},
            ]
        }
        index_to_material_id = {"001": "mat-001"}
        seq_text_ids = {0: "text-001"}

        with patch("clio.export.jianying.parse_time_range") as mock_parse:
            mock_parse.side_effect = [(0.0, 10.0), (10.0, 20.0)]

            tracks = _build_tracks(plan_data, index_to_material_id, seq_text_ids)

        assert len(tracks) == 2
        assert tracks[0]["type"] == "video"
        assert tracks[1]["type"] == "text"
        assert len(tracks[0]["segments"]) == 2
        assert len(tracks[1]["segments"]) == 1

    def test_skips_unknown_index(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "999", "use_timeline": "00:00-00:10"},
            ]
        }
        tracks = _build_tracks(plan_data, {}, {})
        assert tracks == []

    def test_skips_invalid_timeline(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "invalid"},
            ]
        }
        index_to_material_id = {"001": "mat-001"}

        with patch("clio.export.jianying.parse_time_range", side_effect=ValueError):
            tracks = _build_tracks(plan_data, index_to_material_id, {})

        assert tracks == []


class TestExportPlanToJianying:
    def test_missing_plan_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="plan 文件不存在"):
            export_plan_to_jianying(
                tmp_path / "nonexistent.json",
                tmp_path / "output",
                tmp_path / "input",
            )

    def test_generates_complete_draft_json(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "day_title": "Day 1",
                    "sequence": [
                        {
                            "index": "001",
                            "use_timeline": "00:00-00:10",
                            "voiceover_hint": "Hello",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        video_path = input_dir / "GL010683.mp4"
        video_path.write_text("fake")

        texts_dir = tmp_path / "texts"
        texts_dir.mkdir()
        (texts_dir / "001.json").write_text(json.dumps({"index": 1, "source_file": "GL010683.mp4"}), encoding="utf-8")

        output_dir = tmp_path / "output"

        with (
            patch("clio.utils.find_videos", return_value=[video_path]),
            patch("clio.export.jianying.get_duration_sec", return_value=60.0),
        ):
            result = export_plan_to_jianying(plan_path, output_dir, input_dir, "day1", "ffprobe", texts_dir)

        assert result == output_dir
        draft_path = output_dir / "draft_content.json"
        assert draft_path.exists()

        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        assert draft["name"] == "Day 1"
        assert draft["fps"] == 30
        assert len(draft["materials"]["videos"]) == 1
        assert len(draft["materials"]["texts"]) == 1
        assert len(draft["tracks"]) >= 1
        assert draft["tracks"][0]["type"] == "video"

    def test_raises_when_no_video_material_resolves(self, tmp_path: Path) -> None:
        """A plan with segments but zero resolvable videos must NOT export an
        empty draft — it must raise instead of silently producing an empty file."""
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(
            json.dumps(
                {
                    "day_title": "Day 1",
                    "sequence": [{"index": "001", "use_timeline": "00:00-00:10"}],
                }
            ),
            encoding="utf-8",
        )

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        unused = input_dir / "SOMETHING.mp4"
        unused.write_text("fake")

        texts_dir = tmp_path / "texts"
        texts_dir.mkdir()
        (texts_dir / "001.json").write_text(json.dumps({"index": 1, "source_file": "GL010683.mp4"}), encoding="utf-8")

        output_dir = tmp_path / "output"

        with (
            patch("clio.utils.find_videos", return_value=[unused]),
            patch("clio.export.jianying.get_duration_sec", return_value=60.0),
        ):
            with pytest.raises(RuntimeError, match="没有任何视频素材可解析"):
                export_plan_to_jianying(plan_path, output_dir, input_dir, "day1", "ffprobe", texts_dir)

        assert not (output_dir / "draft_content.json").exists()

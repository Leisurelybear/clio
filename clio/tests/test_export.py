"""Tests for clio.export package."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.export import export_plan
from clio.export.jianying import (
    _build_index_to_offset,
    _build_index_to_source,
    _build_materials,
    _build_srt,
    _build_tracks,
    _format_srt_timestamp,
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


class TestSrt:
    def test_formats_timestamp(self) -> None:
        assert _format_srt_timestamp(0) == "00:00:00,000"
        assert _format_srt_timestamp(3_723_456_789) == "01:02:03,456"
        assert _format_srt_timestamp(-1) == "00:00:00,000"

    def test_builds_cues_from_text_track(self) -> None:
        materials = {
            "texts": [
                {"id": "text-1", "content": json.dumps({"text": "Hello\nWorld"})},
                {"id": "text-2", "content": "not-json"},
            ]
        }
        tracks = [
            {
                "type": "text",
                "segments": [
                    {"material_id": "text-1", "target_timerange": {"start": 0, "duration": 10_000_000}},
                    {"material_id": "missing", "target_timerange": {"start": 10_000_000, "duration": 2_000_000}},
                ],
            }
        ]

        assert _build_srt(materials, tracks) == ("1\n00:00:00,000 --> 00:00:10,000\nHello\nWorld\n")


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


def test_build_index_to_offset_expands_legacy_index_keys(tmp_path: Path) -> None:
    texts_dir = tmp_path / "texts"
    texts_dir.mkdir()
    identity = {
        "original_stem": "GX010001",
        "original_path": "GX010001.mp4",
        "compressed_stem": "001_GX010001_seg02",
        "compressed_path": "001_GX010001_seg02.mp4",
        "index": "001",
        "segment_index": 2,
        "segment_offset_sec": 30.0,
        "segment_duration_sec": 30.0,
    }
    (texts_dir / "001.json").write_text(json.dumps({"media_identity": identity}), encoding="utf-8")

    offsets = _build_index_to_offset(texts_dir)

    assert offsets["1"] == 30.0
    assert offsets["001"] == 30.0


def test_build_index_to_source_skips_missing_fields(tmp_path: Path) -> None:
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
                {"index": "001", "subtitle": "Hello world", "voiceover_hint": "ignored narration"},
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
        import json

        content = json.loads(materials["texts"][0]["content"])
        assert content["text"] == "Hello world"

    def test_voiceover_hint_alone_does_not_create_text_track(self, tmp_path: Path) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "voiceover_hint": "narration only"},
            ]
        }
        with patch("clio.export.jianying._resolve_video") as mock_resolve:
            mock_resolve.return_value = (tmp_path / "GL010683.mp4", 60_000_000)
            materials, _idx_map, text_ids = _build_materials(plan_data, [], "ffprobe", {"001": "GL010683"})
        assert len(materials["videos"]) == 1
        assert materials["texts"] == []
        assert text_ids == {}

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
                {"index": "001", "use_timeline": "00:00-00:10", "subtitle": "Hello"},
                {"index": "001", "use_timeline": "00:10-00:20", "subtitle": ""},
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

    def test_clamps_timeline_to_material_duration(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "00:15-00:25"},
            ]
        }

        with patch("clio.export.jianying.parse_time_range", return_value=(15.0, 25.0)):
            tracks = _build_tracks(plan_data, {"001": "mat-001"}, {}, index_to_duration_us={"001": 20_000_000})

        segment = tracks[0]["segments"][0]
        assert segment["source_timerange"] == {"start": 15_000_000, "duration": 5_000_000}
        assert segment["target_timerange"] == {"start": 0, "duration": 5_000_000}

    def test_skips_timeline_starting_beyond_material(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "00:12-00:20"},
            ]
        }

        with patch("clio.export.jianying.parse_time_range", return_value=(12.0, 20.0)):
            tracks = _build_tracks(plan_data, {"001": "mat-001"}, {}, index_to_duration_us={"001": 10_000_000})

        assert tracks == []

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


class TestBuildTransitions:
    def test_dissolve_attaches_transition_to_previous_segment(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "00:00-00:05"},
                {"index": "001", "use_timeline": "00:05-00:10", "transition": "dissolve"},
            ]
        }
        transitions: list[dict] = []

        with patch("clio.export.jianying.parse_time_range") as mock_parse:
            mock_parse.side_effect = [(0.0, 5.0), (5.0, 10.0)]
            tracks = _build_tracks(plan_data, {"001": "mat-001"}, {}, transition_materials=transitions)

        segments = tracks[0]["segments"]
        assert "extra_material_refs" not in segments[1]
        assert segments[0]["extra_material_refs"] == [transitions[0]["id"]]
        assert len(transitions) == 1
        assert transitions[0]["name"] == "叠化"
        assert transitions[0]["resource_id"] == "6724845717472416269"
        assert transitions[0]["effect_id"] == "322577"
        assert transitions[0]["duration"] == 500_000
        assert transitions[0]["type"] == "transition"
        assert transitions[0]["is_overlap"] is True

    def test_hard_cut_adds_no_transition(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "00:00-00:05"},
                {"index": "001", "use_timeline": "00:05-00:10", "transition": "hard_cut"},
            ]
        }
        transitions: list[dict] = []

        with patch("clio.export.jianying.parse_time_range") as mock_parse:
            mock_parse.side_effect = [(0.0, 5.0), (5.0, 10.0)]
            tracks = _build_tracks(plan_data, {"001": "mat-001"}, {}, transition_materials=transitions)

        assert transitions == []
        assert all("extra_material_refs" not in seg for seg in tracks[0]["segments"])

    def test_first_segment_transition_is_ignored(self) -> None:
        plan_data = {"sequence": [{"index": "001", "use_timeline": "00:00-00:05", "transition": "dissolve"}]}
        transitions: list[dict] = []

        with patch("clio.export.jianying.parse_time_range", return_value=(0.0, 5.0)):
            _build_tracks(plan_data, {"001": "mat-001"}, {}, transition_materials=transitions)

        assert transitions == []

    def test_short_segment_skips_transition(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "00:00-00:00.3"},
                {"index": "001", "use_timeline": "00:05-00:10", "transition": "dissolve"},
            ]
        }
        transitions: list[dict] = []

        with patch("clio.export.jianying.parse_time_range") as mock_parse:
            mock_parse.side_effect = [(0.0, 0.3), (5.0, 10.0)]
            tracks = _build_tracks(plan_data, {"001": "mat-001"}, {}, transition_materials=transitions)

        assert transitions == []
        assert "extra_material_refs" not in tracks[0]["segments"][0]

    def test_unknown_transition_kind_is_ignored(self) -> None:
        plan_data = {
            "sequence": [
                {"index": "001", "use_timeline": "00:00-00:05"},
                {"index": "001", "use_timeline": "00:05-00:10", "transition": "b_roll"},
            ]
        }
        transitions: list[dict] = []

        with patch("clio.export.jianying.parse_time_range") as mock_parse:
            mock_parse.side_effect = [(0.0, 5.0), (5.0, 10.0)]
            _build_tracks(plan_data, {"001": "mat-001"}, {}, transition_materials=transitions)

        assert transitions == []


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
                            "subtitle": "Hello",
                            "voiceover_hint": "narration only",
                        },
                        {
                            "index": "001",
                            "use_timeline": "00:10-00:15",
                            "subtitle": "World",
                            "transition": "dissolve",
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
            patch("clio.export.jianying.probe_media_details", return_value={"fps": 29.97}),
            patch("clio.export.jianying.platform.system", return_value="Darwin"),
        ):
            result = export_plan_to_jianying(plan_path, output_dir, input_dir, "day1", "ffprobe", texts_dir)

        assert result == output_dir
        draft_path = output_dir / "draft_content.json"
        assert draft_path.exists()

        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        assert draft["name"] == "Day 1"
        assert draft["fps"] == 30
        assert draft["platform"]["os"] == "mac"
        assert len(draft["materials"]["videos"]) == 1
        assert len(draft["materials"]["texts"]) == 2
        assert len(draft["materials"]["transitions"]) == 1
        transition = draft["materials"]["transitions"][0]
        assert transition["name"] == "叠化"
        assert draft["tracks"][0]["segments"][0]["extra_material_refs"] == [transition["id"]]
        assert len(draft["tracks"]) >= 1
        assert draft["tracks"][0]["type"] == "video"
        assert (output_dir / "subtitles.srt").read_text(encoding="utf-8") == (
            "1\n00:00:00,000 --> 00:00:10,000\nHello\n\n2\n00:00:10,000 --> 00:00:15,000\nWorld\n"
        )

        meta = json.loads((output_dir / "draft_meta_info.json").read_text(encoding="utf-8"))
        assert meta["draft_id"] == draft["id"]
        assert meta["draft_name"] == "Day 1"
        assert meta["draft_root_path"] == str(output_dir)
        assert meta["tm_duration"] == draft["duration"]
        assert meta["draft_materials"] == []

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

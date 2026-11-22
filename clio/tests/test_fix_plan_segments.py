"""Regression tests: plan segments with non-string fields (int index, null title)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clio.export.jianying import _build_materials, _build_tracks
from clio.tasks._helpers import normalize_plan_segment
from clio.tasks.cut import _cut_artifact_plan


class TestNormalizePlanSegment:
    def test_coerces_text_fields(self):
        seg = normalize_plan_segment({"index": 1, "title": None, "use_timeline": " 00:01-00:03 ", "extra": 5})
        assert seg["index"] == "1"
        assert seg["title"] == ""
        assert seg["use_timeline"] == "00:01-00:03"
        assert seg["extra"] == 5

    def test_non_dict_becomes_empty(self):
        assert normalize_plan_segment(None) == {}


class TestCutArtifactPlan:
    def test_int_index_and_null_title_do_not_crash(self):
        seq = [normalize_plan_segment({"index": 2, "title": None, "use_timeline": "00:00-00:02"})]
        names, preserved = _cut_artifact_plan(seq)
        assert preserved == set()
        assert any(name.startswith("2_") and name.endswith("_seg_001.mp4") for name in names)


class TestJianyingIntIndex:
    def test_int_index_uses_source_mapping_and_offset(self, tmp_path: Path):
        video = tmp_path / "GL010695.mp4"
        video.write_bytes(b"v")
        plan = {"sequence": [{"index": 1, "title": None, "use_timeline": "00:00-00:02"}]}
        with patch("clio.export.jianying.get_duration_sec", return_value=60.0):
            materials, index_to_material_id, seq_text_ids = _build_materials(
                plan, [video], "ffprobe", {"1": "GL010695", "001": "GL010695"}
            )
        assert len(materials["videos"]) == 1
        assert "1" in index_to_material_id

        tracks = _build_tracks(plan, index_to_material_id, seq_text_ids, {"1": 10.0})
        video_track = next(track for track in tracks if track["type"] == "video")
        assert video_track["segments"][0]["source_timerange"]["start"] == 10_000_000

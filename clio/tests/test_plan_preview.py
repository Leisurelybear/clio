"""Tests for standard cut-clip preview mapping (R-031b)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clio.plan_preview import _clip_is_current, _valid_clip, build_preview_media, expected_clip_name
from clio.ui.routes.plan import handle_get_plan_preview_media
from clio.ui.routes.videos import handle_get_video


def _plan(*segments: dict) -> dict:
    return {"sequence": list(segments)}


def _add_clip(
    cuts_dir: Path,
    segment: dict,
    index: int,
    *,
    info: dict | None = None,
    payload: bytes = b"video",
) -> Path:
    cuts_dir.mkdir(parents=True, exist_ok=True)
    name = expected_clip_name(segment, index)
    clip = cuts_dir / name
    clip.write_bytes(payload)
    if info is not None:
        clip.with_suffix(".json").write_text(json.dumps({"_cut_info": info}), encoding="utf-8")
    return clip


def test_expected_name_matches_cut_task_convention():
    segment = {"index": "001", "title": "A/B test"}
    assert expected_clip_name(segment, 3) == "001_AB_test_seg_003.mp4"


def test_maps_standard_clips_and_partial_fallback(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    first = {"index": "001", "title": "开场", "use_timeline": "00:01-00:06"}
    second = {"index": "002", "title": "第二段", "use_timeline": "00:10-00:14"}
    first_clip = _add_clip(
        cuts_dir,
        first,
        1,
        info={
            "seg_index": 1,
            "timeline": "00:01-00:06",
            "start_sec": 1,
            "end_sec": 6,
        },
    )
    first_clip_stat = first_clip.stat()
    result = build_preview_media(_plan(first, second), cuts_dir, day="day1")
    assert result["available"] is True
    assert result["segments"][0]["file"] == "001_开场_seg_001.mp4"
    assert result["segments"][0]["rev"] == f"{first_clip_stat.st_mtime_ns}-{first_clip_stat.st_size}"
    assert result["segments"][0]["duration_sec"] == 5
    assert result["segments"][1]["file"] is None
    assert result["segments"][1]["source"]["video_index"] == "002"


def test_accepts_old_clip_without_cut_info(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "old", "use_timeline": "00:00-00:03"}
    _add_clip(cuts_dir, segment, 1, info=None)
    result = build_preview_media(_plan(segment), cuts_dir, day="day1")
    assert result["segments"][0]["file"] == "001_old_seg_001.mp4"


def test_rejects_old_clip_without_metadata_after_plan_change(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "old", "use_timeline": "00:00-00:03"}
    clip = _add_clip(cuts_dir, segment, 1, info=None)
    plan_mtime = clip.stat().st_mtime + 10
    result = build_preview_media(_plan(segment), cuts_dir, day="day1", plan_mtime=plan_mtime)
    assert result["segments"][0]["file"] is None


def test_new_plan_times_accept_offset_or_truncated_actual_times(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "offset", "use_timeline": "00:00-00:10"}
    clip = _add_clip(
        cuts_dir,
        segment,
        1,
        info={
            "seg_index": 1,
            "timeline": "00:00-00:10",
            "plan_start_sec": 0,
            "plan_end_sec": 10,
            "start_sec": 120,
            "end_sec": 128,
        },
    )
    old = clip.stat().st_mtime - 20
    os.utime(clip, (old, old))
    result = build_preview_media(_plan(segment), cuts_dir, day="day1", plan_mtime=old + 10)
    assert result["segments"][0]["file"] == "001_offset_seg_001.mp4"


def test_legacy_actual_times_are_accepted_while_clip_is_current(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "legacy", "use_timeline": "00:00-00:10"}
    clip = _add_clip(
        cuts_dir,
        segment,
        1,
        info={"seg_index": 1, "timeline": "00:00-00:10", "start_sec": 120, "end_sec": 128},
    )
    plan_mtime = clip.stat().st_mtime - 5
    result = build_preview_media(_plan(segment), cuts_dir, day="day1", plan_mtime=plan_mtime)
    assert result["segments"][0]["file"] == "001_legacy_seg_001.mp4"


def test_legacy_actual_times_are_rejected_after_plan_change(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "legacy", "use_timeline": "00:00-00:10"}
    clip = _add_clip(
        cuts_dir,
        segment,
        1,
        info={"seg_index": 1, "timeline": "00:00-00:10", "start_sec": 120, "end_sec": 128},
    )
    plan_mtime = clip.stat().st_mtime + 10
    result = build_preview_media(_plan(segment), cuts_dir, day="day1", plan_mtime=plan_mtime)
    assert result["segments"][0]["file"] is None


@pytest.mark.parametrize(
    "info",
    [
        {"seg_index": 2, "timeline": "00:00-00:03"},
        {"seg_index": 1, "timeline": "00:00-00:04"},
        {"timeline": "00:00-00:03"},
    ],
)
def test_rejects_mismatched_cut_info(tmp_path: Path, info: dict):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "clip", "use_timeline": "00:00-00:03"}
    _add_clip(cuts_dir, segment, 1, info=info)
    assert build_preview_media(_plan(segment), cuts_dir, day="day1")["segments"][0]["file"] is None


def test_rejects_truncated_json_beside_clip(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "clip", "use_timeline": "00:00-00:03"}
    clip = _add_clip(cuts_dir, segment, 1)
    clip.with_suffix(".json").write_text("{broken", encoding="utf-8")
    assert build_preview_media(_plan(segment), cuts_dir, day="day1")["segments"][0]["file"] is None


def test_duplicate_sequence_entries_map_by_clip_index(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "same", "use_timeline": "00:00-00:02"}
    _add_clip(cuts_dir, segment, 1)
    _add_clip(cuts_dir, segment, 2)
    result = build_preview_media(_plan(segment, segment), cuts_dir, day="day1")
    assert [item["file"] for item in result["segments"]] == [
        "001_same_seg_001.mp4",
        "001_same_seg_002.mp4",
    ]


def test_symlink_clip_is_not_mapped(tmp_path: Path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlink unavailable")
    cuts_dir = tmp_path / "cuts" / "day1"
    cuts_dir.mkdir(parents=True)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    segment = {"index": "001", "title": "link", "use_timeline": "00:00-00:02"}
    link = cuts_dir / expected_clip_name(segment, 1)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not permitted")
    assert build_preview_media(_plan(segment), cuts_dir, day="day1")["segments"][0]["file"] is None


def test_preview_route_rejects_bad_day_and_missing_plan(tmp_path: Path):
    handler = MagicMock()
    handle_get_plan_preview_media(handler, {"day": ["../secret"]})
    assert handler._send_json.call_args.args[1] == 403

    handler = MagicMock()
    handle_get_plan_preview_media(handler, {"day": ["C:foo"]})
    assert handler._send_json.call_args.args[1] == 403

    cfg = MagicMock()
    cfg.plans_dir = tmp_path / "plans"
    handler = MagicMock()
    handler._resolve_project_dir.return_value = tmp_path
    handler._get_config.return_value = cfg
    handle_get_plan_preview_media(handler, {"day": ["day1"]})
    assert handler._send_json.call_args.args[1] == 404


def test_preview_route_uses_standard_cuts_directory(tmp_path: Path):
    plans_dir = tmp_path / "output" / "plans"
    plans_dir.mkdir(parents=True)
    segment = {"index": "001", "title": "clip", "use_timeline": "00:00-00:03"}
    (plans_dir / "day1_plan.json").write_text(json.dumps(_plan(segment)), encoding="utf-8")
    cuts_dir = tmp_path / "output" / "cuts" / "day1"
    _add_clip(cuts_dir, segment, 1)
    cfg = MagicMock()
    cfg.plans_dir = plans_dir
    cfg.paths.output_dir = str(tmp_path / "output")
    handler = MagicMock()
    handler._resolve_project_dir.return_value = tmp_path
    handler._get_config.return_value = cfg
    handle_get_plan_preview_media(handler, {"day": ["day1"]})
    payload = handler._send_json.call_args.args[0]
    assert payload["segments"][0]["file"] == "001_clip_seg_001.mp4"


def test_preview_route_rejects_symlinked_cuts_root(tmp_path: Path):
    plans_dir = tmp_path / "output" / "plans"
    plans_dir.mkdir(parents=True)
    (plans_dir / "day1_plan.json").write_text(json.dumps(_plan()), encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "output" / "cuts").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted")
    cfg = MagicMock()
    cfg.plans_dir = plans_dir
    cfg.paths.output_dir = str(tmp_path / "output")
    handler = MagicMock()
    handler._resolve_project_dir.return_value = tmp_path
    handler._get_config.return_value = cfg
    handle_get_plan_preview_media(handler, {"day": ["day1"]})
    assert handler._send_json.call_args.args[1] == 403


def _video_handler(tmp_path: Path) -> MagicMock:
    handler = MagicMock()
    handler._resolve_project_dir.return_value = tmp_path
    handler._get_project_output.return_value = tmp_path / "output"
    handler.send_error = MagicMock()
    return handler


def test_cut_video_route_serves_only_standard_day_child(tmp_path: Path):
    cuts_dir = tmp_path / "output" / "cuts" / "day1"
    cuts_dir.mkdir(parents=True)
    clip = cuts_dir / "001_clip_seg_001.mp4"
    clip.write_bytes(b"video")
    handler = _video_handler(tmp_path)
    handle_get_video(handler, {"day": ["day1"], "file": ["001_clip_seg_001.mp4"], "source": ["cut"]})
    handler._send_video_range.assert_called_once_with(clip.resolve())


def test_cut_video_route_rejects_traversal_and_nested_path(tmp_path: Path):
    handler = _video_handler(tmp_path)
    handle_get_video(handler, {"day": ["../day1"], "file": ["clip.mp4"], "source": ["cut"]})
    assert handler.send_error.call_args.args[0] == 403
    handler.send_error.reset_mock()
    handle_get_video(handler, {"day": ["day1"], "file": ["nested/clip.mp4"], "source": ["cut"]})
    assert handler.send_error.call_args.args[0] == 403


def test_sidecar_without_cut_info_falls_back_to_mtime(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "s", "use_timeline": "00:00-00:03"}
    clip = _add_clip(cuts_dir, segment, 1, info=None)
    clip.with_suffix(".json").write_text("{}", encoding="utf-8")

    result = build_preview_media(_plan(segment), cuts_dir, day="day1")

    assert result["segments"][0]["file"] == clip.name


def test_mismatched_legacy_end_time_rejected_after_plan_change(tmp_path: Path):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "s", "use_timeline": "00:00-00:03"}
    clip = _add_clip(
        cuts_dir,
        segment,
        1,
        info={"seg_index": 1, "timeline": "00:00-00:03", "start_sec": 0, "end_sec": 99},
    )
    plan_mtime = clip.stat().st_mtime + 10

    result = build_preview_media(_plan(segment), cuts_dir, day="day1", plan_mtime=plan_mtime)

    assert result["segments"][0]["file"] is None


def test_clip_is_current_returns_false_when_stat_fails(tmp_path: Path):
    assert _clip_is_current(tmp_path / "missing.mp4", 123.0) is False


def test_valid_clip_returns_none_when_stat_raises(tmp_path: Path, monkeypatch):
    cuts_dir = tmp_path / "cuts" / "day1"
    segment = {"index": "001", "title": "s", "use_timeline": "00:00-00:03"}
    clip = _add_clip(
        cuts_dir,
        segment,
        1,
        info={"seg_index": 1, "timeline": "00:00-00:03", "start_sec": 0, "end_sec": 3},
    )
    real_stat = Path.stat
    seen = {"count": 0}

    def flaky_stat(self, *args, **kwargs):
        if self.name == clip.name:
            seen["count"] += 1
            if seen["count"] >= 2:
                raise OSError("stat failed")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr("clio.plan_preview.validate_within_root", lambda path, root: path)
    monkeypatch.setattr(Path, "stat", flaky_stat)

    assert _valid_clip(cuts_dir, segment, 1) is None

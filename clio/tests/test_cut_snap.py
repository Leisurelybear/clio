from __future__ import annotations

from clio.cut_snap import SnapBoundaries, _format_time, snap_plan_ranges, snap_range


def test_snap_range_prefers_shot_start_and_sentence_end():
    result = snap_range(
        0.4,
        20.5,
        SnapBoundaries(shots=(0.0, 10.0, 20.0), sentence_ends=(9.2, 20.8)),
    )

    assert result.start == 0.0
    assert result.end == 20.8
    assert result.start_snapped is True
    assert result.end_snapped is True


def test_snap_range_leaves_distant_endpoints_unchanged():
    result = snap_range(4.0, 16.0, SnapBoundaries(shots=(0.0, 20.0)))

    assert result.start == 4.0
    assert result.end == 16.0
    assert result.start_snapped is False
    assert result.end_snapped is False


def test_snap_range_rejects_adjustment_that_makes_clip_too_short():
    result = snap_range(9.7, 10.1, SnapBoundaries(shots=(9.9, 10.2)))

    assert result.start == 9.7
    assert result.end == 10.1


def test_snap_plan_ranges_uses_transcript_offset_and_updates_timeline():
    plan = {
        "sequence": [
            {"index": "001", "use_timeline": "00:00.3-00:10.2"},
            {"index": "002", "use_timeline": "00:00-00:10"},
        ]
    }
    clips = [
        {
            "index": "001",
            "match_stem": "A",
            "segment_offset_sec": 30.0,
            "timeline": [{"start": "00:00", "end": "00:10"}],
        }
    ]
    transcripts = {
        "a": {
            "segments": [
                {"start": 30.0, "end": 40.4, "text": "第一句。"},
            ]
        }
    }

    changed = snap_plan_ranges(plan, clips, transcripts)

    assert changed == 1
    assert plan["sequence"][0]["use_timeline"] == "00:00-00:10.4"
    assert plan["sequence"][1]["use_timeline"] == "00:00-00:10"


def test_snap_plan_ranges_ignores_invalid_or_missing_boundaries():
    plan = {"sequence": [{"index": "001", "use_timeline": "invalid"}]}
    clips = [{"index": "001", "timeline": [{"start": "bad", "end": "bad"}]}]

    assert snap_plan_ranges(plan, clips, {}) == 0
    assert plan["sequence"][0]["use_timeline"] == "invalid"


def test_format_time_keeps_milliseconds_only_when_needed():
    assert _format_time(65.25) == "01:05.25"
    assert _format_time(3661.0) == "01:01:01"

"""Behavioral tests for the shared plan pacing heuristics."""

from __future__ import annotations

from clio.plan_model import PlanSegment
from clio.plan_pacing import (
    BROLL_MAX_SEC,
    MIN_SEGMENT_SEC,
    duration_floor,
    fragment_positions,
    notable_fragments,
    segment_duration,
    segment_durations,
    timeline_text,
    total_seconds,
)


def _seg(timeline: str) -> dict[str, str]:
    return {"use_timeline": timeline}


def test_segment_duration_reads_dict_and_plan_segment():
    assert segment_duration(_seg("00:00-00:20")) == 20.0
    assert segment_duration(PlanSegment(index="001", use_timeline="00:10-00:40")) == 30.0
    assert timeline_text(PlanSegment(index="001", use_timeline=" 00:00-00:08 ")) == "00:00-00:08"


def test_segment_duration_treats_invalid_ranges_as_zero():
    assert segment_duration(_seg("")) == 0.0
    assert segment_duration(_seg("nope")) == 0.0
    assert segment_duration(_seg("00:20-00:10")) == 0.0
    assert segment_duration({"index": "001"}) == 0.0


def test_total_seconds_sums_only_parseable_segments():
    sequence = [_seg("00:00-00:30"), _seg("bad"), _seg("00:05-00:15")]

    assert segment_durations(sequence) == [30.0, 0.0, 10.0]
    assert total_seconds(sequence) == 40.0


def test_duration_floor_is_target_aware_but_never_below_readiness_minimum():
    assert duration_floor(None) == 30.0
    assert duration_floor(0) == 30.0
    assert duration_floor(-180) == 30.0
    assert duration_floor(20) == 30.0
    assert duration_floor(180) == 144.0


def test_fragment_positions_exempt_opening_and_short_broll():
    durations = [5.0, 2.0, 5.0, 8.0, 3.0, 7.9]

    assert fragment_positions(durations) == [3, 6]
    assert BROLL_MAX_SEC == 3.0
    assert MIN_SEGMENT_SEC == 8.0


def test_notable_fragments_requires_more_than_quarter_of_segments():
    # Two fragments in nine segments is within tolerance (max(2, 9 // 4) == 2).
    assert notable_fragments([20.0, 5.0, 5.0] + [20.0] * 6) == []
    # Three fragments in nine segments crosses the threshold.
    assert notable_fragments([20.0, 5.0, 5.0, 5.0] + [20.0] * 5) == [2, 3, 4]


def test_notable_fragments_ignores_empty_sequences():
    assert notable_fragments([]) == []


def test_effective_max_segments_no_target_keeps_configured():
    from clio.plan_pacing import effective_max_segments

    # No target or negative target returns configured value
    assert effective_max_segments(40, None) == 40
    assert effective_max_segments(12, 0) == 12
    assert effective_max_segments(25, -180) == 25


def test_effective_max_segments_caps_based_on_target():
    from clio.plan_pacing import effective_max_segments

    # 180s target with MIN_SEGMENT_SEC=8s → ceil(180/8)=23 segments +2 allowance=25
    assert effective_max_segments(40, 180) == 25  # capped to 25
    assert effective_max_segments(12, 180) == 12  # configured 12 is lower than 25
    assert effective_max_segments(25, 180) == 25  # exactly the calculated limit

    # Smaller targets
    assert effective_max_segments(40, 60) == 10  # ceil(60/8)=8 +2=10
    assert effective_max_segments(5, 60) == 5  # configured 5 is lower

    # Edge cases
    assert effective_max_segments(40, 8) == 3  # ceil(8/8)=1 +2=3
    assert effective_max_segments(40, 1) == 3  # ceil(1/8)=1 +2=3


def test_is_pacing_improvement_basics():
    from clio.plan_pacing import is_pacing_improvement

    orig = {"sequence": [{"use_timeline": "00:00-00:10"}]}  # 10s
    rep = {"sequence": [{"use_timeline": "00:00-00:20"}]}  # 20s

    # Both below floor (30s), repaired is longer → improvement
    assert is_pacing_improvement(orig, rep, target_duration_sec=None)

    # Same duration, no improvement
    same = {"sequence": [{"use_timeline": "00:00-00:10"}]}
    assert not is_pacing_improvement(orig, same, target_duration_sec=None)


def test_is_pacing_improvement_fragment_reduction():
    from clio.plan_pacing import is_pacing_improvement

    # Original: 3 fragments (positions 2,3,4)
    orig = {
        "sequence": [
            {"use_timeline": "00:00-00:10"},  # position 1, exempt
            {"use_timeline": "00:10-00:15"},  # position 2, 5s fragment
            {"use_timeline": "00:15-00:20"},  # position 3, 5s fragment
            {"use_timeline": "00:20-00:25"},  # position 4, 5s fragment
        ]
    }

    # Repaired: merged fragments
    rep = {
        "sequence": [
            {"use_timeline": "00:00-00:10"},
            {"use_timeline": "00:10-00:25"},  # merged 3 fragments into one 15s segment
        ]
    }

    # Fewer fragments is an improvement
    assert is_pacing_improvement(orig, rep, target_duration_sec=None)


def test_is_pacing_improvement_meets_duration_floor():
    from clio.plan_pacing import is_pacing_improvement

    # Floor is 144s for 180s target
    orig = {"sequence": [{"use_timeline": "00:00-01:00"}]}  # 60s, below floor
    rep = {"sequence": [{"use_timeline": "00:00-02:30"}]}  # 150s, meets floor

    # Crossing the floor is an improvement
    assert is_pacing_improvement(orig, rep, target_duration_sec=180)


def test_is_pacing_improvement_rejects_worse_plans():
    from clio.plan_pacing import is_pacing_improvement

    orig = {
        "sequence": [
            {"use_timeline": "00:00-00:20"},
            {"use_timeline": "00:20-00:40"},
        ]
    }  # 40s total, no fragments

    rep = {
        "sequence": [
            {"use_timeline": "00:00-00:10"},
            {"use_timeline": "00:10-00:15"},  # 5s fragment
        ]
    }  # 25s total, 1 fragment

    # Worse on both dimensions
    assert not is_pacing_improvement(orig, rep, target_duration_sec=None)

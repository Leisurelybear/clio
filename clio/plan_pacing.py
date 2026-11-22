"""Shared pacing heuristics for daily vlog plans.

Both the generation-time repair loop in :mod:`clio.analyze` and the export
readiness checks in :mod:`clio.plan_readiness` must agree on what a usable cut
looks like. Keeping the thresholds and the fragment detector here prevents the
two surfaces from drifting apart.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from clio.cut import parse_time_range

# A day plan must reach at least this share of the configured target duration.
MIN_TOTAL_DURATION_RATIO = 0.8
# Narrative beats shorter than this look like fragments rather than complete shots.
MIN_SEGMENT_SEC = 8.0
# 1-3s B-roll inserts are intentional; only longer short cuts look fragmented.
BROLL_MAX_SEC = 3.0
# Cap how many positions get spelled out in a single message.
MAX_REPORTED_SHORT_SEGMENTS = 6
# Readiness treats anything under 30s as unusably short even without a target.
READINESS_MIN_TOTAL_SEC = 30.0


def timeline_text(segment: Any) -> str:
    """Read ``use_timeline`` from a plan dict or :class:`clio.plan_model.PlanSegment`."""
    value = segment.get("use_timeline") if isinstance(segment, dict) else getattr(segment, "use_timeline", "")
    return str(value or "").strip()


def segment_duration(segment: Any) -> float:
    """Duration in seconds for one segment; unparseable ranges count as zero."""
    try:
        start, end = parse_time_range(timeline_text(segment))
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, end - start)


def segment_durations(sequence: Iterable[Any]) -> list[float]:
    return [segment_duration(segment) for segment in sequence]


def total_seconds(sequence: Iterable[Any]) -> float:
    return sum(segment_durations(sequence))


def duration_floor(target_duration_sec: float | None) -> float:
    """Minimum acceptable total duration for a day cut.

    Without a target the readiness floor stays at ``READINESS_MIN_TOTAL_SEC``;
    with a target the floor rises to ``MIN_TOTAL_DURATION_RATIO`` of it.
    """
    floor = READINESS_MIN_TOTAL_SEC
    if target_duration_sec is not None and target_duration_sec > 0:
        floor = max(floor, target_duration_sec * MIN_TOTAL_DURATION_RATIO)
    return floor


def fragment_positions(durations: Sequence[float]) -> list[int]:
    """1-based positions of narrative fragments.

    The opening segment is exempt because a short hook is intentional; inserts
    up to ``BROLL_MAX_SEC`` are exempt because they are deliberate B-roll.
    """
    return [
        position
        for position, duration in enumerate(durations, start=1)
        if position > 1 and BROLL_MAX_SEC < duration < MIN_SEGMENT_SEC
    ]


def notable_fragments(durations: Sequence[float]) -> list[int]:
    """Fragment positions only when they are common enough to hurt pacing."""
    if not durations:
        return []
    positions = fragment_positions(durations)
    if len(positions) > max(2, len(durations) // 4):
        return positions
    return []


def effective_max_segments(max_segments: int, target_duration_sec: float | None) -> int:
    """Cap segment count based on target duration to avoid fragmentation.

    When a target duration is set (target_duration_sec > 0), we calculate the
    maximum reasonable number of segments as:
        ceil(target_duration_sec / MIN_SEGMENT_SEC) + ALLOWANCE

    where ALLOWANCE = 2 accounts for hook (exempt) and B‑roll inserts.
    The result is then bounded by the configured max_segments.
    """
    if target_duration_sec is None or target_duration_sec <= 0:
        return max_segments

    import math

    basic_limit = math.ceil(target_duration_sec / MIN_SEGMENT_SEC)
    allowance = 2  # hook + B‑roll inserts
    calculated = basic_limit + allowance

    return min(max_segments, calculated)


def is_pacing_improvement(
    original: dict,
    repaired: dict,
    target_duration_sec: float | None,
) -> bool:
    """Return True if the repaired plan is Pareto‑better on pacing.

    Criteria (all must hold):
    1. Total duration shortfall is not worse (≥ original duration floor ratio)
    2. Fragment count is not worse (≤ original fragment count)
    3. At least one of the above is strictly better

    The hook segment (position 1) and B‑roll ≤3 s are always exempt.
    """
    orig_seq = original.get("sequence", [])
    rep_seq = repaired.get("sequence", [])

    if not orig_seq or not rep_seq:
        return False

    orig_durations = segment_durations(orig_seq)
    rep_durations = segment_durations(rep_seq)

    # 1. Total duration comparison
    orig_total = sum(orig_durations)
    rep_total = sum(rep_durations)
    floor = duration_floor(target_duration_sec)

    # If both meet or exceed the floor, compare absolute totals
    if orig_total >= floor and rep_total >= floor:
        duration_not_worse = rep_total >= orig_total
    else:
        # Compare distance to floor
        orig_shortfall = max(0.0, floor - orig_total)
        rep_shortfall = max(0.0, floor - rep_total)
        duration_not_worse = rep_shortfall <= orig_shortfall

    # 2. Fragment count comparison
    orig_fragments = len(fragment_positions(orig_durations))
    rep_fragments = len(fragment_positions(rep_durations))
    fragments_not_worse = rep_fragments <= orig_fragments

    # 3. Strict improvement in at least one dimension
    duration_better = (
        (orig_total < floor and rep_total >= floor)  # crosses floor
        or (rep_total > orig_total and orig_total >= floor)  # longer while both meet floor
        or (rep_total < floor and orig_total < floor and rep_total > orig_total)  # both below but closer
    )

    # Special case: identical fragment count but fragment positions moved earlier
    # (earlier fragments are less disruptive to pacing)
    fragments_better = rep_fragments < orig_fragments

    return duration_not_worse and fragments_not_worse and (duration_better or fragments_better)

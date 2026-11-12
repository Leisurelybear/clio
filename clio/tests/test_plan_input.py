"""Tests for bounded planning-input projection."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

from clio.config import AppConfig
from clio.config.models import PlanConfig
from clio.plan_input import build_plan_clips


def _config(row_limit: int = 8, moment_limit: int = 6) -> AppConfig:
    cfg = MagicMock(spec=AppConfig)
    cfg.plan = PlanConfig(
        max_timeline_rows_per_clip=row_limit,
        max_key_moments_per_clip=moment_limit,
    )
    return cast(AppConfig, cfg)


def _row(start: str, end: str, description: str = "", **extra: Any) -> dict[str, Any]:
    return {"start": start, "end": end, "description": description, **extra}


def test_projection_removes_inline_transcripts_and_unknown_fields() -> None:
    clips = [
        {
            "index": "001",
            "title": "Arrival",
            "source_stem": "GL010683",
            "transcript": "INLINE_SECRET",
            "transcript_segments": [{"start": 0, "end": 1, "text": "SECRET"}],
            "timeline": [
                _row(
                    "00:00",
                    "00:10",
                    transcript_segments=[{"start": 0, "end": 1, "text": "SECRET"}],
                    internal_note="drop me",
                )
            ],
        }
    ]

    projected = build_plan_clips(clips, _config())

    assert "transcript" not in projected[0]
    assert "transcript_segments" not in projected[0]
    assert "transcript_segments" not in projected[0]["timeline"][0]
    assert "internal_note" not in projected[0]["timeline"][0]


def test_timeline_rows_are_bounded_sorted_and_keep_endpoints() -> None:
    rows = [_row(f"{minute:02d}:00", f"{minute:02d}:30", str(minute)) for minute in range(10)]

    projected = build_plan_clips([{"index": "001", "timeline": list(reversed(rows))}], _config(row_limit=4))

    timeline = projected[0]["timeline"]
    starts = [row["start"] for row in timeline]
    assert len(timeline) == 4
    assert starts == sorted(starts)
    assert starts[0] == "00:00"
    assert starts[-1] == "09:00"


def test_key_moment_overlap_is_prioritized_over_raw_score() -> None:
    rows = [
        _row("00:00", "00:10", "first", score=1),
        _row("00:10", "00:20", "second", score=1),
        _row("00:20", "00:30", "third", score=1),
        _row("00:30", "00:40", "fourth", score=1),
        _row("00:40", "00:50", "fifth", score=1),
        _row("00:50", "01:00", "sixth", score=1),
    ]
    clips = [
        {
            "index": "001",
            "timeline": rows,
            "highlights": [],
            "key_moments": [{**rows[3], "title": "important", "score": 1}],
        }
    ]

    projected = build_plan_clips(clips, _config(row_limit=3))

    assert [row["description"] for row in projected[0]["timeline"]] == ["first", "fourth", "sixth"]


def test_timeline_score_and_quality_break_middle_ties() -> None:
    rows = [
        _row("00:00", "00:10", "first", score=1),
        _row("00:10", "00:20", "second", score=1),
        _row("00:20", "00:30", "shaky", score=5, quality="shaky"),
        _row("00:30", "00:40", "clean", score=5, quality="ok"),
        _row("00:40", "00:50", "fifth", score=1),
        _row("00:50", "01:00", "sixth", score=1),
    ]

    projected = build_plan_clips([{"index": "001", "timeline": rows}], _config(row_limit=3))

    assert [row["description"] for row in projected[0]["timeline"]] == ["first", "clean", "sixth"]


def test_moments_are_capped_by_score_then_restored_to_time_order() -> None:
    clip = {
        "index": "001",
        "timeline": [_row("00:00", "01:00")],
        "key_moments": [
            {"start": "00:10", "end": "00:12", "title": "late-high", "score": 5},
            {"start": "00:20", "end": "00:22", "title": "early-low", "score": 2},
            {"start": "00:30", "end": "00:32", "title": "earliest-high", "score": 5},
        ],
    }

    projected = build_plan_clips([clip], _config(moment_limit=2))

    assert [item["title"] for item in projected[0]["key_moments"]] == ["late-high", "earliest-high"]


def test_zero_limits_disable_budgeting() -> None:
    clip = {
        "index": "001",
        "timeline": [_row(f"00:{minute:02d}", f"00:{minute + 1:02d}") for minute in range(1, 10)],
        "key_moments": [
            {"start": f"00:{minute:02d}", "end": f"00:{minute + 1:02d}", "title": str(minute)}
            for minute in range(1, 10)
        ],
    }

    projected = build_plan_clips([clip], _config(row_limit=0, moment_limit=0))

    assert len(projected[0]["timeline"]) == 9
    assert len(projected[0]["key_moments"]) == 9


def test_missing_key_moments_falls_back_to_highlights() -> None:
    clip = {
        "index": "001",
        "timeline": [_row("00:00", "01:00")],
        "highlights": [{"start": "00:10", "end": "00:20", "description": "highlight", "score": 4}],
    }

    projected = build_plan_clips([clip], _config())

    assert projected[0]["key_moments"] == projected[0]["highlights"]
    assert projected[0]["key_moments"][0]["title"] == "highlight"


def test_duration_prefers_collected_duration_then_analyze_windows() -> None:
    explicit = {"index": "001", "duration_sec": 42.5, "timeline": [_row("00:00", "00:10")]}
    windows = {
        "index": "002",
        "timeline": [],
        "analyze_windows": [{"start_sec": 0, "end_sec": 25.5}, {"start_sec": 25.5, "end_sec": 60}],
    }

    projected = build_plan_clips([explicit, windows], _config())

    assert projected[0]["duration_sec"] == 42.5
    assert projected[1]["duration_sec"] == 60.0


def test_duration_falls_back_to_timeline_then_clip_duration() -> None:
    with_timeline = {"index": "001", "timeline": [_row("00:00", "00:12.5")]}
    with_duration = {"index": "002", "duration_sec": "00:42.5", "timeline": []}

    projected = build_plan_clips([with_timeline, with_duration], _config())

    assert projected[0]["duration_sec"] == 12.5
    assert projected[1]["duration_sec"] == 42.5


def test_empty_invalid_reversed_and_zero_duration_rows_are_dropped() -> None:
    clip = {
        "index": "001",
        "timeline": [
            _row("", "00:10"),
            _row("00:20", "00:20"),
            _row("00:40", "00:30"),
            _row("bad", "00:50"),
        ],
    }

    projected = build_plan_clips([clip], _config())

    assert projected[0]["timeline"] == []
    assert projected[0]["duration_sec"] == 0.0


def test_list_and_dict_timestamp_values_do_not_crash() -> None:
    clip = {
        "index": "001",
        "timeline": [
            {"start": [1], "end": [2], "description": "list"},
            {"start": {"value": 1}, "end": {"value": 2}, "description": "dict"},
            _row("00:00", "00:01", "valid"),
        ],
    }

    projected = build_plan_clips([clip], _config())

    assert [row["description"] for row in projected[0]["timeline"]] == ["valid"]


def test_missing_end_uses_one_second_span() -> None:
    projected = build_plan_clips(
        [{"index": "001", "timeline": [{"start": "01:02:03.5", "description": "point"}]}],
        _config(),
    )

    assert projected[0]["timeline"][0] == {
        "start": "01:02:03.5",
        "end": "01:02:04.5",
        "description": "point",
    }


def test_projection_sanitizes_structured_fields() -> None:
    clip = {
        "index": "001",
        "timeline": [
            _row(
                "00:00",
                "00:10",
                score=9,
                camera="drone",
                quality="blurry",
                tags=["  scenic ", "", "scenic", 3],
            )
        ],
    }

    row = build_plan_clips([clip], _config())[0]["timeline"][0]

    assert row["score"] == 5
    assert "camera" not in row
    assert "quality" not in row
    assert row["tags"] == ["scenic"]


def test_projection_is_deterministic_and_does_not_mutate_input() -> None:
    clip = {
        "index": "001",
        "timeline": [_row("00:20", "00:30", "later"), _row("00:00", "00:10", "earlier")],
        "key_moments": [{"start": "00:20", "end": "00:21", "title": "later"}],
    }
    original = {"index": "001", "timeline": list(clip["timeline"]), "key_moments": list(clip["key_moments"])}

    first = build_plan_clips([clip], _config(row_limit=1))
    second = build_plan_clips([clip], _config(row_limit=1))

    assert first == second
    assert clip == original

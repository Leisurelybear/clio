"""Table-driven tests for the shared timestamp parsers (A-13)."""

from __future__ import annotations

import math

import pytest

from clio.analyze_windows import shift_analysis_times
from clio.plan_input import _timestamp_seconds as plan_input_seconds
from clio.timecode import parse_timestamp, parse_timestamp_strict


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (12, 12.0),
        (12.5, 12.5),
        ("12.5", 12.5),
        (" 7 ", 7.0),
        ("01:02", 62.0),
        ("01:02.5", 62.5),
        ("1:02:03.5", 3723.5),
        ("01:75", 135.0),
        (-1, -1.0),
        (True, None),
        (None, None),
        ("", None),
        ("abc", None),
        ("1:2:3:4", None),
        ("1.5:30", None),
        ("nan", None),
        (math.inf, None),
        ([], None),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("12.5", 12.5),
        ("01:02", 62.0),
        ("1:02:03.5", 3723.5),
        ("01:75", None),
        ("1:60:00", None),
        (-1, None),
        ("-1", None),
        (True, None),
        ("nan", None),
    ],
)
def test_parse_timestamp_strict(value, expected):
    assert parse_timestamp_strict(value) == expected


def test_plan_input_accepts_plain_second_strings():
    assert plan_input_seconds("12.5") == 12.5
    assert plan_input_seconds("-3") is None


def test_shift_analysis_times_shifts_plain_second_strings():
    shifted = shift_analysis_times(
        {"timeline": [{"start": "12.5", "end": "20", "description": "x"}, {"start": "00:10", "end": 15}]},
        100,
    )

    assert shifted["timeline"][0]["start"] == "112.5"
    assert shifted["timeline"][0]["end"] == "120"
    assert shifted["timeline"][1]["start"] == "01:50"
    assert shifted["timeline"][1]["end"] == 115

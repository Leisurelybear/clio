"""Shared timestamp parsing for analysis timelines and plans.

Analysis JSON produced by models uses several spellings for the same instant:
numbers (``12.5``), plain-second strings (``"12.5"``), ``"MM:SS(.f)"`` and
``"HH:MM:SS(.f)"``. Every module that reads timeline values goes through one of
the two parsers below so they agree on which values are timestamps.
"""

from __future__ import annotations

import math
from typing import Any


def parse_timestamp(value: Any) -> float | None:
    """Parse a timeline value into seconds, or None when it is not a timestamp.

    Accepts finite numbers, plain-second strings (``"12.5"``), ``"MM:SS(.f)"``
    and ``"HH:MM:SS(.f)"`` (hour/minute fields must be integers). Minute and
    second fields are *not* range-checked (``"01:75"`` → 135.0) and negative
    values are returned as-is; callers that need bounds apply them.
    Booleans, None, empty strings and non-finite values return None.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if math.isfinite(seconds) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            seconds = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            return None
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) else None


def parse_timestamp_strict(value: Any) -> float | None:
    """Like :func:`parse_timestamp`, but for validation of finished plans/analyses.

    Every field must be a finite, non-negative number; in ``MM:SS`` the seconds
    must be < 60 and in ``HH:MM:SS`` minutes and seconds must be < 60.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return None
    if any(not math.isfinite(part) or part < 0 for part in values):
        return None
    if len(values) == 3:
        hours, minutes, seconds = values
        if minutes >= 60 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds
    if len(values) == 2:
        minutes, seconds = values
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    return values[0]

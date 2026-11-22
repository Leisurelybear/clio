"""Regression tests: video path keys only fold case where the OS does."""

from __future__ import annotations

import os
from pathlib import Path

from clio.ui.routes.videos import _video_key


def test_video_key_matches_platform_case_rules(tmp_path: Path):
    upper = tmp_path / "A.mp4"
    lower = tmp_path / "a.mp4"
    expected_same = os.path.normcase("A") == os.path.normcase("a")
    assert (_video_key(upper) == _video_key(lower)) is expected_same


def test_video_key_still_collapses_dot_segments(tmp_path: Path):
    real = tmp_path / "clips" / "a.mp4"
    dotted = tmp_path / "clips" / ".." / "clips" / "a.mp4"
    assert _video_key(real) == _video_key(dotted)

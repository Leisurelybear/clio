"""Regression tests: single-file re-analyze must not match other videos by substring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

from clio.config import AppConfig
from clio.tasks.analyze import _collect_single_file_items
from clio.vmeta import VideoMeta


def _config(compressed_dir: Path) -> AppConfig:
    return cast(AppConfig, SimpleNamespace(compressed_dir=compressed_dir))


def test_stem_prefix_of_another_video_is_not_matched(tmp_path: Path):
    comp = tmp_path / "compressed"
    comp.mkdir()
    for name in ("001_GH01_seg01.mp4", "002_GH0123.mp4", "003_GH01_seg02.mp4", "004_xGH01.mp4"):
        (comp / name).write_bytes(b"c")
    original = tmp_path / "GH01.MP4"
    original.write_bytes(b"o")

    items = _collect_single_file_items(original, _config(comp))

    assert items is not None
    assert sorted(item[0].name for item in items) == ["001_GH01_seg01.mp4", "003_GH01_seg02.mp4"]


def test_vmeta_source_path_wins_over_stem(tmp_path: Path):
    comp = tmp_path / "compressed"
    comp.mkdir()
    mine = tmp_path / "a" / "GH01.MP4"
    other = tmp_path / "b" / "GH01.MP4"
    for p in (mine, other):
        p.parent.mkdir()
        p.write_bytes(b"o")
    own = comp / "001_GH01.mp4"
    foreign = comp / "002_GH01.mp4"
    own.write_bytes(b"c1")
    foreign.write_bytes(b"c2")
    VideoMeta.build(source=mine, target=own, source_duration=1.0, target_duration=1.0).write(own)
    VideoMeta.build(source=other, target=foreign, source_duration=1.0, target_duration=1.0).write(foreign)

    items = _collect_single_file_items(mine, _config(comp))

    assert items is not None
    assert [item[0].name for item in items] == ["001_GH01.mp4"]

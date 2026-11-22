"""Regression tests for clio.ui.services.file_service write/stream helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from clio.ui.services.file_service import _save_atomic


def test_save_atomic_keeps_primary_visible_while_backing_up(tmp_path: Path):
    target = tmp_path / "001_a.json"
    target.write_bytes(b"old")
    seen_during_replace: list[bool] = []
    real_replace = os.replace

    def spy_replace(src, dst, *args, **kwargs):
        if Path(dst) == target:
            seen_during_replace.append(target.exists())
        return real_replace(src, dst, *args, **kwargs)

    with patch("os.replace", side_effect=spy_replace):
        _save_atomic(target, b"new")

    assert seen_during_replace == [True]
    assert target.read_bytes() == b"new"
    assert target.with_suffix(".json.bak").read_bytes() == b"old"


def test_save_atomic_backup_falls_back_to_copy_without_links(tmp_path: Path):
    target = tmp_path / "project.yaml"
    target.write_bytes(b"v1")
    with patch("os.link", side_effect=OSError("links unsupported")):
        _save_atomic(target, b"v2")
    assert target.read_bytes() == b"v2"
    assert target.with_suffix(".yaml.bak").read_bytes() == b"v1"


def test_save_atomic_second_write_rotates_previous_backup(tmp_path: Path):
    target = tmp_path / "a.json"
    target.write_bytes(b"1")
    _save_atomic(target, b"2")
    _save_atomic(target, b"3")
    assert target.read_bytes() == b"3"
    assert target.with_suffix(".json.bak").read_bytes() == b"2"
    rotated = [p for p in tmp_path.iterdir() if p.name.startswith("a.json.bak.")]
    assert [p.read_bytes() for p in rotated] == [b"1"]


def test_prune_rotated_backups_handles_glob_metacharacters(tmp_path: Path):
    from clio.ui.services.file_service import _prune_rotated_backups

    target = tmp_path / "001_trip[1].json"
    target.write_bytes(b"x")
    for i in range(6):
        rotated = tmp_path / f"001_trip[1].json.bak.{i}"
        rotated.write_bytes(b"old")
        os.utime(rotated, (1_000_000 + i, 1_000_000 + i))
    (tmp_path / "001_trip[1].json.bak").write_bytes(b"bak")

    _prune_rotated_backups(target, keep=3)

    left = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("001_trip[1].json.bak."))
    assert left == ["001_trip[1].json.bak.3", "001_trip[1].json.bak.4", "001_trip[1].json.bak.5"]
    assert (tmp_path / "001_trip[1].json.bak").exists()

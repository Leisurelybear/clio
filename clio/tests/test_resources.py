"""Tests for bundled runtime resource lookup."""

from __future__ import annotations

from pathlib import Path

from clio.resources import find_resource


def test_finds_resource_in_first_matching_root(tmp_path: Path):
    roots = [tmp_path / "empty", tmp_path / "installed"]
    target = roots[1] / "templates" / "trip_context.md"
    target.parent.mkdir(parents=True)
    target.write_text("context", encoding="utf-8")

    assert find_resource("templates/trip_context.md", roots=roots) == target.resolve()


def test_rejects_absolute_and_parent_paths(tmp_path: Path):
    assert find_resource(str(tmp_path / "config.example.yaml"), roots=[tmp_path]) is None
    assert find_resource("../config.example.yaml", roots=[tmp_path]) is None


def test_repo_resources_are_discoverable():
    assert find_resource("config.example.yaml") is not None
    assert find_resource("templates/vlog_template.md") is not None
    assert find_resource("templates/trip_context.md") is not None
    assert find_resource("ui/static/locales/zh-CN.json") is not None
    assert find_resource("ui/static/locales/en.json") is not None

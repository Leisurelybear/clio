"""Regression tests: ?project=<name> only resolves allow-listed project dirs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clio.ui.services.project_service import ProjectResolutionError, resolve_project_input


def _project(path: Path, name: str) -> Path:
    path.mkdir(parents=True)
    (path / "project.json").write_text(json.dumps({"name": name}), encoding="utf-8")
    return path


def test_unregistered_sibling_is_not_resolved_by_name(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_bytes(b"")
    current = _project(tmp_path / "projects" / "current", "Current")
    _project(tmp_path / "projects" / "stranger", "Stranger")

    with pytest.raises(ProjectResolutionError) as exc:
        resolve_project_input({"project": ["Stranger"]}, current, cfg)
    assert exc.value.status == 404


def test_serve_root_is_still_resolved_by_name(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_bytes(b"")
    current = _project(tmp_path / "projects" / "current", "Current")

    assert resolve_project_input({"project": ["Current"]}, current, cfg) == current

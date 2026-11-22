"""Tests: candidate project.yaml content can be validated without touching project.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from clio.config import load_config, validate_project_yaml

ORIGINAL = "analyze:\n  max_workers: 2\n"


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("config_version: V2\n", encoding="utf-8")
    project_dir = tmp_path / "trip"
    project_dir.mkdir()
    (project_dir / "project.yaml").write_text(ORIGINAL, encoding="utf-8")
    return config_path, project_dir


def test_valid_candidate_text_returns_config_and_leaves_file(project):
    config_path, project_dir = project

    config = validate_project_yaml("analyze:\n  max_workers: 5\n", project_dir, config_path=config_path)

    assert config.analyze.max_workers == 5
    assert (project_dir / "project.yaml").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.parametrize(
    "text",
    [
        "analyze:\n  max_workers: 0\n",
        "analyze: [unclosed\n",
        "- just\n- a list\n",
        "credentials:\n  x: {}\n",
    ],
)
def test_invalid_candidate_text_raises_value_error(project, text):
    config_path, project_dir = project

    with pytest.raises(ValueError):
        validate_project_yaml(text, project_dir, config_path=config_path)

    assert (project_dir / "project.yaml").read_text(encoding="utf-8") == ORIGINAL


def test_load_config_accepts_candidate_path(project, tmp_path: Path):
    config_path, project_dir = project
    candidate = tmp_path / "candidate.yaml"
    candidate.write_text("paths:\n  output_dir: ./out\nanalyze:\n  max_workers: 3\n", encoding="utf-8")

    config = load_config(config_path, project_dir, project_yaml_path=candidate)

    assert config.analyze.max_workers == 3
    # Relative paths resolve against the project dir, not the temp file's dir.
    assert config.project_cfg is not None
    assert config.project_cfg.paths.output_dir == (project_dir / "out").resolve()
    assert (project_dir / "project.yaml").read_text(encoding="utf-8") == ORIGINAL


def test_load_config_with_missing_candidate_path_fails(project, tmp_path: Path):
    config_path, project_dir = project

    with pytest.raises(FileNotFoundError):
        load_config(config_path, project_dir, project_yaml_path=tmp_path / "missing.yaml")

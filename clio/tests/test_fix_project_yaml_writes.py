"""Regression tests for project.yaml writes from the config and whisper routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from clio.ui.routes.config_routes import _write_project_yaml_validated
from clio.ui.routes.whisper_models import handle_put_whisper_model


def test_invalid_project_yaml_is_rolled_back_atomically(tmp_path: Path):
    proj_yaml = tmp_path / "project.yaml"
    proj_yaml.write_bytes(b"analyze:\n  skip_existing: true\n")

    with patch("clio.ui.routes.config_routes.load_config", side_effect=ValueError("bad")):
        error = _write_project_yaml_validated(tmp_path / "config.yaml", tmp_path, b"analyze: [broken\n")

    assert isinstance(error, ValueError)
    assert proj_yaml.read_bytes() == b"analyze:\n  skip_existing: true\n"
    assert (tmp_path / "project.yaml.bak").read_bytes() == b"analyze:\n  skip_existing: true\n"


def test_valid_project_yaml_is_kept(tmp_path: Path):
    with patch("clio.ui.routes.config_routes.load_config"):
        error = _write_project_yaml_validated(tmp_path / "config.yaml", tmp_path, b"plan: {}\n")
    assert error is None
    assert (tmp_path / "project.yaml").read_bytes() == b"plan: {}\n"


def test_whisper_model_switch_keeps_key_order_and_backup(tmp_path: Path):
    proj_yaml = tmp_path / "project.yaml"
    proj_yaml.write_text("plan: {}\nanalyze: {}\n", encoding="utf-8")
    handler = MagicMock()
    handler._resolve_project_dir.return_value = tmp_path
    handler.__class__._config_cache = MagicMock()

    handle_put_whisper_model(handler, {}, {"model_size": "medium"})

    raw = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
    assert list(raw) == ["plan", "analyze", "whisper"]
    assert raw["whisper"]["model_size"] == "medium"
    assert (tmp_path / "project.yaml.bak").is_file()

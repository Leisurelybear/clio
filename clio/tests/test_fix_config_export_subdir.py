"""Regression test: an unsupported export.output_subdir is a normal config error."""

from __future__ import annotations

from pathlib import Path

import pytest

from clio.config.loader import load_config


def test_custom_export_subdir_raises_value_error(tmp_path: Path):
    (tmp_path / "config.yaml").write_text("config_version: V2\n", encoding="utf-8")
    (tmp_path / "project.yaml").write_text("export:\n  output_subdir: final\n", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(tmp_path / "config.yaml")

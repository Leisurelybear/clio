"""Regression tests: V1->V2 migration never rewrites a file without a fresh backup."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from clio.config import loader
from clio.config.loader import _migrate_v1_to_v2

V1_CONFIG = "proxy:\n  enabled: false\ncompress:\n  fps: 30\n  target_size_mb: 10\n"


def test_existing_bak_is_not_reused(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(V1_CONFIG, encoding="utf-8")
    stale = tmp_path / "config.yaml.bak"
    stale.write_text("stale: snapshot\n", encoding="utf-8")

    _migrate_v1_to_v2(cfg_path)

    assert stale.read_text(encoding="utf-8") == "stale: snapshot\n"
    fresh = [p for p in tmp_path.iterdir() if p.name.startswith("config.yaml.bak.")]
    assert len(fresh) == 1
    assert fresh[0].read_text(encoding="utf-8") == V1_CONFIG
    assert yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["config_version"] == "V2"


def test_failed_config_backup_aborts_migration(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(V1_CONFIG, encoding="utf-8")

    def _fail(path):
        raise OSError("disk full")

    monkeypatch.setattr(loader, "_write_migration_backup", _fail)

    _migrate_v1_to_v2(cfg_path)

    assert cfg_path.read_text(encoding="utf-8") == V1_CONFIG
    assert not (tmp_path / "project.yaml").exists()


def test_failed_project_backup_keeps_project_yaml(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("proxy:\n  enabled: false\n", encoding="utf-8")
    project_dir = tmp_path / "trip"
    project_dir.mkdir()
    project_yaml = project_dir / "project.yaml"
    # Only global keys: a successful migration would remove this file.
    project_yaml.write_text("proxy:\n  enabled: true\n", encoding="utf-8")
    (tmp_path / "projects.json").write_text(
        json.dumps({"projects": [{"project_dir": str(project_dir)}]}), encoding="utf-8"
    )
    real_backup = loader._write_migration_backup

    def _flaky(path):
        if path.name == "project.yaml":
            raise OSError("read-only")
        return real_backup(path)

    monkeypatch.setattr(loader, "_write_migration_backup", _flaky)

    _migrate_v1_to_v2(cfg_path)

    assert project_yaml.read_text(encoding="utf-8") == "proxy:\n  enabled: true\n"

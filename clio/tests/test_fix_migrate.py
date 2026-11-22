"""Regression tests for migrate registry / target fixes."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from clio.tasks.migrate import run_migrate


def _legacy_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "GL010001.MP4").write_bytes(b"video1")
    (root / "project.yaml").write_text(
        yaml.dump({"paths": {"input_dir": ".", "output_dir": "./output"}}, allow_unicode=True),
        encoding="utf-8",
    )
    (root / "output").mkdir()
    return root


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")
    return config_path


class TestRegistry:
    def test_corrupt_registry_is_not_reset(self, tmp_path: Path) -> None:
        project = _legacy_project(tmp_path / "trip")
        config_path = _config(tmp_path)
        reg = tmp_path / "projects.json"
        reg.write_text('{"projects": [', encoding="utf-8")

        updated, errors = run_migrate(config_path, from_path=project)

        assert updated == 0
        assert errors
        assert reg.read_text(encoding="utf-8") == '{"projects": ['
        assert not (project / "videos.json").exists()

    def test_dict_entries_and_unknown_keys_survive(self, tmp_path: Path) -> None:
        project = _legacy_project(tmp_path / "trip")
        other = tmp_path / "other"
        other.mkdir()
        config_path = _config(tmp_path)
        reg = tmp_path / "projects.json"
        reg.write_text(
            json.dumps(
                {
                    "projects": [
                        {"name": "trip", "project_dir": str(project), "pinned": True},
                        {"name": "other", "project_dir": str(other), "color": "red"},
                    ],
                    "last_project": {"name": "other", "project_dir": str(other)},
                    "ui": {"sort": "recent"},
                }
            ),
            encoding="utf-8",
        )

        updated, errors = run_migrate(config_path, from_path=project)

        assert updated == 1, errors
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["ui"] == {"sort": "recent"}
        assert data["projects"][0]["pinned"] is True
        assert Path(data["projects"][0]["project_dir"]).resolve() == project.resolve()
        assert data["projects"][1] == {"name": "other", "project_dir": str(other), "color": "red"}
        assert data["last_project"] == {"name": "other", "project_dir": str(other)}

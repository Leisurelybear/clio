"""Regression tests: PUT /api/project is locked and only accepts UI session fields."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.routes import projects as projects_mod
from clio.ui.routes.projects import handle_put_project


def _handler(proj_dir: Path) -> MagicMock:
    handler = MagicMock()
    handler._resolve_project_dir.return_value = proj_dir
    handler.DEFAULT_PROJECT = {"currentDay": "day1", "source": "compressed", "name": proj_dir.name}
    handler.config_path = None
    return handler


def test_put_project_ignores_server_owned_fields(tmp_path: Path):
    proj_file = tmp_path / "project.json"
    proj_file.write_text(json.dumps({"name": "p", "output_dir": "/safe/out", "version": 2}), encoding="utf-8")

    with patch.object(projects_mod, "_save_last_project"):
        handle_put_project(
            _handler(tmp_path),
            {},
            {"currentDay": "day3", "output_dir": "/attacker", "version": 99, "createdAt": "x"},
        )

    data = json.loads(proj_file.read_text(encoding="utf-8"))
    assert data["currentDay"] == "day3"
    assert data["output_dir"] == "/safe/out"
    assert data["version"] == 2
    assert data.get("createdAt") != "x"


def test_concurrent_put_project_keeps_both_updates(tmp_path: Path):
    (tmp_path / "project.json").write_text(json.dumps({"name": "p"}), encoding="utf-8")
    real_load = projects_mod._load_project_json

    def slow_load(path):
        data = real_load(path)
        time.sleep(0.2)
        return data

    with patch.object(projects_mod, "_load_project_json", side_effect=slow_load), patch.object(
        projects_mod, "_save_last_project"
    ):
        threads = [
            threading.Thread(target=handle_put_project, args=(_handler(tmp_path), {}, {"currentDay": "day2"})),
            threading.Thread(target=handle_put_project, args=(_handler(tmp_path), {}, {"lastVideo": "a.mp4"})),
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=10)

    data = json.loads((tmp_path / "project.json").read_text(encoding="utf-8"))
    assert data["currentDay"] == "day2"
    assert data["lastVideo"] == "a.mp4"

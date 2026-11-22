"""Regression test: resolving the last project tolerates dict registry entries."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.services import project_service
from clio.ui.services.project_service import resolve_last_project_config


def test_legacy_name_lookup_handles_dict_entries(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n", encoding="utf-8")
    proj = tmp_path / "trip"
    proj.mkdir()
    (proj / "project.json").write_text(json.dumps({"name": "Trip"}), encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "project.json").write_text("[]", encoding="utf-8")  # non-object project.json
    registry = {
        "last_project": {"name": "Trip", "project_dir": str(tmp_path / "moved-away")},
        "projects": [{"project_dir": str(other)}, {"project_dir": str(proj)}],
    }
    default = MagicMock()
    loaded = MagicMock()

    with (
        patch.object(project_service, "_registry_path", return_value=tmp_path / "projects.json"),
        patch.object(project_service, "_read_registry", return_value=registry),
        patch.object(project_service, "load_config", return_value=loaded) as load,
    ):
        (tmp_path / "projects.json").write_text("{}", encoding="utf-8")
        result = resolve_last_project_config(default, config_path)

    assert result is loaded
    assert load.call_args.kwargs["project_dir"] == proj

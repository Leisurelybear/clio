"""Tests for clio/ui/routes/projects.py — project CRUD handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from clio.ui.routes.projects import (
    handle_get_project,
    handle_get_projects,
    handle_post_project_add,
    handle_post_project_create,
    handle_put_project,
)


class TestHandleGetProject:
    def test_returns_defaults_when_no_project_json(self):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = Path("/nonexistent")
        handler.DEFAULT_PROJECT = {"name": "Unnamed", "currentDay": "day1", "source": "compressed"}
        handler._send_json = MagicMock()

        handle_get_project(handler, {})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args[0][0]
        assert payload["name"] == "Unnamed"
        assert "steps" in payload

    def test_reads_project_json(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        (proj_dir / "project.json").write_text(json.dumps({"name": "Tokyo", "currentDay": "day3"}), encoding="utf-8")
        handler._resolve_project_dir.return_value = proj_dir
        handler.DEFAULT_PROJECT = {"name": "Unnamed", "currentDay": "day1", "source": "compressed"}
        handler._send_json = MagicMock()

        handle_get_project(handler, {})
        payload = handler._send_json.call_args[0][0]
        assert payload["name"] == "Tokyo"
        assert payload["currentDay"] == "day3"


class TestHandleGetProjects:
    def test_empty(self, tmp_path: Path):
        handler = MagicMock()
        handler.config_path = tmp_path / "config.yaml"
        handler.project_dir = tmp_path / "input"
        handler._send_json = MagicMock()
        handler.__class__._config_cache = MagicMock()

        handle_get_projects(handler, {})

        payload = handler._send_json.call_args[0][0]
        assert "projects" in payload
        assert "last_project" in payload

    def test_last_project_includes_project_dir(self, tmp_path: Path):
        """last_project must carry project_dir so auto-open cannot attach the wrong dir."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        handler.config_path = cfg
        handler.project_dir = tmp_path / "default"
        handler.project_dir.mkdir()
        handler._send_json = MagicMock()
        handler.__class__._config_cache = MagicMock()
        handler.__class__._config_cache.keys.return_value = []

        proj_a = tmp_path / "proj_a"
        proj_a.mkdir()
        (proj_a / "project.json").write_text(
            json.dumps({"name": "Alpha", "project_dir": str(proj_a)}), encoding="utf-8"
        )
        proj_b = tmp_path / "proj_b"
        proj_b.mkdir()
        (proj_b / "project.json").write_text(json.dumps({"name": "Beta", "project_dir": str(proj_b)}), encoding="utf-8")

        reg = tmp_path / "projects.json"
        reg.write_text(
            json.dumps(
                {
                    "projects": [str(proj_a), str(proj_b)],
                    "last_project": {"name": "Beta", "project_dir": str(proj_b)},
                }
            ),
            encoding="utf-8",
        )
        assert reg.is_file()

        handle_get_projects(handler, {})
        payload = handler._send_json.call_args[0][0]
        last = payload["last_project"]
        assert isinstance(last, dict)
        assert last["name"] == "Beta"
        assert last["project_dir"] == str(proj_b)


class TestHandlePutProject:
    def test_updates_project_json(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        handler._resolve_project_dir.return_value = proj_dir
        handler.config_path = cfg
        handler.DEFAULT_PROJECT = {"name": "Unnamed", "currentDay": "day1", "source": "compressed"}
        handler._send_json = MagicMock()

        handle_put_project(handler, {}, {"name": "Updated", "currentDay": "day2"})

        handler._send_json.assert_called_once_with({"ok": True})
        proj_file = proj_dir / "project.json"
        assert proj_file.is_file()
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        assert data["name"] == "Updated"
        assert data["currentDay"] == "day2"
        assert "updatedAt" in data


class TestHandlePostProjectCreate:
    def test_missing_name(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_post_project_create(handler, {})

        assert handler._send_json.call_args[0][1] == 400

    def test_missing_input_dir(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_post_project_create(handler, {"name": "Test"})

        assert handler._send_json.call_args[0][1] == 400

    def test_nonexistent_input_dir(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_post_project_create(handler, {"name": "Test", "input_dir": "/nonexistent"})

        assert handler._send_json.call_args[0][1] == 400

    def test_creates_project(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "new_project"
        proj_dir.mkdir()
        handler.config_path = tmp_path / "config.yaml"
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        handle_post_project_create(handler, {"name": "Paris", "input_dir": str(proj_dir)})

        assert handler._send_json.call_args[0][0]["ok"] is True
        proj_file = proj_dir / "project.json"
        assert proj_file.is_file()
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        assert data["name"] == "Paris"
        assert data["currentDay"] == "day1"

    def test_does_not_clobber_existing_project(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "existing"
        proj_dir.mkdir()
        (proj_dir / "project.json").write_text(json.dumps({"name": "Original"}), encoding="utf-8")
        handler.config_path = tmp_path / "config.yaml"
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        handle_post_project_create(handler, {"name": "Intruder", "project_dir": str(proj_dir)})

        assert handler._send_json.call_args[0][1] == 409
        data = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
        assert data["name"] == "Original"

    def test_aborts_registration_when_project_yaml_fails(self, tmp_path: Path):
        from unittest.mock import patch

        handler = MagicMock()
        proj_dir = tmp_path / "new_project"
        proj_dir.mkdir()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("paths: {}\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.projects._create_project_yaml", return_value=None):
            handle_post_project_create(handler, {"name": "Paris", "input_dir": str(proj_dir)})

        assert handler._send_json.call_args[0][1] == 500
        assert not (tmp_path / "projects.json").is_file()


class TestHandlePostProjectAdd:
    def test_missing_input_dir(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_post_project_add(handler, {})

        assert handler._send_json.call_args[0][1] == 400

    def test_adds_existing_project(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "existing"
        proj_dir.mkdir()
        (proj_dir / "project.json").write_text(json.dumps({"name": "Existing Project"}), encoding="utf-8")
        handler.config_path = tmp_path / "config.yaml"
        handler._send_json = MagicMock()

        handle_post_project_add(handler, {"input_dir": str(proj_dir)})

        assert handler._send_json.call_args[0][0]["ok"] is True

    def test_auto_creates_project_json(self, tmp_path: Path):
        """Adding a dir without project.json should auto-create it."""
        handler = MagicMock()
        proj_dir = tmp_path / "new_dir"
        proj_dir.mkdir()
        handler.config_path = tmp_path / "config.yaml"
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        handle_post_project_add(handler, {"input_dir": str(proj_dir)})

        assert handler._send_json.call_args[0][0]["ok"] is True
        proj_file = proj_dir / "project.json"
        assert proj_file.is_file()


class TestHandlePostProjectMigrate:
    def test_requires_project_dir(self, tmp_path: Path):
        from clio.ui.routes.projects import handle_post_project_migrate

        handler = MagicMock()
        handler.config_path = tmp_path / "config.yaml"
        handler.config_path.write_text("paths: {}\n", encoding="utf-8")
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_post_project_migrate(handler, {})
        assert handler._send_json.call_args[0][1] == 400

    def test_migrates_legacy_project(self, tmp_path: Path):
        import json

        import yaml

        from clio.ui.routes.projects import handle_post_project_migrate

        cfg = tmp_path / "config.yaml"
        cfg.write_text("paths: {}\n", encoding="utf-8")
        proj = tmp_path / "legacy"
        proj.mkdir()
        (proj / "A.mp4").write_bytes(b"x")
        (proj / "project.yaml").write_text(
            yaml.dump({"paths": {"input_dir": ".", "output_dir": "./output"}}, allow_unicode=True),
            encoding="utf-8",
        )
        (proj / "project.json").write_text(json.dumps({"name": "legacy"}), encoding="utf-8")

        handler = MagicMock()
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_post_project_migrate(handler, {"project_dir": str(proj)})
        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is True
        assert payload.get("migrated") is True
        assert (proj / "videos.json").is_file()


class TestProjectCreateVideosJson:
    def test_create_writes_videos_json(self, tmp_path: Path):
        import json

        from clio.ui.routes.projects import handle_post_project_create

        handler = MagicMock()
        proj = tmp_path / "newproj"
        proj.mkdir()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("paths: {}" + chr(10), encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_post_project_create(handler, {"name": "newproj", "project_dir": str(proj)})
        assert handler._send_json.call_args[0][0]["ok"] is True
        assert (proj / "videos.json").is_file()
        assert json.loads((proj / "videos.json").read_text(encoding="utf-8")) == []


class TestHandlePostProjectRemove:
    def _handler(self, tmp_path: Path) -> MagicMock:
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("paths: {}\n", encoding="utf-8")
        handler.config_path = cfg
        handler._send_json = MagicMock()
        return handler

    def _seed_registry(self, tmp_path: Path, *proj_dirs: Path) -> None:
        from clio.ui.services.project_service import _registry_path

        handler = self._handler(tmp_path)
        reg = _registry_path(handler.config_path)
        reg.write_text(
            json.dumps({"projects": [str(p.resolve()) for p in proj_dirs]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def test_ambiguous_name_returns_409(self, tmp_path: Path):
        """GAP-P2-13: same display name in two dirs must not mass-delete."""
        from clio.ui.routes.projects import handle_post_project_remove
        from clio.ui.services.project_service import _registry_path

        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        (a / "project.json").write_text(json.dumps({"name": "Twin"}), encoding="utf-8")
        (b / "project.json").write_text(json.dumps({"name": "Twin"}), encoding="utf-8")
        handler = self._handler(tmp_path)
        self._seed_registry(tmp_path, a, b)

        handle_post_project_remove(handler, {"name": "Twin"})

        payload, status = handler._send_json.call_args[0][0], handler._send_json.call_args[0][1]
        assert status == 409
        assert payload["ok"] is False
        assert payload.get("count", 0) >= 2 or len(payload.get("matches", [])) >= 2
        reg = json.loads(_registry_path(handler.config_path).read_text(encoding="utf-8"))
        assert len(reg["projects"]) == 2

    def test_unique_name_removes_one_and_reports_identity(self, tmp_path: Path):
        from clio.ui.routes.projects import handle_post_project_remove
        from clio.ui.services.project_service import _registry_path

        a = tmp_path / "solo"
        a.mkdir()
        (a / "project.json").write_text(json.dumps({"name": "Solo"}), encoding="utf-8")
        handler = self._handler(tmp_path)
        self._seed_registry(tmp_path, a)

        handle_post_project_remove(handler, {"name": "Solo"})

        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is True
        assert payload["removed_count"] == 1
        assert str(a.resolve()) in payload["removed"][0]["project_dir"]
        reg = json.loads(_registry_path(handler.config_path).read_text(encoding="utf-8"))
        assert reg["projects"] == []

    def test_project_dir_removes_exact_path(self, tmp_path: Path):
        from clio.ui.routes.projects import handle_post_project_remove
        from clio.ui.services.project_service import _registry_path

        a = tmp_path / "keep"
        b = tmp_path / "drop"
        a.mkdir()
        b.mkdir()
        (a / "project.json").write_text(json.dumps({"name": "Same"}), encoding="utf-8")
        (b / "project.json").write_text(json.dumps({"name": "Same"}), encoding="utf-8")
        handler = self._handler(tmp_path)
        self._seed_registry(tmp_path, a, b)

        handle_post_project_remove(handler, {"project_dir": str(b)})

        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is True
        assert payload["removed_count"] == 1
        reg = json.loads(_registry_path(handler.config_path).read_text(encoding="utf-8"))
        assert [str(Path(p).resolve()) for p in reg["projects"]] == [str(a.resolve())]

    def test_unknown_name_returns_404(self, tmp_path: Path):
        from clio.ui.routes.projects import handle_post_project_remove

        handler = self._handler(tmp_path)
        self._seed_registry(tmp_path)

        handle_post_project_remove(handler, {"name": "Missing"})

        payload, status = handler._send_json.call_args[0][0], handler._send_json.call_args[0][1]
        assert status == 404
        assert payload["ok"] is False

    def test_unknown_project_dir_returns_404(self, tmp_path: Path):
        from clio.ui.routes.projects import handle_post_project_remove

        handler = self._handler(tmp_path)
        self._seed_registry(tmp_path)

        handle_post_project_remove(handler, {"project_dir": str(tmp_path / "nope")})

        payload, status = handler._send_json.call_args[0][0], handler._send_json.call_args[0][1]
        assert status == 404
        assert payload["ok"] is False

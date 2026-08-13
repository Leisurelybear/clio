"""Tests for clio/ui/services/project_service.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clio.ui.services.project_service import (
    ProjectResolutionError,
    _add_to_registry,
    _detect_steps,
    _list_projects,
    _project_output_dir,
    _registry_path,
    _save_last_project,
    resolve_project_input,
)


class TestProjectOutputDir:
    def test_default_output(self, tmp_path: Path):
        result = _project_output_dir(tmp_path)
        expected = (tmp_path / "output").resolve()
        assert result == expected

    def test_from_project_json_absolute(self, tmp_path: Path):
        proj_file = tmp_path / "project.json"
        abs_dir = Path("/").resolve() / "custom_out"
        proj_file.write_text(json.dumps({"output_dir": str(abs_dir)}), encoding="utf-8")
        result = _project_output_dir(tmp_path)
        assert result == abs_dir

    def test_from_project_json_relative(self, tmp_path: Path):
        proj_file = tmp_path / "project.json"
        proj_file.write_text(json.dumps({"output_dir": "custom_out"}), encoding="utf-8")
        result = _project_output_dir(tmp_path)
        assert result == (tmp_path / "custom_out").resolve()

    def test_corrupted_json_falls_back(self, tmp_path: Path):
        proj_file = tmp_path / "project.json"
        proj_file.write_text("{invalid", encoding="utf-8")
        result = _project_output_dir(tmp_path)
        assert result == (tmp_path / "output").resolve()


class TestRegistryPath:
    def test_with_config(self, tmp_path: Path):
        cfg = tmp_path / "sub" / "config.yaml"
        result = _registry_path(cfg)
        assert result == tmp_path / "sub" / "projects.json"

    def test_without_config(self):
        result = _registry_path(None)
        assert result == Path("projects.json")


class TestAddToRegistry:
    def test_creates_new_registry(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_bytes(b"")
        _add_to_registry(str(tmp_path / "projects" / "Paris"), cfg)
        reg = tmp_path / "projects.json"
        assert reg.is_file()
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert "Paris" in data["projects"][0]
        assert data.get("last_project") is None  # not set on first add

    def test_appends_to_existing(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        _add_to_registry(str(tmp_path / "proj1"), cfg)
        _add_to_registry(str(tmp_path / "proj2"), cfg)
        reg = tmp_path / "projects.json"
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert len(data["projects"]) == 2

    def test_does_not_duplicate(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        _add_to_registry(str(tmp_path / "proj"), cfg)
        _add_to_registry(str(tmp_path / "proj"), cfg)
        reg = tmp_path / "projects.json"
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert len(data["projects"]) == 1

    def test_preserves_last_project(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        reg = tmp_path / "projects.json"
        reg.write_text(json.dumps({"projects": [], "last_project": "paris"}), encoding="utf-8")
        _add_to_registry(str(tmp_path / "tokyo"), cfg)
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["last_project"] == "paris"

    def test_corrupt_registry_refuses_overwrite(self, tmp_path: Path):
        """GAP-P2-02: corrupt projects.json must not be treated as empty and saved."""
        import pytest

        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        reg = tmp_path / "projects.json"
        good_bak = tmp_path / "projects.json.bak"
        reg.write_text("{not-json", encoding="utf-8")
        good_bak.write_text(json.dumps({"projects": [str(tmp_path / "keep")]}), encoding="utf-8")
        with pytest.raises(ValueError, match="损坏"):
            _add_to_registry(str(tmp_path / "new"), cfg)
        assert reg.read_text(encoding="utf-8") == "{not-json"
        assert "keep" in good_bak.read_text(encoding="utf-8")
        assert any(p.name.startswith("projects.json.corrupt.") for p in tmp_path.iterdir())


class TestSaveLastProject:
    def test_saves_last_project(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        _save_last_project("paris", cfg)
        reg = tmp_path / "projects.json"
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["last_project"] == "paris"
        assert data["projects"] == []


class TestDetectSteps:
    def test_all_false_when_no_output(self, tmp_path: Path):
        steps = _detect_steps(tmp_path / "nonexistent")
        for k in ("compress", "analyze", "scripts", "plan", "label", "cut"):
            assert steps[k] is False

    def test_all_false_empty_output(self, tmp_path: Path):
        out = tmp_path / "output"
        out.mkdir()
        steps = _detect_steps(out)
        for k in ("compress", "analyze", "scripts", "plan", "label", "cut"):
            assert steps[k] is False

    def test_compress_detected(self, tmp_path: Path):
        out = tmp_path / "output"
        comp = out / "compressed"
        comp.mkdir(parents=True)
        (comp / "001_test.mp4").write_bytes(b"")
        steps = _detect_steps(out)
        assert steps["compress"] is True
        assert steps["analyze"] is False

    def test_analyze_detected(self, tmp_path: Path):
        out = tmp_path / "output"
        texts = out / "texts"
        texts.mkdir(parents=True)
        (texts / "001_test.json").write_bytes(b"{}")
        steps = _detect_steps(out)
        assert steps["analyze"] is True

    def test_scripts_detected(self, tmp_path: Path):
        out = tmp_path / "output"
        scripts = out / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "001_test.json").write_bytes(b"{}")
        steps = _detect_steps(out)
        assert steps["scripts"] is True

    def test_plans_detected(self, tmp_path: Path):
        out = tmp_path / "output"
        plans = out / "plans"
        plans.mkdir(parents=True)
        (plans / "day1_plan.json").write_bytes(b"{}")
        steps = _detect_steps(out)
        assert steps["plan"] is True

    def test_custom_subdirs_from_project_yaml(self, tmp_path: Path):
        proj = tmp_path / "proj"
        out = proj / "output"
        plans = out / "storyboard"
        plans.mkdir(parents=True)
        (plans / "day1_plan.json").write_bytes(b"{}")
        (proj / "project.yaml").write_text(
            "plan:\n  plans_subdir: storyboard\n",
            encoding="utf-8",
        )
        steps = _detect_steps(out, project_dir=proj)
        assert steps["plan"] is True
        assert _detect_steps(out)["plan"] is False

    def test_label_detected(self, tmp_path: Path):
        out = tmp_path / "output"
        labeled = out / "labeled"
        labeled.mkdir(parents=True)
        (labeled / "labels.json").write_bytes(b"{}")
        steps = _detect_steps(out)
        assert steps["label"] is True

    def test_cut_detected(self, tmp_path: Path):
        out = tmp_path / "output"
        cuts = out / "cuts"
        cuts.mkdir(parents=True)
        (cuts / "day1" / "clip.mp4").mkdir(parents=True)
        steps = _detect_steps(out)
        assert steps["cut"] is True


class TestListProjects:
    def test_no_registry_no_siblings_fallback(self, tmp_path: Path):
        """With no registry and no sibling projects, fallback to input_dir."""
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        projects = _list_projects(cfg, input_dir)
        assert len(projects) == 0

    def test_from_registry(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        reg = tmp_path / "projects.json"
        proj_dir = tmp_path / "paris"
        proj_dir.mkdir()
        (proj_dir / "project.json").write_text(json.dumps({"name": "Paris Trip"}), encoding="utf-8")
        reg.write_text(json.dumps({"projects": [str(proj_dir.resolve())]}), encoding="utf-8")
        projects = _list_projects(cfg, tmp_path / "other")
        names = [p["name"] for p in projects]
        assert "Paris Trip" in names

    def test_does_not_auto_discover_sibling(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        projects_root = tmp_path / "projects"
        projects_root.mkdir()
        tokyo = projects_root / "tokyo"
        tokyo.mkdir()
        (tokyo / "project.json").write_text(json.dumps({"name": "Tokyo"}), encoding="utf-8")
        input_dir = projects_root / "current"
        input_dir.mkdir()
        (input_dir / "project.json").write_text(json.dumps({"name": "Current"}), encoding="utf-8")
        reg = tmp_path / "projects.json"
        reg.write_text(json.dumps({"projects": [str(input_dir.resolve())]}), encoding="utf-8")
        projects = _list_projects(cfg, input_dir)
        names = [p["name"] for p in projects]
        assert "Tokyo" not in names
        assert "Current" in names


class TestResolveProjectInput:
    def test_rejects_unregistered_input_dir_query(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        default_input = tmp_path / "default"
        default_input.mkdir()
        arbitrary = tmp_path / "arbitrary"
        arbitrary.mkdir()

        with pytest.raises(ProjectResolutionError) as exc:
            resolve_project_input({"input_dir": [str(arbitrary)]}, default_input, cfg)
        assert exc.value.status == 404

    def test_accepts_registered_input_dir_query(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        default_input = tmp_path / "default"
        default_input.mkdir()
        registered = tmp_path / "registered"
        registered.mkdir()
        _add_to_registry(str(registered), cfg)

        result = resolve_project_input({"input_dir": [str(registered)]}, default_input, cfg)

        assert result == registered


def test_collect_allowed_includes_default_and_registry(tmp_path):
    from clio.ui.services.project_service import collect_allowed_project_paths, is_under_root

    default = tmp_path / "default"
    default.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": [str(other)]}), encoding="utf-8")
    # config_path parent holds projects.json
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("{}", encoding="utf-8")
    allowed = collect_allowed_project_paths(default, cfg_path)
    assert str(default.resolve()) in allowed
    assert str(other.resolve()) in allowed
    assert is_under_root(other / "nested", other) is True
    assert is_under_root(default, other) is False

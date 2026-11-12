"""Tests for clio.tasks.migrate — legacy project → videos.json migration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from clio.tasks.migrate import _migrate_one, run_migrate


def _write_legacy_project(root: Path, name: str = "trip1") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "GL010001.MP4").write_bytes(b"video1")
    (root / "GL010002.MP4").write_bytes(b"video2")
    (root / "project.yaml").write_text(
        yaml.dump(
            {
                "paths": {
                    "input_dir": ".",
                    "output_dir": "./output",
                    "recursive": True,
                },
                "ai": {"context": ""},
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (root / "project.json").write_text(
        json.dumps({"name": name, "output_dir": str(root / "output"), "currentDay": "day1"}),
        encoding="utf-8",
    )
    return root


def test_migrate_in_place(tmp_path: Path) -> None:
    project = _write_legacy_project(tmp_path / "trip1")
    (project / "output" / "compressed").mkdir(parents=True)
    (project / "output" / "compressed" / "001_GL010001.mp4").write_bytes(b"c")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")
    reg = tmp_path / "projects.json"
    reg.write_text(json.dumps({"projects": [str(project)], "last_project": str(project)}), encoding="utf-8")

    updated, errors = run_migrate(config_path, from_path=project)
    assert updated == 1, errors
    assert (project / "videos.json").is_file()
    videos = json.loads((project / "videos.json").read_text(encoding="utf-8"))
    assert len(videos) == 2
    raw = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    assert "input_dir" not in (raw.get("paths") or {})
    assert "recursive" not in (raw.get("paths") or {})
    # output_dir stored absolute so artifacts remain reachable
    assert Path(raw["paths"]["output_dir"]).is_absolute()
    assert Path(raw["paths"]["output_dir"]) == (project / "output").resolve()
    assert (project / "project.yaml.migrate-bak").is_file()
    data = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert data.get("version") == 2
    # last_project path string updated to dict with project_dir
    reg_data = json.loads(reg.read_text(encoding="utf-8"))
    last = reg_data["last_project"]
    assert isinstance(last, dict)
    assert Path(last["project_dir"]).resolve() == project.resolve()


def test_migrate_skips_already_new(tmp_path: Path) -> None:
    project = tmp_path / "newproj"
    project.mkdir()
    (project / "project.yaml").write_text(
        yaml.dump({"paths": {"output_dir": "./output"}}, allow_unicode=True),
        encoding="utf-8",
    )
    (project / "videos.json").write_text("[]", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")

    updated, _errors = run_migrate(config_path, from_path=project)
    assert updated == 0


def test_migrate_preserves_curated_videos_json(tmp_path: Path) -> None:
    project = _write_legacy_project(tmp_path / "trip2", name="trip2")
    external = tmp_path / "ext" / "only.mp4"
    external.parent.mkdir()
    external.write_bytes(b"x")
    # curated list already present; should not be overwritten by scan of project dir
    (project / "videos.json").write_text(json.dumps([str(external.resolve())]), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")

    updated, errors = run_migrate(config_path, from_path=project)
    assert updated == 1, errors
    videos = json.loads((project / "videos.json").read_text(encoding="utf-8"))
    assert videos == [str(external.resolve())]


def test_migrate_external_input_keeps_absolute_output(tmp_path: Path) -> None:
    """When input_dir points outside project, still keep absolute output_dir."""
    media = tmp_path / "media"
    media.mkdir()
    (media / "A.mp4").write_bytes(b"a")
    project = tmp_path / "proj"
    project.mkdir()
    out = project / "output"
    out.mkdir()
    (out / "compressed").mkdir()
    (out / "compressed" / "001_A.mp4").write_bytes(b"c")
    (project / "project.yaml").write_text(
        yaml.dump(
            {
                "paths": {
                    "input_dir": str(media),
                    "output_dir": "./output",
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (project / "project.json").write_text(json.dumps({"name": "ext"}), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")

    updated, errors = run_migrate(config_path, from_path=project)
    assert updated == 1, errors
    # in-place because output exists
    assert (project / "videos.json").is_file()
    raw = yaml.safe_load((project / "project.yaml").read_text(encoding="utf-8"))
    assert Path(raw["paths"]["output_dir"]).resolve() == out.resolve()
    videos = json.loads((project / "videos.json").read_text(encoding="utf-8"))
    assert any(Path(v).name == "A.mp4" for v in videos)


def test_migrate_reports_non_object_registry(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")
    (tmp_path / "projects.json").write_text("[]", encoding="utf-8")

    updated, errors = run_migrate(config_path)

    assert updated == 0
    assert errors


def test_migrate_reports_non_object_project_yaml(tmp_path: Path) -> None:
    project = tmp_path / "bad-project"
    project.mkdir()
    (project / "project.yaml").write_text("- not-an-object\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")

    updated, errors = run_migrate(config_path, from_path=project)

    assert updated == 0
    assert errors


def test_migrate_propagates_unexpected_yaml_parser_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _write_legacy_project(tmp_path / "trip1")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("paths: {}\n", encoding="utf-8")

    def fail_parse(_stream: object) -> None:
        raise RuntimeError("unexpected parser failure")

    monkeypatch.setattr("clio.tasks.migrate.yaml.safe_load", fail_parse)

    with pytest.raises(RuntimeError, match="unexpected parser failure"):
        _migrate_one(project, config_path, tmp_path / "projects.json")

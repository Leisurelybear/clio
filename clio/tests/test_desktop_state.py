# clio/tests/test_desktop_state.py
from pathlib import Path

from clio.desktop.state import load_last_dir, resolve_initial_dir, save_last_dir, state_path


def test_state_path(tmp_path: Path):
    assert state_path(tmp_path) == tmp_path / "desktop-state.json"


def test_save_and_load_last_dir(tmp_path: Path):
    d = tmp_path / "media"
    d.mkdir()
    save_last_dir(tmp_path, str(d))
    assert load_last_dir(tmp_path) == str(d.resolve())


def test_save_file_stores_parent(tmp_path: Path):
    d = tmp_path / "media"
    d.mkdir()
    f = d / "a.mp4"
    f.write_bytes(b"x")
    save_last_dir(tmp_path, str(f), is_file=True)
    assert load_last_dir(tmp_path) == str(d.resolve())


def test_resolve_prefers_existing_preferred(tmp_path: Path):
    pref = tmp_path / "project"
    pref.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    save_last_dir(tmp_path, str(other))
    assert resolve_initial_dir(tmp_path, str(pref)) == str(pref.resolve())


def test_resolve_falls_back_to_last(tmp_path: Path):
    other = tmp_path / "other"
    other.mkdir()
    save_last_dir(tmp_path, str(other))
    assert resolve_initial_dir(tmp_path, str(tmp_path / "missing")) == str(other.resolve())


def test_resolve_relative_path_against_explicit_base(tmp_path: Path):
    project_dir = tmp_path / "project"
    output_dir = project_dir / "output"
    output_dir.mkdir(parents=True)
    assert resolve_initial_dir(tmp_path, "./output", base_dir=project_dir) == str(output_dir.resolve())


def test_resolve_file_path_to_parent(tmp_path: Path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    file_path = media_dir / "clip.mp4"
    file_path.write_bytes(b"x")
    assert resolve_initial_dir(tmp_path, str(file_path)) == str(media_dir.resolve())

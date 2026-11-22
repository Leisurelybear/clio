"""Regression tests: texts/scripts routes honour configured artifact subdirs."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from clio.ui.routes.texts import _resolve_scripts_artifact, _resolve_texts_artifact, handle_get_texts
from clio.ui.services.file_service import resolve_in, resolve_texts


def _handler(proj_out: Path, texts_subdir: str = "analysis", scripts_subdir: str = "vo") -> MagicMock:
    handler = MagicMock()
    handler._resolve_project_dir.return_value = proj_out
    handler._get_project_output.return_value = proj_out
    handler._get_config.return_value = SimpleNamespace(
        analyze=SimpleNamespace(texts_subdir=texts_subdir),
        script=SimpleNamespace(scripts_subdir=scripts_subdir),
    )
    handler._resolve_texts.side_effect = lambda name, out, preferred_subdir=None: resolve_texts(
        name, out, out, preferred_subdir
    )
    handler._resolve_in.side_effect = lambda sub, name, out: resolve_in(sub, name, out, out)
    return handler


def test_resolve_texts_uses_preferred_subdir(tmp_path: Path):
    (tmp_path / "analysis").mkdir()
    target = tmp_path / "analysis" / "001_a.json"
    target.write_text("{}", encoding="utf-8")
    assert resolve_texts("001_a.json", tmp_path, tmp_path) is None
    assert resolve_texts("001_a.json", tmp_path, tmp_path, "analysis") == target


def test_routes_find_custom_texts_and_scripts_dirs(tmp_path: Path):
    (tmp_path / "analysis").mkdir()
    (tmp_path / "vo").mkdir()
    text = tmp_path / "analysis" / "001_a.json"
    text.write_text(json.dumps({"title": "x"}), encoding="utf-8")
    script = tmp_path / "vo" / "001_a_voiceover.json"
    script.write_text("{}", encoding="utf-8")
    handler = _handler(tmp_path)

    assert _resolve_texts_artifact(handler, {}, "001_a.json") == text
    assert _resolve_scripts_artifact(handler, {}, "001_a_voiceover.json") == script

    handle_get_texts(handler, {"file": ["001_a.json"]})
    handler.send_error.assert_not_called()
    assert handler._send_bytes.call_args.args[0] == text.read_bytes()


def test_default_subdirs_still_work(tmp_path: Path):
    (tmp_path / "texts").mkdir()
    (tmp_path / "scripts").mkdir()
    text = tmp_path / "texts" / "001_a.json"
    text.write_text("{}", encoding="utf-8")
    script = tmp_path / "scripts" / "001_a_voiceover.json"
    script.write_text("{}", encoding="utf-8")
    handler = _handler(tmp_path, texts_subdir="texts", scripts_subdir="scripts")

    assert _resolve_texts_artifact(handler, {}, "001_a.json") == text
    assert _resolve_scripts_artifact(handler, {}, "001_a_voiceover.json") == script

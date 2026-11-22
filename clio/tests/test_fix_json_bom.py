"""Regression tests: JSON written with a UTF-8 BOM (Windows Notepad) is accepted (A-06)."""

from __future__ import annotations

import json

from clio.evaluation import _read_json_object
from clio.plan_readiness import _load_json_dict
from clio.run_scope import _artifact_from_path


def _write_bom_json(path, data):
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data).encode("utf-8"))
    return path


def test_run_scope_artifact_reads_bom_json(tmp_path):
    path = _write_bom_json(tmp_path / "001_clip.json", {"source_file": "clip.mp4", "title": "t"})

    artifact = _artifact_from_path(path, set())

    assert artifact.data.get("title") == "t"
    assert artifact.stem_key == "stem:clip"


def test_plan_readiness_reads_bom_json(tmp_path):
    path = _write_bom_json(tmp_path / "001_clip.json", {"index": "001"})

    assert _load_json_dict(path) == {"index": "001"}


def test_evaluation_reads_bom_json(tmp_path):
    path = _write_bom_json(tmp_path / "manifest.json", {"suite": "s"})

    assert _read_json_object(path, label="manifest") == {"suite": "s"}

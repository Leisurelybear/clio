"""Regression tests for A-06b: user-edited JSON saved with a UTF-8 BOM still loads."""

from __future__ import annotations

import json
from pathlib import Path

from clio.tasks.plan import _read_json_object
from clio.tasks.transcript_align import _load_transcript


def _write_bom_json(path: Path, data: dict) -> Path:
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False).encode("utf-8"))
    return path


def test_plan_reader_accepts_bom(tmp_path: Path) -> None:
    path = _write_bom_json(tmp_path / "001_clip.json", {"title": "标题", "summary": "x"})
    data, error = _read_json_object(path)
    assert error is None
    assert data is not None and data["title"] == "标题"


def test_transcript_loader_accepts_bom(tmp_path: Path) -> None:
    path = _write_bom_json(tmp_path / "001_clip_transcript.json", {"segments": []})
    assert _load_transcript(path) == {"segments": []}

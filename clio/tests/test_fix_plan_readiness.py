"""Regression tests for plan readiness collectors (A-02 / A-03 / A-04)."""

from __future__ import annotations

from types import SimpleNamespace

from clio.plan_readiness import collect_project_indices


def test_collect_project_indices_skips_non_utf8_and_non_object_texts(tmp_path):
    comp = tmp_path / "compressed"
    comp.mkdir()
    (comp / "001_CLIP.mp4").write_bytes(b"x")
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "001_gbk.json").write_bytes('{"index": "001", "title": "海边"}'.encode("gbk"))
    (texts / "002_list.json").write_text("[1, 2, 3]", encoding="utf-8")
    cfg = SimpleNamespace(compressed_dir=comp, texts_dir=texts, project_dir=None)

    known, offline = collect_project_indices(cfg)

    assert "001" in known
    assert offline == set()

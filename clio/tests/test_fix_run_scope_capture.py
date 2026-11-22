"""Regression tests: run-scope capture resolution lists compressed/ once (A-15)."""

from __future__ import annotations

from types import SimpleNamespace

import clio.run_scope as run_scope


def _artifact(tmp_path, stem: str) -> run_scope._Artifact:
    return run_scope._Artifact(
        path=tmp_path / f"{stem}.json",
        data={},
        identity=None,
        identity_key=f"stem:{stem}",
        stem_key=f"stem:{stem}",
        compressed_stem=stem,
        usable=True,
        legacy_labels=set(),
    )


def test_resolve_capture_uses_shared_compressed_lookup(tmp_path, monkeypatch):
    clip = tmp_path / "001_Clip.mp4"
    clip.write_bytes(b"x")
    probed: list = []

    def no_directory_scan(*_args, **_kwargs):
        raise AssertionError("compressed/ must not be re-listed per artifact")

    monkeypatch.setattr(run_scope, "_compressed_files", no_directory_scan)
    monkeypatch.setattr(run_scope, "resolve_binary", lambda *_a: "ffprobe")
    monkeypatch.setattr(run_scope, "_probe_creation_time_text", lambda path, _ff: probed.append(path) or "")
    calls = {"n": 0}

    def lookup():
        calls["n"] += 1
        return {"001_clip": clip}

    config = SimpleNamespace(paths=SimpleNamespace(ffprobe=""))
    run_scope._resolve_capture(_artifact(tmp_path, "001_Clip"), config, lookup)

    assert calls["n"] == 1
    assert probed == [clip]

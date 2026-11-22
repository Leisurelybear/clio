"""Regression tests for plan readiness collectors (A-02 / A-03 / A-04)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

from clio.plan_readiness import collect_project_indices, collect_project_media_durations


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


def _durations_cfg(compressed):
    return SimpleNamespace(
        compressed_dir=compressed,
        project_dir=None,
        naming=SimpleNamespace(index_width=3),
        paths=SimpleNamespace(ffprobe=""),
    )


def test_collect_project_media_durations_survives_ffprobe_failure(tmp_path, monkeypatch):
    compressed = tmp_path / "compressed"
    compressed.mkdir()
    (compressed / "001_broken.mp4").write_bytes(b"x")
    (compressed / "002_ok.mp4").write_bytes(b"x")

    def fake_duration(path, _ffprobe):
        if path.name.startswith("001"):
            raise subprocess.CalledProcessError(1, ["ffprobe"])
        return 8.0

    monkeypatch.setattr("clio.plan_readiness.resolve_binary", lambda *_args: "ffprobe")
    monkeypatch.setattr("clio.plan_readiness.get_duration_sec", fake_duration)

    assert collect_project_media_durations(_durations_cfg(compressed)) == {"002": 8.0}


def test_collect_project_media_durations_keeps_vmeta_when_ffprobe_missing(tmp_path, monkeypatch):
    compressed = tmp_path / "compressed"
    compressed.mkdir()
    (compressed / "001_old.mp4").write_bytes(b"x")  # no .vmeta -> needs ffprobe
    clip = compressed / "002_new.mp4"
    clip.write_bytes(b"x")
    clip.with_suffix(".vmeta").write_text(
        json.dumps(
            {
                "source_path": str(tmp_path / "new.mp4"),
                "target_path": clip.name,
                "source_modifyTime": 0,
                "source_size": 1,
                "target_modifyTime": 0,
                "target_size": 1,
                "source_duration_sec": 12.0,
                "target_duration_sec": 10.0,
            }
        ),
        encoding="utf-8",
    )

    def missing_ffprobe(*_args):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("clio.plan_readiness.resolve_binary", missing_ffprobe)
    # Make iteration order deterministic: the clip needing ffprobe comes first.
    real_iterdir = type(compressed).iterdir
    monkeypatch.setattr(
        type(compressed),
        "iterdir",
        lambda self: iter(sorted(real_iterdir(self))),
    )

    assert collect_project_media_durations(_durations_cfg(compressed)) == {"002": 10.0}


def test_collectors_reuse_precomputed_media_aliases(tmp_path, monkeypatch):
    compressed = tmp_path / "compressed"
    compressed.mkdir()
    (compressed / "002_new.mp4").write_bytes(b"x")
    texts = tmp_path / "texts"
    texts.mkdir()

    def rescan(_cfg):
        raise AssertionError("aliases must not be recomputed when passed in")

    monkeypatch.setattr("clio.plan_readiness.collect_media_index_aliases", rescan)
    monkeypatch.setattr("clio.plan_readiness.resolve_binary", lambda *_args: "ffprobe")
    monkeypatch.setattr("clio.plan_readiness.get_duration_sec", lambda *_args: 6.0)
    cfg = SimpleNamespace(
        compressed_dir=compressed,
        texts_dir=texts,
        project_dir=None,
        naming=SimpleNamespace(index_width=3),
        paths=SimpleNamespace(ffprobe=""),
    )
    aliases = {"001": {"002"}}

    known, _offline = collect_project_indices(cfg, media_aliases=aliases)
    durations = collect_project_media_durations(cfg, media_aliases=aliases)

    assert "001" in known and "002" in known
    assert durations == {"002": 6.0, "001": 6.0}

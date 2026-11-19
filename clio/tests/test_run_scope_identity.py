"""Capture-day scope identity regressions.

Phase 1 of the capture-day scope plan
(``docs/superpowers/plans/2026-11-19-capture-day-scopes-plan.md``) reproduces the
reference-project failures as explicit regressions. The ``xfail(strict=True)``
markers below record behaviour that the current production code gets wrong; each
marker is removed by the phase that fixes it:

* resolver/deduplication and greenfield scope resolution -- Phase 2
* analysis scope metadata, manifest, interrupted-analysis -- Phase 3
* preview/start/completion loop -- Phase 8

The fixtures are synthetic and mirror the reference project shape: one compressed
clip per original identity, but two analysis JSON files per original because the
project was analysed once and then re-numbered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from clio.config.models import (
    AnalyzeConfig,
    AppConfig,
    GlobalConfig,
    GlobalPathsConfig,
    NamingConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProjectWhisperConfig,
    ScriptConfig,
)
from clio.tasks.plan import discover_day_labels
from clio.ui.services.run_preview import build_run_preview

UI_STEPS = ["compress", "transcribe", "analyze", "consistency", "voiceover", "plan", "plan_voiceover", "label"]


def _config(project_dir: Path, output_dir: Path) -> AppConfig:
    return AppConfig(
        global_cfg=GlobalConfig(paths=GlobalPathsConfig(), naming=NamingConfig(index_width=3)),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=output_dir),
            analyze=AnalyzeConfig(compressed_subdir="compressed", texts_subdir="texts", skip_existing=True),
            script=ScriptConfig(scripts_subdir="scripts"),
            whisper=ProjectWhisperConfig(transcripts_subdir="transcripts"),
        ),
        project_dir=project_dir,
    )


def _write_analysis(texts_dir: Path, filename: str, data: dict[str, Any]) -> Path:
    texts_dir.mkdir(parents=True, exist_ok=True)
    path = texts_dir / filename
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _analysis_payload(
    *,
    index: str,
    original_stem: str,
    compressed_stem: str,
    day_label: str | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "index": int(index),
        "source_file": f"{original_stem}.MP4",
        "compressed_file": f"{compressed_stem}.mp4",
        "media_identity": {
            "original_stem": original_stem,
            "compressed_stem": compressed_stem,
            "index": index,
        },
        "clip_summary": "clip",
    }
    if day_label is not None:
        data["day_label"] = day_label
    return data


def _row(preview: dict[str, Any], name: str) -> dict[str, Any]:
    return next(step for step in preview["steps"] if step["name"] == name)


def _duplicate_identity_project(tmp_path: Path) -> AppConfig:
    """One original clip that was analysed twice under two different indexes.

    ``001_clip`` is the stale analysis: it points at ``001_clip.mp4`` which no
    longer exists. ``182_clip`` is the current analysis backed by the compressed
    file that is actually on disk. Both share the ``clip`` original identity.
    """
    config = _config(tmp_path, tmp_path / "output")
    config.compressed_dir.mkdir(parents=True)
    (config.compressed_dir / "182_clip.mp4").write_bytes(b"compressed")
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip", day_label="day1"),
    )
    _write_analysis(
        config.texts_dir,
        "182_clip.json",
        _analysis_payload(index="182", original_stem="clip", compressed_stem="182_clip", day_label="day1"),
    )
    return config


@pytest.mark.xfail(
    strict=True,
    reason="Phase 2: preview counts must deduplicate duplicate analysis identities (currently 2, want 1)",
)
def test_preview_counts_deduplicate_media_identity(tmp_path: Path) -> None:
    config = _duplicate_identity_project(tmp_path)

    preview = build_run_preview(config, UI_STEPS, force=False, use_transcripts=True)

    label = _row(preview, "label")
    voiceover = _row(preview, "voiceover")
    assert label["total"] == 1
    assert voiceover["total"] == 1


@pytest.mark.xfail(
    strict=True,
    reason="Phase 3: an analysis without day_label must not be assumed day1",
)
def test_missing_day_label_does_not_default_to_day1(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip"),
    )

    labels = discover_day_labels(config)

    assert "day1" not in labels


@pytest.mark.xfail(
    strict=True,
    reason="Phase 2: a greenfield clip with a persisted capture date must not become day1",
)
def test_persisted_capture_date_is_not_collapsed_to_day1(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    data = _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip")
    data["capture_date"] = "2026-10-01"
    _write_analysis(config.texts_dir, "001_clip.json", data)

    labels = discover_day_labels(config)

    assert labels == ["2026-10-01"]


def test_existing_legacy_labels_are_preserved(tmp_path: Path) -> None:
    """Guarding invariant: legacy day labels are never collapsed while fixing the rest."""
    config = _config(tmp_path, tmp_path / "output")
    _write_analysis(
        config.texts_dir,
        "001_a.json",
        _analysis_payload(index="001", original_stem="a", compressed_stem="001_a", day_label="day1"),
    )
    _write_analysis(
        config.texts_dir,
        "002_b.json",
        _analysis_payload(index="002", original_stem="b", compressed_stem="002_b", day_label="day3"),
    )

    assert discover_day_labels(config) == ["day1", "day3"]

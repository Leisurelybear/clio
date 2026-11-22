"""Capture-day scope identity regressions.

Phase 1 of the capture-day scope plan
(``docs/superpowers/plans/2026-11-19-capture-day-scopes-plan.md``) reproduces the
reference-project failures as explicit regressions. Phase 2 enables the resolver,
deduplication, and greenfield scope tests while later-phase metadata and completion
regressions remain marked ``xfail(strict=True)``.

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
from clio.run_scope import resolve_run_scope
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


def _write_compressed(config: AppConfig, stem: str) -> Path:
    config.compressed_dir.mkdir(parents=True, exist_ok=True)
    path = config.compressed_dir / f"{stem}.mp4"
    path.write_bytes(b"compressed")
    return path


def _analysis_payload(
    *,
    index: str,
    original_stem: str,
    compressed_stem: str,
    day_label: str | None = None,
    capture_date: str | None = None,
    original_path: str = "",
    scope_id: str | None = None,
    schema_version: int | None = None,
    structured_lineage: bool = False,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "index": int(index),
        "source_file": f"{original_stem}.MP4",
        "compressed_file": f"{compressed_stem}.mp4",
        "media_identity": {
            "original_stem": original_stem,
            "original_path": original_path,
            "compressed_stem": compressed_stem,
            "index": index,
        },
        "clip_summary": "clip",
    }
    if day_label is not None:
        data["day_label"] = day_label
    if capture_date is not None:
        data["capture_date"] = capture_date
        data["capture_date_source"] = "analysis_artifact"
    if scope_id is not None:
        data["scope_id"] = scope_id
    if schema_version is not None:
        data["schema_version"] = schema_version
    if structured_lineage:
        data["_lineage"] = "current-lineage"
        data["_lineage_components"] = {
            "provider_model": "provider",
            "prompt": "prompt",
            "context": "context",
            "transcript": "transcript",
            "compressed_media": compressed_stem,
            "window": "window",
        }
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
    _write_compressed(config, "182_clip")
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


def test_persisted_capture_date_is_not_collapsed_to_day1(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "001_clip")
    data = _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip")
    data["capture_date"] = "2026-10-01"
    _write_analysis(config.texts_dir, "001_clip.json", data)

    resolved = resolve_run_scope(config)

    assert resolved.project_mode == "greenfield"
    assert resolved.selected == ["2026-10-01"]
    assert [option.id for option in resolved.options] == ["2026-10-01"]


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


def test_resolver_deduplicates_legacy_project_and_keeps_unknown_bucket(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "182_alpha")
    _write_compressed(config, "192_beta")
    _write_compressed(config, "205_gamma")
    _write_compressed(config, "206_delta")

    _write_analysis(
        config.texts_dir,
        "001_alpha.json",
        _analysis_payload(index="001", original_stem="alpha", compressed_stem="001_alpha", day_label="day1"),
    )
    _write_analysis(
        config.texts_dir,
        "182_alpha.json",
        _analysis_payload(
            index="182",
            original_stem="alpha",
            compressed_stem="182_alpha",
            day_label="day1",
            capture_date="2026-10-01",
        ),
    )
    _write_analysis(
        config.texts_dir,
        "002_beta.json",
        _analysis_payload(index="002", original_stem="beta", compressed_stem="002_beta", day_label="day2"),
    )
    _write_analysis(
        config.texts_dir,
        "192_beta.json",
        _analysis_payload(
            index="192",
            original_stem="beta",
            compressed_stem="192_beta",
            day_label="day2",
            capture_date="2026-10-02",
        ),
    )
    _write_analysis(
        config.texts_dir,
        "205_gamma.json",
        _analysis_payload(index="205", original_stem="gamma", compressed_stem="205_gamma", day_label="day3"),
    )
    _write_analysis(
        config.texts_dir,
        "206_delta.json",
        _analysis_payload(index="206", original_stem="delta", compressed_stem="206_delta"),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert resolved.project_mode == "legacy_adoption"
    assert [option.id for option in resolved.options] == [
        "2026-10-01",
        "2026-10-02",
        "legacy-day3",
        "unknown",
    ]
    assert [option.clip_count for option in resolved.options] == [1, 1, 1, 1]
    assert resolved.selected == ["2026-10-01", "2026-10-02", "legacy-day3"]
    assert [path.name for path in resolved.analyses] == [
        "182_alpha.json",
        "192_beta.json",
        "205_gamma.json",
        "206_delta.json",
    ]
    assert [path.name for path in resolved.ignored_duplicates] == ["001_alpha.json", "002_beta.json"]
    assert resolved.orphaned_artifacts == []


def test_greenfield_resolver_uses_dates_or_unknown_without_day1(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "001_dated")
    _write_compressed(config, "002_undated")
    _write_analysis(
        config.texts_dir,
        "001_dated.json",
        _analysis_payload(
            index="001",
            original_stem="dated",
            compressed_stem="001_dated",
            capture_date="2026-10-01",
        ),
    )
    _write_analysis(
        config.texts_dir,
        "002_undated.json",
        _analysis_payload(index="002", original_stem="undated", compressed_stem="002_undated"),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert resolved.project_mode == "greenfield"
    assert [option.id for option in resolved.options] == ["2026-10-01", "unknown"]
    assert resolved.selected == ["2026-10-01"]
    assert [option.planning_allowed for option in resolved.options] == [True, False]
    assert all(option.id != "day1" for option in resolved.options)


def test_mixed_canonical_and_legacy_evidence_keeps_legacy_adoption(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "001_alpha")
    _write_analysis(
        config.texts_dir,
        "001_alpha.json",
        _analysis_payload(
            index="001",
            original_stem="alpha",
            compressed_stem="001_alpha",
            day_label="day1",
            capture_date="2026-10-01",
            scope_id="2026-10-01",
        ),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert resolved.project_mode == "legacy_adoption"
    assert resolved.options[0].id == "2026-10-01"
    assert resolved.options[0].legacy_day_label == "day1"


def test_pathless_and_path_backed_artifacts_union_by_source_stem(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "182_clip")
    original_path = tmp_path / "originals" / "clip.MP4"
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip"),
    )
    _write_analysis(
        config.texts_dir,
        "182_clip.json",
        _analysis_payload(
            index="182",
            original_stem="clip",
            compressed_stem="182_clip",
            original_path=str(original_path),
            capture_date="2026-10-01",
        ),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert len(resolved.options) == 1
    assert resolved.options[0].clip_count == 1
    assert [path.name for path in resolved.analyses] == ["182_clip.json"]
    assert [path.name for path in resolved.ignored_duplicates] == ["001_clip.json"]


def test_conflicting_legacy_labels_for_one_identity_become_unknown(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "103_clip")
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip", day_label="day1"),
    )
    _write_analysis(
        config.texts_dir,
        "103_clip.json",
        _analysis_payload(index="103", original_stem="clip", compressed_stem="103_clip", day_label="day3"),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert [option.id for option in resolved.options] == ["unknown"]
    assert any("Conflicting legacy labels" in warning for warning in resolved.options[0].warnings)
    assert resolved.selected == []


def test_known_date_wins_over_conflicting_legacy_labels(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "001_clip")
    _write_compressed(config, "103_clip")
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip", day_label="day1"),
    )
    _write_analysis(
        config.texts_dir,
        "103_clip.json",
        _analysis_payload(
            index="103",
            original_stem="clip",
            compressed_stem="103_clip",
            day_label="day3",
            capture_date="2026-10-01",
        ),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert [option.id for option in resolved.options] == ["2026-10-01"]
    option = resolved.options[0]
    assert option.legacy_day_label is None
    assert option.planning_allowed is True
    assert any("Conflicting legacy labels" in warning for warning in option.warnings)


def test_current_structured_lineage_outranks_persisted_scope_in_duplicate_group(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "001_clip")
    _write_compressed(config, "182_clip")
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(
            index="001",
            original_stem="clip",
            compressed_stem="001_clip",
            day_label="day1",
            capture_date="2026-09-01",
        ),
    )
    _write_analysis(
        config.texts_dir,
        "182_clip.json",
        _analysis_payload(
            index="182",
            original_stem="clip",
            compressed_stem="182_clip",
            day_label="day1",
            structured_lineage=True,
        ),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert [path.name for path in resolved.analyses] == ["182_clip.json"]
    assert [path.name for path in resolved.ignored_duplicates] == ["001_clip.json"]


def test_persisted_scope_metadata_outranks_newer_noncanonical_duplicate(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "001_clip")
    _write_compressed(config, "182_clip")
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(
            index="001",
            original_stem="clip",
            compressed_stem="001_clip",
            capture_date="2026-10-01",
            schema_version=1,
        ),
    )
    _write_analysis(
        config.texts_dir,
        "182_clip.json",
        _analysis_payload(
            index="182",
            original_stem="clip",
            compressed_stem="182_clip",
            schema_version=99,
        ),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert [option.id for option in resolved.options] == ["2026-10-01"]
    assert [path.name for path in resolved.analyses] == ["001_clip.json"]
    assert [path.name for path in resolved.ignored_duplicates] == ["182_clip.json"]


def test_orphan_only_legacy_project_is_classified_without_inventing_active_scopes(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_analysis(
        config.texts_dir,
        "001_clip.json",
        _analysis_payload(index="001", original_stem="clip", compressed_stem="001_clip", day_label="day1"),
    )
    _write_analysis(
        config.texts_dir,
        "103_clip.json",
        _analysis_payload(index="103", original_stem="clip", compressed_stem="103_clip", day_label="day3"),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert resolved.project_mode == "legacy_adoption"
    assert resolved.options == []
    assert resolved.selected == []
    assert [path.name for path in resolved.all_deduplicated_analyses] == ["103_clip.json"]
    assert [path.name for path in resolved.orphaned_artifacts] == ["001_clip.json", "103_clip.json"]


def test_empty_videos_json_keeps_legacy_project_discovery(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    (tmp_path / "videos.json").write_text("[]", encoding="utf-8")
    _write_compressed(config, "182_clip")
    _write_analysis(
        config.texts_dir,
        "182_clip.json",
        _analysis_payload(
            index="182",
            original_stem="clip",
            compressed_stem="182_clip",
            capture_date="2026-10-01",
        ),
    )

    resolved = resolve_run_scope(config, all_days=True)

    assert [option.id for option in resolved.options] == ["2026-10-01"]
    assert [path.name for path in resolved.analyses] == ["182_clip.json"]


def test_unanalyzed_dated_media_is_not_planning_allowed(tmp_path: Path) -> None:
    config = _config(tmp_path, tmp_path / "output")
    _write_compressed(config, "001_20261003_120000_unanalyzed")

    resolved = resolve_run_scope(config, all_days=True)

    assert [option.id for option in resolved.options] == ["2026-10-03"]
    option = resolved.options[0]
    assert option.clip_count == 1
    assert option.analyzed_clip_count == 0
    assert option.planning_allowed is False

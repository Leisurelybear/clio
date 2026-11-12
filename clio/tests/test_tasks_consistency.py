from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.ai.structured import ENTITY_CONSISTENCY_OUTPUT
from clio.config import AppConfig
from clio.config.models import (
    AnalyzeConfig,
    GlobalConfig,
    GlobalPathsConfig,
    NamingConfig,
    PlanConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ScriptConfig,
)
from clio.prompt_overrides import PROMPT_PLACEHOLDERS, validate_prompt_template
from clio.prompts import ENTITY_CONSISTENCY_CONTEXT, ENTITY_CONSISTENCY_PROMPT
from clio.schema import ARTIFACT_SCHEMA_VERSION


@pytest.fixture
def cfg(tmp_path: Path) -> AppConfig:
    (tmp_path / "plans").mkdir()
    (tmp_path / "texts").mkdir()
    (tmp_path / "compressed").mkdir()
    (tmp_path / "videos").mkdir()
    return AppConfig(
        global_cfg=GlobalConfig(
            paths=GlobalPathsConfig(ffmpeg="ffmpeg", ffprobe="ffprobe"),
            naming=NamingConfig(index_width=3),
        ),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=tmp_path),
            analyze=AnalyzeConfig(
                skip_existing=True,
                texts_subdir="texts",
                compressed_subdir="compressed",
            ),
            script=ScriptConfig(scripts_subdir="scripts"),
            plan=PlanConfig(plans_subdir="plans"),
        ),
        project_dir=tmp_path / "videos",
    )


def _clips() -> list[dict[str, object]]:
    return [
        {
            "index": "001",
            "title": "Harbor at sunrise",
            "summary": "A quiet harbor",
            "location": "Harbor",
            "duration_sec": 60,
            "timeline": [{"start": "00:00", "end": "00:30", "description": "boats"}],
            "source_stem": "A",
        },
        {
            "index": "002",
            "title": "Harbor walk",
            "summary": "Walking by the water",
            "location": "Harbor",
            "duration_sec": 75,
            "timeline": [{"start": "00:05", "end": "00:40", "description": "boats again"}],
            "source_stem": "B",
        },
    ]


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.provider_id = "deepseek"
    return provider


def _patch_collect(clips: list[dict[str, object]], *, cancelled: bool = False):
    return patch(
        "clio.tasks.plan._collect_plan_clips",
        return_value=(clips, [], cancelled),
    )


def test_normalize_entities_merges_case_insensitively_and_dedupes_clips() -> None:
    from clio.tasks.consistency import normalize_entity_consistency

    report = normalize_entity_consistency(
        {
            "entities": [
                {
                    "canonical_name": "Harbor",
                    "kind": "location",
                    "aliases": ["Port", "harbor", "Dock"],
                    "clips": ["001", "002", "001"],
                },
                {
                    "canonical_name": "harbor",
                    "kind": "location",
                    "aliases": ["Port"],
                    "clips": ["2", "001"],
                },
            ],
            "duplicate_scenes": [],
        },
        _clips(),
    )

    assert report["entities"] == [
        {
            "canonical_name": "Harbor",
            "kind": "location",
            "aliases": ["Port", "Dock"],
            "clips": ["001", "002"],
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "entities": [
                {
                    "canonical_name": "Harbor",
                    "kind": "location",
                    "aliases": [],
                    "clips": ["999"],
                }
            ],
            "duplicate_scenes": [],
        },
        {
            "entities": [],
            "duplicate_scenes": [
                {
                    "canonical_name": "Boats",
                    "clips": [
                        {"index": "001", "start": "00:00", "end": "00:10"},
                        {"index": "999", "start": "00:00", "end": "00:10"},
                    ],
                    "reason": "same subject",
                }
            ],
        },
    ],
)
def test_normalize_entity_consistency_rejects_unknown_clip_index(payload: dict[str, object]) -> None:
    from clio.tasks.consistency import normalize_entity_consistency

    with pytest.raises(ValueError):
        normalize_entity_consistency(payload, _clips())


@pytest.mark.parametrize(
    ("start", "end"),
    [("00:10", "00:10"), ("00:10", "00:05"), ("00:00", "01:20")],
)
def test_normalize_duplicate_scenes_validates_timestamps_and_ranges(start: str, end: str) -> None:
    from clio.tasks.consistency import normalize_entity_consistency

    payload = {
        "entities": [],
        "duplicate_scenes": [
            {
                "canonical_name": "Boats",
                "clips": [
                    {"index": "001", "start": start, "end": end},
                    {"index": "002", "start": "00:00", "end": "00:10"},
                ],
                "reason": "same subject",
            }
        ],
    }

    with pytest.raises(ValueError):
        normalize_entity_consistency(payload, _clips())


def test_entity_consistency_lineage_changes_with_clips_context_and_prompt(cfg: AppConfig) -> None:
    from clio.tasks.consistency import entity_consistency_lineage_fingerprint

    base = entity_consistency_lineage_fingerprint(cfg, _clips())
    changed_clip = _clips()
    changed_clip[0]["timeline"] = [{"start": "00:00", "end": "00:10", "description": "changed"}]

    assert entity_consistency_lineage_fingerprint(cfg, changed_clip) != base
    assert entity_consistency_lineage_fingerprint(cfg, _clips(), context_override="new trip context") != base
    assert (
        entity_consistency_lineage_fingerprint(
            cfg,
            _clips(),
            task_prompts={"entity_consistency": "Use {clips_json} and report entities."},
        )
        != base
    )


def test_load_entity_consistency_requires_current_lineage_and_schema(cfg: AppConfig) -> None:
    from clio.tasks.consistency import (
        entity_consistency_lineage_fingerprint,
        entity_consistency_paths,
        load_entity_consistency,
    )
    from clio.utils import JsonValue, write_json_atomic

    clips = _clips()
    out_json, out_md = entity_consistency_paths(cfg, "day1")
    out_md.write_text("# report\n", encoding="utf-8")
    current: dict[str, JsonValue] = {
        "day_label": "day1",
        "entities": [],
        "duplicate_scenes": [],
        "_schema_version": ARTIFACT_SCHEMA_VERSION,
        "_lineage": entity_consistency_lineage_fingerprint(cfg, clips),
    }
    write_json_atomic(out_json, current)

    assert load_entity_consistency(cfg, "day1", clips) == current

    write_json_atomic(out_json, {**current, "_lineage": "stale"})
    assert load_entity_consistency(cfg, "day1", clips) is None

    write_json_atomic(out_json, {**current, "_schema_version": ARTIFACT_SCHEMA_VERSION - 1})
    assert load_entity_consistency(cfg, "day1", clips) is None


def test_run_entity_consistency_skips_current_report(cfg: AppConfig) -> None:
    from clio.schema import add_schema_version
    from clio.tasks.consistency import (
        entity_consistency_lineage_fingerprint,
        entity_consistency_paths,
        run_entity_consistency,
    )
    from clio.utils import write_json_atomic

    clips = _clips()
    out_json, out_md = entity_consistency_paths(cfg, "day1")
    report = add_schema_version(
        {
            "day_label": "day1",
            "entities": [],
            "duplicate_scenes": [],
            "_lineage": entity_consistency_lineage_fingerprint(cfg, clips),
        }
    )
    write_json_atomic(out_json, report)
    out_md.write_text("# report\n", encoding="utf-8")

    with _patch_collect(clips), patch("clio.tasks.consistency.get_task_provider") as get_provider:
        result = run_entity_consistency(cfg, day_label="day1")

    assert result == report
    get_provider.assert_not_called()


def test_run_entity_consistency_regenerates_stale_report(cfg: AppConfig) -> None:
    from clio.schema import add_schema_version
    from clio.tasks.consistency import entity_consistency_paths, load_entity_consistency, run_entity_consistency
    from clio.utils import write_json_atomic

    clips = _clips()
    out_json, out_md = entity_consistency_paths(cfg, "day1")
    stale = add_schema_version(
        {
            "day_label": "day1",
            "entities": [],
            "duplicate_scenes": [],
            "_lineage": "stale",
        }
    )
    write_json_atomic(out_json, stale)
    out_md.write_text("# stale report\n", encoding="utf-8")

    with (
        _patch_collect(clips),
        patch("clio.tasks.consistency.get_task_provider", return_value=(_provider(), "deepseek-chat")),
        patch("clio.tasks.consistency.provider_use"),
        patch(
            "clio.tasks.consistency._call_ai",
            return_value=json.dumps({"entities": [], "duplicate_scenes": []}),
        ) as call_ai,
    ):
        result = run_entity_consistency(cfg, day_label="day1")

    call_ai.assert_called_once()
    assert result is not None
    assert result["_lineage"] != "stale"
    assert load_entity_consistency(cfg, "day1", clips) is not None


def test_run_entity_consistency_cancels_before_ai(cfg: AppConfig) -> None:
    from clio.tasks.consistency import run_entity_consistency

    with (
        _patch_collect([], cancelled=True),
        patch("clio.tasks.consistency.get_task_provider") as get_provider,
    ):
        result = run_entity_consistency(cfg, cancel_event=threading.Event())

    assert result is None
    get_provider.assert_not_called()


def test_run_entity_consistency_rejects_malformed_ai_output(cfg: AppConfig) -> None:
    from clio.tasks.consistency import entity_consistency_paths, run_entity_consistency

    with (
        _patch_collect(_clips()),
        patch("clio.tasks.consistency.get_task_provider", return_value=(_provider(), "deepseek-chat")),
        patch("clio.tasks.consistency.provider_use"),
        patch("clio.tasks.consistency._call_ai", return_value="not json"),
    ):
        with pytest.raises(ValueError):
            run_entity_consistency(cfg, day_label="day1", overwrite=True)

    out_json, out_md = entity_consistency_paths(cfg, "day1")
    assert not out_json.exists()
    assert not out_md.exists()


def test_consistency_prompt_schema_and_placeholders_are_registered() -> None:
    assert PROMPT_PLACEHOLDERS["entity_consistency"] == {"clips_json"}
    assert PROMPT_PLACEHOLDERS["entity_consistency_context"] == {"consistency_json"}
    validate_prompt_template("entity_consistency", ENTITY_CONSISTENCY_PROMPT)
    validate_prompt_template("entity_consistency_context", ENTITY_CONSISTENCY_CONTEXT)
    assert ENTITY_CONSISTENCY_OUTPUT.schema is not None
    assert ENTITY_CONSISTENCY_OUTPUT.schema["required"] == ["entities", "duplicate_scenes"]
    assert ENTITY_CONSISTENCY_OUTPUT.schema["additionalProperties"] is False

"""Tests for clio/tasks/plan.py — run_plan_vlog selection + skip-existing."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

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


def _project_dir(cfg: AppConfig) -> Path:
    project_dir = cfg.project_dir
    assert project_dir is not None
    return project_dir


def _write_text(cfg: AppConfig, name: str, stem: str, index: int) -> Path:
    path = cfg.texts_dir / name
    path.write_text(
        json.dumps(
            {
                "index": index,
                "title": stem,
                "summary": f"summary {stem}",
                "source_file": f"{stem}.mp4",
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_existing_plan(cfg: AppConfig, day_label: str = "day1") -> None:
    plan = {
        "day_title": "Full day",
        "theme": "all",
        "total_estimated_sec": 300,
        "sequence": [
            {"index": "001", "title": "A", "use_timeline": "00:00-00:30"},
            {"index": "002", "title": "B", "use_timeline": "00:00-00:30"},
        ],
    }
    (cfg.plans_dir / f"{day_label}_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (cfg.plans_dir / f"{day_label}_plan.md").write_text("# full", encoding="utf-8")


def test_source_inputs_preserve_match_stem_and_segment_offset():
    from clio.tasks.plan import _source_inputs_from_clips

    assert _source_inputs_from_clips(
        [
            {
                "index": 7,
                "source_stem": "007_GoPro",
                "match_stem": "GoPro",
                "segment_offset_sec": "12.5",
            },
            {"index": 8, "source_stem": "008_Clip", "segment_offset_sec": "invalid"},
        ]
    ) == [
        {"index": "7", "source_stem": "007_GoPro", "match_stem": "GoPro", "segment_offset_sec": 12.5},
        {"index": "8", "source_stem": "008_Clip", "match_stem": "", "segment_offset_sec": 0.0},
    ]


def test_plan_lineage_changes_with_analysis_timeline(cfg: AppConfig):
    from clio.tasks.plan import _plan_lineage_fingerprint

    first = [{"index": "001", "source_stem": "A", "title": "A", "timeline": [{"start": "00:00", "end": "00:05"}]}]
    second = [{"index": "001", "source_stem": "A", "title": "A", "timeline": [{"start": "00:00", "end": "00:10"}]}]

    assert _plan_lineage_fingerprint(cfg, first) != _plan_lineage_fingerprint(cfg, second)


def test_planner_uses_projection_while_validation_keeps_full_clips(cfg: AppConfig, capsys) -> None:
    from clio.tasks.plan import run_plan_vlog

    timeline = [
        {"start": f"00:{minute:02d}", "end": f"00:{minute + 1:02d}", "description": str(minute)} for minute in range(5)
    ]
    analysis_path = _write_text(cfg, "001_A.json", "A", 1)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis.update(
        {
            "timeline": timeline,
            "transcript": "INLINE_SECRET",
            "key_moments": [
                {"start": "00:00", "end": "00:05", "title": "opening", "score": 5},
            ],
        }
    )
    analysis["timeline"][0]["transcript_segments"] = [{"start": 0, "end": 1, "text": "SECRET"}]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
    cfg.plan.max_timeline_rows_per_clip = 2

    captured: dict[str, object] = {}

    def _fake(clips, config, day_label="day1", **kwargs):
        captured["clips"] = list(clips)
        captured["full_clips"] = list(kwargs["full_clips"])
        return _fake_plan_payload(clips)

    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake):
        result = run_plan_vlog(cfg, day_label="day1", overwrite=True)

    projected = captured["clips"]
    full = captured["full_clips"]
    assert isinstance(projected, list)
    assert isinstance(full, list)
    assert len(projected[0]["timeline"]) == 2
    assert len(full[0]["timeline"]) == 5
    assert "_original_path" not in projected[0]
    assert "_compressed_path" not in projected[0]
    assert "_original_path" in full[0]
    assert "_compressed_path" in full[0]
    assert projected[0]["key_moments"][0]["title"] == "opening"
    assert "transcript" not in projected[0]
    assert "transcript_segments" not in projected[0]["timeline"][0]
    assert result is not None
    assert result["source_inputs"] == [
        {"index": "001", "source_stem": "A", "match_stem": "A", "segment_offset_sec": 0.0}
    ]
    assert "注入规划" in capsys.readouterr().out


def test_run_plan_forwards_cancel_event(cfg: AppConfig) -> None:
    from clio.tasks.plan import run_plan_vlog

    _write_text(cfg, "001_A.json", "A", 1)
    event = threading.Event()
    captured: dict[str, object] = {}

    def _fake(clips, config, day_label="day1", **kwargs):
        captured["cancel_event"] = kwargs.get("cancel_event")
        return _fake_plan_payload(clips)

    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake):
        result = run_plan_vlog(cfg, day_label="day1", overwrite=True, cancel_event=event)

    assert result is not None
    assert captured["cancel_event"] is event


def test_plan_failure_does_not_write_artifacts(cfg: AppConfig):
    from clio.tasks.plan import run_plan_vlog

    _write_text(cfg, "001_A.json", "A", 1)
    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=RuntimeError("没有可用片段")):
        with pytest.raises(RuntimeError, match="没有可用片段"):
            run_plan_vlog(cfg, day_label="day1", overwrite=True)

    assert not (cfg.plans_dir / "day1_plan.json").exists()
    assert not (cfg.plans_dir / "day1_plan.md").exists()


class TestRunPlanVlogFilesFilter:
    def test_files_selection_bypasses_skip_existing(self, cfg: AppConfig):
        """When files= is set, do not return the full prior plan via skip-existing (I1)."""
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        _write_text(cfg, "002_B.json", "B", 2)
        _write_existing_plan(cfg)

        captured: dict = {}

        def _fake_plan(clips, config, day_label="day1", **kwargs):
            captured["clips"] = list(clips)
            return {
                "day_title": "Selected",
                "theme": "subset",
                "total_estimated_sec": 60,
                "sequence": [{"index": c["index"], "title": c["title"], "use_timeline": "00:00-00:10"} for c in clips],
            }

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan):
            result = run_plan_vlog(
                cfg,
                day_label="day1",
                files=["A"],
                overwrite=False,
                confirm_plan_overwrite=True,
            )

        assert result is not None
        assert "clips" in captured, "plan_daily_vlog should run when files= is set despite existing plan"
        assert len(captured["clips"]) == 1
        assert captured["clips"][0]["title"] == "A"
        assert len(result.get("sequence", [])) == 1
        assert result["sequence"][0]["title"] == "A"

    def test_same_named_originals_only_plan_selected_path(self, cfg: AppConfig):
        from clio.tasks._video_loader import save_selected_videos
        from clio.tasks.plan import run_plan_vlog

        a_dir = _project_dir(cfg) / "a"
        b_dir = _project_dir(cfg) / "b"
        a_dir.mkdir()
        b_dir.mkdir()
        a = a_dir / "clip.mp4"
        b = b_dir / "clip.mp4"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        save_selected_videos(_project_dir(cfg), [a, b])

        for index, path in ((1, a), (2, b)):
            payload = {
                "index": index,
                "title": path.parent.name,
                "summary": "s",
                "source_file": path.name,
                "media_identity": {
                    "original_stem": "clip",
                    "original_path": str(path),
                    "compressed_stem": f"{index:03d}_clip",
                    "compressed_path": "",
                    "index": f"{index:03d}",
                },
            }
            (cfg.texts_dir / f"{index:03d}_clip.json").write_text(json.dumps(payload), encoding="utf-8")

        captured: dict = {}

        def _fake_plan(clips, config, day_label="day1", **kwargs):
            captured["clips"] = list(clips)
            return {
                "day_title": "Selected",
                "theme": "subset",
                "total_estimated_sec": 10,
                "sequence": [{"index": c["index"], "title": c["title"], "use_timeline": "00:00-00:05"} for c in clips],
            }

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan):
            run_plan_vlog(cfg, day_label="day1", files=[str(a)], overwrite=True)

        assert [clip["index"] for clip in captured["clips"]] == ["001"]

    def test_existing_json_requires_confirmation(self, cfg: AppConfig):
        from clio.tasks.plan import PlanOverwriteConfirmationRequired, run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        _write_existing_plan(cfg)
        plan_file = cfg.plans_dir / "day1_plan.json"
        old_json = plan_file.read_text(encoding="utf-8")
        old_md = (cfg.plans_dir / "day1_plan.md").read_text(encoding="utf-8")

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            with pytest.raises(PlanOverwriteConfirmationRequired):
                run_plan_vlog(cfg, day_label="day1", files=["A"])

        mock_plan.assert_not_called()
        assert plan_file.read_text(encoding="utf-8") == old_json
        assert (cfg.plans_dir / "day1_plan.md").read_text(encoding="utf-8") == old_md

    def test_existing_markdown_alone_requires_confirmation(self, cfg: AppConfig):
        from clio.tasks.plan import PlanOverwriteConfirmationRequired, run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        (cfg.plans_dir / "day1_plan.md").write_text("# hand edited", encoding="utf-8")

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            with pytest.raises(PlanOverwriteConfirmationRequired):
                run_plan_vlog(cfg, day_label="day1", files=["A"])

        mock_plan.assert_not_called()

    def test_no_existing_plan_needs_no_confirmation(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload) as mock_plan:
            result = run_plan_vlog(cfg, day_label="day1", files=["A"])

        mock_plan.assert_called_once()
        assert result is not None
        assert result["source_inputs"] == [
            {"index": "001", "source_stem": "A", "match_stem": "A", "segment_offset_sec": 0.0}
        ]

    def test_empty_files_selection_requires_confirmation(self, cfg: AppConfig):
        from clio.tasks.plan import PlanOverwriteConfirmationRequired, run_plan_vlog

        _write_existing_plan(cfg)

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            with pytest.raises(PlanOverwriteConfirmationRequired):
                run_plan_vlog(cfg, day_label="day1", files=[])

        mock_plan.assert_not_called()

    def test_skip_existing_still_applies_without_files(self, cfg: AppConfig):
        """Without files=, existing valid plan is returned and AI is not called."""
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        _write_existing_plan(cfg)

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            result = run_plan_vlog(cfg, day_label="day1", files=None, overwrite=False)

        mock_plan.assert_not_called()
        assert result is not None
        assert result["theme"] == "all"
        assert len(result["sequence"]) == 2


class TestRunPlanVlogProjectSelection:
    def test_removed_analysis_is_excluded(self, cfg: AppConfig):
        from clio.tasks._video_loader import save_selected_videos
        from clio.tasks.plan import run_plan_vlog

        selected = _project_dir(cfg) / "A.mp4"
        selected.write_bytes(b"video")
        save_selected_videos(_project_dir(cfg), [selected])
        _write_text(cfg, "001_A.json", "A", 1)
        _write_text(cfg, "002_B.json", "B", 2)

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload) as mock_plan:
            result = run_plan_vlog(cfg, day_label="day1", overwrite=True)

        mock_plan.assert_called_once()
        assert result is not None
        assert result["source_inputs"] == [
            {"index": "001", "source_stem": "A", "match_stem": "A", "segment_offset_sec": 0.0}
        ]

    def test_empty_selection_has_no_usable_media(self, cfg: AppConfig):
        from clio.tasks._video_loader import save_selected_videos
        from clio.tasks.plan import run_plan_vlog

        save_selected_videos(_project_dir(cfg), [])
        _write_text(cfg, "001_A.json", "A", 1)

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            result = run_plan_vlog(cfg, day_label="day1", overwrite=True)

        mock_plan.assert_not_called()
        assert result is None


class TestPlanArtifactProtection:
    def _generate_plan(self, cfg: AppConfig) -> Path:
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload):
            run_plan_vlog(cfg, day_label="day1", overwrite=True)
        return cfg.plans_dir / "day1_plan.json"

    def _mark_edited(self, path: Path) -> None:
        from clio.artifact_guard import mark_user_edited

        data = json.loads(path.read_text(encoding="utf-8"))
        mark_user_edited(data, edited_at="2026-11-10T00:00:00Z")
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_same_lineage_skip_preserves_user_edit_without_backup(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        plan_path = self._generate_plan(cfg)
        self._mark_edited(plan_path)
        old_json = plan_path.read_bytes()
        old_md = (cfg.plans_dir / "day1_plan.md").read_bytes()

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            result = run_plan_vlog(cfg, day_label="day1", overwrite=False)

        mock_plan.assert_not_called()
        assert result is not None
        assert result["_user_edited"] is True
        assert plan_path.read_bytes() == old_json
        assert (cfg.plans_dir / "day1_plan.md").read_bytes() == old_md
        assert not list(cfg.plans_dir.glob("*.bak.*"))

    def test_new_lineage_requires_confirmation_before_ai(self, cfg: AppConfig):
        from clio.artifact_guard import ArtifactOverwriteConfirmationRequired
        from clio.tasks.plan import run_plan_vlog

        plan_path = self._generate_plan(cfg)
        self._mark_edited(plan_path)
        old_json = plan_path.read_bytes()
        old_md = (cfg.plans_dir / "day1_plan.md").read_bytes()
        _write_text(cfg, "001_A.json", "A2", 1)

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            with pytest.raises(ArtifactOverwriteConfirmationRequired):
                run_plan_vlog(cfg, day_label="day1", overwrite=False)

        mock_plan.assert_not_called()
        assert plan_path.read_bytes() == old_json
        assert (cfg.plans_dir / "day1_plan.md").read_bytes() == old_md
        assert not list(cfg.plans_dir.glob("*.bak.*"))

    def test_corrupt_existing_plan_requires_confirmation_then_backs_up(self, cfg: AppConfig):
        from clio.artifact_guard import ArtifactOverwriteConfirmationRequired
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        plan_path = cfg.plans_dir / "day1_plan.json"
        md_path = cfg.plans_dir / "day1_plan.md"
        plan_path.write_text("{corrupt", encoding="utf-8")
        md_path.write_text("# user plan", encoding="utf-8")

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload) as mock_plan:
            with pytest.raises(ArtifactOverwriteConfirmationRequired):
                run_plan_vlog(cfg, day_label="day1", overwrite=False)

        mock_plan.assert_not_called()
        assert plan_path.read_text(encoding="utf-8") == "{corrupt"
        assert md_path.read_text(encoding="utf-8") == "# user plan"
        assert not list(cfg.plans_dir.glob("*.bak.*"))

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload) as mock_plan:
            result = run_plan_vlog(
                cfg,
                day_label="day1",
                overwrite=False,
                confirm_artifact_overwrite=True,
            )

        mock_plan.assert_called_once()
        assert result is not None
        assert "_user_edited" not in json.loads(plan_path.read_text(encoding="utf-8"))
        backups = sorted(cfg.plans_dir.glob("*.bak.*"))
        assert {path.name.split(".bak.", 1)[0] for path in backups} == {"day1_plan.json", "day1_plan.md"}
        backup_json = next(path for path in backups if path.name.startswith("day1_plan.json.bak."))
        assert backup_json.read_text(encoding="utf-8") == "{corrupt"

    def test_confirmed_overwrite_backs_up_old_plan_and_clears_marker(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        plan_path = self._generate_plan(cfg)
        self._mark_edited(plan_path)
        _write_text(cfg, "001_A.json", "A2", 1)

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload) as mock_plan:
            result = run_plan_vlog(
                cfg,
                day_label="day1",
                overwrite=False,
                confirm_artifact_overwrite=True,
            )

        mock_plan.assert_called_once()
        assert result is not None
        assert "_user_edited" not in result
        backups = sorted(cfg.plans_dir.glob("*.bak.*"))
        assert {path.name.split(".bak.", 1)[0] for path in backups} == {"day1_plan.json", "day1_plan.md"}
        backup_json = next(path for path in backups if path.name.startswith("day1_plan.json.bak."))
        assert json.loads(backup_json.read_text(encoding="utf-8"))["_user_edited"] is True
        assert json.loads(plan_path.read_text(encoding="utf-8")).get("_user_edited") is None


def _fake_plan_payload(clips, config=None, day_label="day1", **kwargs):
    return {
        "day_title": "Selected",
        "theme": "subset",
        "total_estimated_sec": 60,
        "opening_tip": "",
        "ending_tip": "",
        "sequence": [{"index": c["index"], "title": c["title"], "use_timeline": "00:00-00:10"} for c in clips],
    }


class TestPlanSourceInputs:
    def test_full_plan_writes_source_inputs(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        _write_text(cfg, "002_B.json", "B", 2)

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload):
            result = run_plan_vlog(cfg, day_label="day1", files=None, overwrite=True)

        assert result is not None
        si = result["source_inputs"]
        assert si == [
            {"index": "001", "source_stem": "A", "match_stem": "A", "segment_offset_sec": 0.0},
            {"index": "002", "source_stem": "B", "match_stem": "B", "segment_offset_sec": 0.0},
        ]
        on_disk = json.loads((cfg.plans_dir / "day1_plan.json").read_text(encoding="utf-8"))
        assert on_disk["source_inputs"] == si

    def test_files_selection_subset_in_source_inputs(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        _write_text(cfg, "002_B.json", "B", 2)

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload):
            result = run_plan_vlog(cfg, day_label="day1", files=["A"], overwrite=True)

        assert result is not None
        assert result["source_inputs"] == [
            {"index": "001", "source_stem": "A", "match_stem": "A", "segment_offset_sec": 0.0}
        ]

    def test_legacy_part_transcript_matches_original_stem(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "GL010683", 1)
        cfg.plan.use_transcripts = True
        transcript_dir = cfg.transcripts_dir
        transcript_dir.mkdir(parents=True)
        (transcript_dir / "001_GL010683_part02_transcript.json").write_text(
            json.dumps({"source_stem": "001_GL010683_part02", "segments": []}),
            encoding="utf-8",
        )
        captured: dict[str, dict[str, object]] = {}

        def _fake(clips, config, day_label="day1", **kwargs):
            captured["transcripts"] = cast(dict[str, object], kwargs.get("transcripts_map"))
            return _fake_plan_payload(clips)

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake):
            run_plan_vlog(cfg, day_label="day1", overwrite=True)

        assert "gl010683" in captured["transcripts"]

    def test_ai_supplied_source_inputs_are_overwritten(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)

        def _poisoned(clips, config, day_label="day1", **kwargs):
            payload = _fake_plan_payload(clips)
            payload["source_inputs"] = [{"index": "999", "source_stem": "FAKE"}]
            return payload

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_poisoned):
            result = run_plan_vlog(cfg, day_label="day1", overwrite=True)

        assert result is not None
        assert result["source_inputs"] == [
            {"index": "001", "source_stem": "A", "match_stem": "A", "segment_offset_sec": 0.0}
        ]

    def test_plan_md_lists_source_inputs(self, cfg: AppConfig):
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        _write_text(cfg, "002_B.json", "B", 2)

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload):
            run_plan_vlog(cfg, day_label="day1", overwrite=True)

        md = (cfg.plans_dir / "day1_plan.md").read_text(encoding="utf-8")
        assert "## 规划素材" in md
        assert "`001` A" in md
        assert "`002` B" in md
        # section before sequence
        assert md.index("## 规划素材") < md.index("## 推荐剪辑顺序")

    def test_skip_existing_preserves_plan_without_source_inputs(self, cfg: AppConfig):
        """Legacy plan without the field is returned as-is on skip."""
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        legacy = {
            "day_title": "Legacy",
            "theme": "old",
            "total_estimated_sec": 10,
            "sequence": [{"index": "001", "title": "A", "use_timeline": "00:00-00:05"}],
        }
        (cfg.plans_dir / "day1_plan.json").write_text(json.dumps(legacy), encoding="utf-8")
        (cfg.plans_dir / "day1_plan.md").write_text("# legacy", encoding="utf-8")

        with patch("clio.tasks.plan.plan_daily_vlog") as mock_plan:
            result = run_plan_vlog(cfg, day_label="day1", files=None, overwrite=False)

        mock_plan.assert_not_called()
        assert result is not None
        assert "source_inputs" not in result

    def test_plan_regenerates_when_inputs_change(self, cfg: AppConfig):
        """Same-stem different analysis content must invalidate the cached plan (P1-P1-06)."""
        from clio.tasks.plan import run_plan_vlog

        _write_text(cfg, "001_A.json", "A", 1)
        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload):
            run_plan_vlog(cfg, day_label="day1", files=None, overwrite=True)

        # Re-analyze: same index/stem but different title+summary content.
        (cfg.texts_dir / "001_A.json").write_text(
            json.dumps({"index": 1, "title": "A2", "summary": "changed", "source_file": "A.mp4"}),
            encoding="utf-8",
        )

        with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload) as mock_plan:
            run_plan_vlog(cfg, day_label="day1", files=None, overwrite=False)

        mock_plan.assert_called_once()


def test_planner_snaps_plan_ranges_to_analysis_timeline(cfg: AppConfig):
    from clio.tasks.plan import run_plan_vlog

    analysis_path = _write_text(cfg, "001_A.json", "A", 1)
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["timeline"] = [
        {"start": "00:00", "end": "00:05", "description": "开门"},
        {"start": "00:05", "end": "00:15", "description": "散步"},
        {"start": "00:15", "end": "00:20", "description": "收尾"},
    ]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    def _fake_plan(clips, config, day_label="day1", **kwargs):
        return {
            "day_title": "day1",
            "theme": "walk",
            "total_estimated_sec": 12,
            "sequence": [
                {"index": clips[0]["index"], "title": "walk", "use_timeline": "00:04-00:16"},
            ],
        }

    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan):
        result = run_plan_vlog(cfg, day_label="day1", overwrite=True)

    assert result is not None
    assert result["sequence"][0]["use_timeline"] == "00:05-00:15"
    assert result["total_estimated_sec"] == 10


def _write_consistency_report(cfg: AppConfig, *, lineage: str, malformed: bool = False) -> Path:
    from clio.schema import add_schema_version
    from clio.tasks.consistency import entity_consistency_paths
    from clio.utils import write_json_atomic

    out_json, out_md = entity_consistency_paths(cfg, "day1")
    out_md.write_text("# consistency\n", encoding="utf-8")
    if malformed:
        out_json.write_text("{not json", encoding="utf-8")
        return out_json
    report = add_schema_version(
        {
            "day_label": "day1",
            "entities": [
                {
                    "canonical_name": "Harbor",
                    "kind": "location",
                    "aliases": [],
                    "clips": ["001"],
                }
            ],
            "duplicate_scenes": [],
            "_lineage": lineage,
        }
    )
    write_json_atomic(out_json, report)
    return out_json


def _current_consistency_lineage(cfg: AppConfig) -> str:
    from clio.tasks.consistency import entity_consistency_lineage_fingerprint
    from clio.tasks.plan import _collect_plan_clips

    clips, _skipped, cancelled = _collect_plan_clips(
        cfg,
        "day1",
        None,
        filter_by_day=False,
        cancel_event=None,
    )
    assert not cancelled
    return entity_consistency_lineage_fingerprint(cfg, clips)


def test_plan_receives_current_entity_consistency_report(cfg: AppConfig) -> None:
    from clio.tasks.plan import run_plan_vlog

    _write_text(cfg, "001_A.json", "A", 1)
    _write_consistency_report(cfg, lineage=_current_consistency_lineage(cfg))
    captured: dict[str, object] = {}

    def _fake(clips, config, day_label="day1", **kwargs):
        captured["consistency"] = kwargs.get("entity_consistency")
        return _fake_plan_payload(clips, config, day_label, **kwargs)

    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake):
        result = run_plan_vlog(cfg, day_label="day1", overwrite=True)

    assert result is not None
    consistency = captured["consistency"]
    assert isinstance(consistency, dict)
    assert consistency["entities"][0]["canonical_name"] == "Harbor"


def test_entity_consistency_artifact_invalidates_cached_plan(cfg: AppConfig) -> None:
    from clio.tasks.plan import run_plan_vlog

    _write_text(cfg, "001_A.json", "A", 1)
    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload):
        first = run_plan_vlog(cfg, day_label="day1", overwrite=True)

    assert first is not None
    _write_consistency_report(cfg, lineage=_current_consistency_lineage(cfg))

    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake_plan_payload) as mock_plan:
        second = run_plan_vlog(cfg, day_label="day1", overwrite=False)

    mock_plan.assert_called_once()
    assert second is not None
    assert second["_lineage"] != first["_lineage"]


@pytest.mark.parametrize("mode", ["malformed", "stale"])
def test_plan_ignores_unusable_entity_consistency_report(cfg: AppConfig, mode: str) -> None:
    from clio.tasks.plan import run_plan_vlog

    _write_text(cfg, "001_A.json", "A", 1)
    _write_consistency_report(cfg, lineage="stale", malformed=mode == "malformed")
    captured: dict[str, object] = {}

    def _fake(clips, config, day_label="day1", **kwargs):
        captured["consistency"] = kwargs.get("entity_consistency")
        return _fake_plan_payload(clips, config, day_label, **kwargs)

    with patch("clio.tasks.plan.plan_daily_vlog", side_effect=_fake):
        result = run_plan_vlog(cfg, day_label="day1", overwrite=True)

    assert result is not None
    assert captured["consistency"] is None

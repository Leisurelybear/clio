from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from clio.config import AppConfig
from clio.config.models import (
    AnalyzeConfig,
    GlobalConfig,
    GlobalPathsConfig,
    PlanConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ScriptConfig,
)


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    texts = tmp_path / "texts"
    scripts = tmp_path / "scripts"
    texts.mkdir()
    scripts.mkdir()
    template = tmp_path / "vlog_template.md"
    template.write_text("Template: {title}")
    return AppConfig(
        global_cfg=GlobalConfig(
            paths=GlobalPathsConfig(ffmpeg="", ffprobe=""),
        ),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(
                output_dir=tmp_path,
            ),
            analyze=AnalyzeConfig(
                skip_existing=True,
                texts_subdir="texts",
                compressed_subdir="compressed",
                max_workers=1,
            ),
            script=ScriptConfig(
                scripts_subdir="scripts",
                template_file=template,
            ),
            plan=PlanConfig(plans_subdir="plans"),
        ),
        project_dir=tmp_path,
    )


def test_voiceover_lineage_changes_with_template(cfg: AppConfig):
    from clio.tasks.scripts import _voiceover_lineage_fingerprint

    analysis = {"index": "001", "title": "A"}
    assert _voiceover_lineage_fingerprint(cfg, analysis, "template A") != _voiceover_lineage_fingerprint(
        cfg, analysis, "template B"
    )


def test_voiceover_lineage_changes_with_target_words(cfg: AppConfig):
    from clio.tasks.scripts import _voiceover_lineage_fingerprint

    analysis = {"index": "001", "title": "A"}
    base = _voiceover_lineage_fingerprint(cfg, analysis, "template")
    cfg.script.target_words += 20

    assert _voiceover_lineage_fingerprint(cfg, analysis, "template") != base


def test_missing_default_template_falls_back_to_bundled(cfg: AppConfig, tmp_path, monkeypatch):
    from clio.tasks.scripts import _load_script_template

    bundled = tmp_path / "bundled_vlog_template.md"
    bundled.write_text("Bundled template", encoding="utf-8")
    assert cfg.project_cfg is not None
    cfg.project_cfg.script.template_file = tmp_path / "templates" / "vlog_template.md"
    monkeypatch.setattr("clio.tasks.scripts.find_resource", lambda _name: bundled)

    assert _load_script_template(cfg) == "Bundled template"


class TestRunGenerateScripts:
    @patch("clio.tasks.scripts.generate_voiceover")
    def test_creates_output_dir(self, mock_gen, tmp_path):
        texts = tmp_path / "texts"
        texts.mkdir()
        (texts / "001_test.json").write_text('{"title": "t"}')
        scripts = tmp_path / "scripts"
        template = tmp_path / "vlog_template.md"
        template.write_text("Template: {title}")
        cfg = AppConfig(
            global_cfg=GlobalConfig(
                paths=GlobalPathsConfig(ffmpeg="", ffprobe=""),
            ),
            project_cfg=ProjectConfig(
                paths=ProjectPathsConfig(output_dir=tmp_path),
                analyze=AnalyzeConfig(
                    skip_existing=True,
                    texts_subdir="texts",
                    compressed_subdir="compressed",
                    max_workers=1,
                ),
                script=ScriptConfig(
                    scripts_subdir="scripts",
                    template_file=template,
                ),
                plan=PlanConfig(plans_subdir="plans"),
            ),
        )
        mock_gen.return_value = {"title": "t", "voiceover": "hello", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        assert scripts.is_dir()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_generates_from_texts(self, mock_gen, cfg):
        data = {"title": "clip1", "scenes": [{"description": "a cat"}]}
        (cfg.texts_dir / "001_test.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "clip1", "voiceover": "a cat walks in", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        out = cfg.scripts_dir / "001_test_voiceover.json"
        assert out.exists()
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["voiceover"] == "a cat walks in"

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_skip_existing(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        out = cfg.scripts_dir / "001_voiceover.json"
        out.write_text('{"voiceover": "existing"}')

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        mock_gen.assert_not_called()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_regenerates_when_analysis_changes(self, mock_gen, cfg):
        """Different analysis content must invalidate the cached script (P1-P1-06)."""
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v1", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        out = cfg.scripts_dir / "001_voiceover.json"
        assert out.exists()
        assert mock_gen.call_count == 1

        # Re-analyze the same clip with different content.
        (cfg.texts_dir / "001.json").write_text(json.dumps({"title": "t", "summary": "totally different"}))
        run_generate_scripts(cfg)
        assert mock_gen.call_count == 2

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_writes_md_file(self, mock_gen, cfg):
        data = {"title": "clip1", "scenes": []}
        (cfg.texts_dir / "001_test.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "clip1", "voiceover": "hello world", "edit_tip": "add b-roll"}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        md = cfg.scripts_dir / "001_test_voiceover.md"
        assert md.exists()
        content = md.read_text(encoding="utf-8")
        assert "hello world" in content
        assert "add b-roll" in content

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_single_file_param(self, mock_gen, cfg):
        data = {"title": "t"}
        f = cfg.texts_dir / "001.json"
        f.write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg, single_file=f)
        out = cfg.scripts_dir / "001_voiceover.json"
        assert out.exists()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_passes_template(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        assert "Template: {title}" in mock_gen.call_args.args[1]

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_tracker_next_called(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}
        tracker = MagicMock()

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg, tracker=tracker)
        tracker.update.assert_called_once()
        tracker.next.assert_called_once()
        tracker.log.assert_called_once()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_no_texts_found(self, mock_gen, cfg):
        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        mock_gen.assert_not_called()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_files_filter(self, mock_gen, cfg):
        data = {"title": "t", "scenes": []}
        for name in ("001_A.json", "002_B.json", "003_C.json"):
            (cfg.texts_dir / name).write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg, files=["002_B"])
        assert mock_gen.call_count == 1
        out = cfg.scripts_dir / "002_B_voiceover.json"
        assert out.exists()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_default_batch_filters_by_videos_json(self, mock_gen, cfg, tmp_path):
        current = tmp_path / "current.mp4"
        current.write_bytes(b"video")
        (tmp_path / "videos.json").write_text(json.dumps([str(current)]), encoding="utf-8")
        (cfg.texts_dir / "001_current.json").write_text(
            json.dumps({"title": "current", "media_identity": {"original_path": str(current)}}),
            encoding="utf-8",
        )
        (cfg.texts_dir / "002_stale.json").write_text(json.dumps({"title": "stale"}), encoding="utf-8")
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        assert mock_gen.call_count == 1
        assert (cfg.scripts_dir / "001_current_voiceover.json").exists()
        assert not (cfg.scripts_dir / "002_stale_voiceover.json").exists()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_overwrite_flag(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        out = cfg.scripts_dir / "001_voiceover.json"
        out.write_text('{"voiceover": "existing"}')
        mock_gen.return_value = {"title": "t", "voiceover": "new", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg, overwrite=True)
        assert mock_gen.call_count == 1
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["voiceover"] == "new"

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_cancel_event_returns_cancelled(self, mock_gen, cfg):
        import threading

        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}
        cancel = threading.Event()
        cancel.set()

        from clio.processing_state import ProcessingState
        from clio.tasks.scripts import _process_one_script

        state = ProcessingState(cfg.paths.output_dir)
        result = _process_one_script(
            cfg.texts_dir / "001.json", cfg, "template", MagicMock(), cancel, False, state, None
        )
        assert result == "cancelled"
        mock_gen.assert_not_called()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_corrupt_existing_cache_requires_confirmation_then_backs_up(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        out = cfg.scripts_dir / "001_voiceover.json"
        out.write_text("{corrupt", encoding="utf-8")
        mock_gen.return_value = {"title": "t", "voiceover": "new", "edit_tip": ""}

        from clio.artifact_guard import ArtifactOverwriteConfirmationRequired
        from clio.tasks.scripts import run_generate_scripts

        with pytest.raises(ArtifactOverwriteConfirmationRequired):
            run_generate_scripts(cfg)
        mock_gen.assert_not_called()
        assert out.read_text(encoding="utf-8") == "{corrupt"
        assert not list(cfg.scripts_dir.glob("*.bak.*"))

        run_generate_scripts(cfg, confirm_artifact_overwrite=True)
        assert mock_gen.call_count == 1
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["voiceover"] == "new"
        assert result["_lineage"]
        assert "_user_edited" not in result
        backups = sorted(cfg.scripts_dir.glob("001_voiceover.json.bak.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{corrupt"

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_non_object_cache_requires_confirmation_then_backs_up(self, mock_gen, cfg):
        (cfg.texts_dir / "001.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
        out = cfg.scripts_dir / "001_voiceover.json"
        out.write_text("[]", encoding="utf-8")
        mock_gen.return_value = {"title": "t", "voiceover": "new", "edit_tip": ""}

        from clio.artifact_guard import ArtifactOverwriteConfirmationRequired
        from clio.tasks.scripts import run_generate_scripts

        with pytest.raises(ArtifactOverwriteConfirmationRequired):
            run_generate_scripts(cfg)

        mock_gen.assert_not_called()
        assert out.read_text(encoding="utf-8") == "[]"
        assert not list(cfg.scripts_dir.glob("*.bak.*"))

        run_generate_scripts(cfg, confirm_artifact_overwrite=True)

        mock_gen.assert_called_once()
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["voiceover"] == "new"
        assert "_user_edited" not in result
        backups = sorted(cfg.scripts_dir.glob("001_voiceover.json.bak.*"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "[]"

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_same_lineage_skip_preserves_user_edit_without_backup(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "old", "edit_tip": ""}

        from clio.artifact_guard import mark_user_edited
        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        out = cfg.scripts_dir / "001_voiceover.json"
        existing = json.loads(out.read_text(encoding="utf-8"))
        mark_user_edited(existing, edited_at="2026-11-10T00:00:00Z")
        out.write_text(json.dumps(existing), encoding="utf-8")
        old = out.read_bytes()
        mock_gen.reset_mock()

        run_generate_scripts(cfg)

        mock_gen.assert_not_called()
        assert out.read_bytes() == old
        assert json.loads(old)["_user_edited"] is True
        assert not list(cfg.scripts_dir.glob("*.bak.*"))

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_lineage_change_requires_confirmation_before_ai(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "old", "edit_tip": ""}

        from clio.artifact_guard import ArtifactOverwriteConfirmationRequired, mark_user_edited
        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        out = cfg.scripts_dir / "001_voiceover.json"
        existing = json.loads(out.read_text(encoding="utf-8"))
        mark_user_edited(existing)
        out.write_text(json.dumps(existing), encoding="utf-8")
        old = out.read_bytes()
        mock_gen.reset_mock()
        (cfg.texts_dir / "001.json").write_text(json.dumps({"title": "t", "summary": "changed"}))

        with pytest.raises(ArtifactOverwriteConfirmationRequired):
            run_generate_scripts(cfg)

        mock_gen.assert_not_called()
        assert out.read_bytes() == old
        assert not list(cfg.scripts_dir.glob("*.bak.*"))

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_confirmed_overwrite_backs_up_user_edit_and_clears_marker(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "old", "edit_tip": ""}

        from clio.artifact_guard import mark_user_edited
        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        out = cfg.scripts_dir / "001_voiceover.json"
        existing = json.loads(out.read_text(encoding="utf-8"))
        mark_user_edited(existing)
        out.write_text(json.dumps(existing), encoding="utf-8")
        mock_gen.reset_mock()
        mock_gen.return_value = {"title": "t", "voiceover": "new", "edit_tip": ""}
        (cfg.texts_dir / "001.json").write_text(json.dumps({"title": "t", "summary": "changed"}))

        run_generate_scripts(cfg, confirm_artifact_overwrite=True)

        mock_gen.assert_called_once()
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["voiceover"] == "new"
        assert "_user_edited" not in result
        backups = sorted(cfg.scripts_dir.glob("001_voiceover.json.bak.*"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8"))["_user_edited"] is True

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_parallel_mode_checks_confirmation_before_submit(self, mock_gen, cfg):
        cfg.project_cfg.analyze.max_workers = 4
        (cfg.texts_dir / "001.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
        (cfg.scripts_dir / "001_voiceover.json").write_text("{corrupt", encoding="utf-8")

        from clio.artifact_guard import ArtifactOverwriteConfirmationRequired
        from clio.tasks.scripts import run_generate_scripts

        with (
            patch("clio.tasks.scripts.ThreadPoolExecutor") as pool,
            pytest.raises(ArtifactOverwriteConfirmationRequired),
        ):
            run_generate_scripts(cfg)

        pool.assert_not_called()
        mock_gen.assert_not_called()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_legacy_user_edited_cache_is_stamped_and_skipped(self, mock_gen, cfg):
        (cfg.texts_dir / "001.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
        out = cfg.scripts_dir / "001_voiceover.json"
        out.write_text(
            json.dumps({"voiceover": "legacy", "_user_edited": True, "_user_edited_at": "2026-11-10T00:00:00Z"}),
            encoding="utf-8",
        )

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)

        mock_gen.assert_not_called()
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["voiceover"] == "legacy"
        assert result["_user_edited"] is True
        assert result["_lineage"]

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_legacy_cache_without_lineage_stamped(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        out = cfg.scripts_dir / "001_voiceover.json"
        out.write_text('{"voiceover": "legacy"}')

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        mock_gen.assert_not_called()
        result = json.loads(out.read_text(encoding="utf-8"))
        assert result["voiceover"] == "legacy"
        assert result["_lineage"]

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_lineage_change_regenerates(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v1", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        assert mock_gen.call_count == 1
        from clio.config.models import TaskConfig

        cfg.project_cfg.ai.tasks["voiceover"] = TaskConfig(provider="deepseek", model="deepseek-chat")
        cfg.project_cfg.ai.tasks["voiceover"].model = "different-model"
        run_generate_scripts(cfg)
        assert mock_gen.call_count == 2

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_generation_error_counts_and_continues(self, mock_gen, cfg, capsys):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        (cfg.texts_dir / "002.json").write_text(json.dumps(data))
        mock_gen.side_effect = [RuntimeError("boom"), {"title": "t", "voiceover": "ok", "edit_tip": ""}]

        from clio.tasks.scripts import run_generate_scripts

        with pytest.raises(RuntimeError, match="1 个失败"):
            run_generate_scripts(cfg)
        assert mock_gen.call_count == 2
        out = cfg.scripts_dir / "002_voiceover.json"
        assert out.exists()
        assert "1 个 voiceover 生成失败" in capsys.readouterr().out

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_parallel_path(self, mock_gen, cfg):
        cfg.project_cfg.analyze.max_workers = 4
        data = {"title": "t"}
        for name in ("001.json", "002.json", "003.json"):
            (cfg.texts_dir / name).write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        assert mock_gen.call_count == 3
        for name in ("001", "002", "003"):
            assert (cfg.scripts_dir / f"{name}_voiceover.json").exists()

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_parallel_cancel(self, mock_gen, cfg, capsys):
        import threading

        cfg.project_cfg.analyze.max_workers = 4
        data = {"title": "t"}
        for name in ("001.json", "002.json", "003.json"):
            (cfg.texts_dir / name).write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}
        cancel = threading.Event()
        cancel.set()

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg, cancel_event=cancel)
        assert "取消" in capsys.readouterr().out

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_missing_template_file_falls_back_empty(self, mock_gen, cfg):

        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.return_value = {"title": "t", "voiceover": "v", "edit_tip": ""}
        cfg.project_cfg.script.template_file = cfg.project_cfg.script.template_file.parent / "nope.md"

        from clio.tasks.scripts import run_generate_scripts

        run_generate_scripts(cfg)
        assert mock_gen.call_count == 1

    @patch("clio.tasks.scripts.generate_voiceover")
    def test_process_one_script_returns_error_string(self, mock_gen, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_gen.side_effect = [RuntimeError("boom"), {"title": "t", "voiceover": "v", "edit_tip": ""}]

        from clio.processing_state import ProcessingState
        from clio.tasks.scripts import _process_one_script

        state = ProcessingState(cfg.paths.output_dir)
        with pytest.raises(RuntimeError):
            _process_one_script(cfg.texts_dir / "001.json", cfg, "template", MagicMock(), None, False, state, None)


def _write_plan_voiceover_inputs(
    cfg: AppConfig,
    *,
    offset_sec: float = 0.0,
    source_inputs: object | None = None,
) -> dict:
    cfg.plans_dir.mkdir(parents=True, exist_ok=True)
    (cfg.texts_dir / "001_A.json").write_text(
        json.dumps(
            {
                "index": 1,
                "title": "A",
                "summary": "harbor walk",
                "timeline": [{"start": "00:00", "end": "00:10", "description": "walk"}],
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "day_title": "Day 1",
        "theme": "harbor",
        "opening_tip": "",
        "ending_tip": "",
        "sequence": [
            {
                "index": "001",
                "title": "A",
                "use_timeline": "00:00-00:10",
                "voiceover_hint": "walk",
            }
        ],
        "source_inputs": source_inputs
        if source_inputs is not None
        else [
            {
                "index": "001",
                "source_stem": "001_A",
                "match_stem": "A",
                "segment_offset_sec": offset_sec,
            }
        ],
    }
    (cfg.plans_dir / "day1_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return plan


def _fake_plan_voiceover(*args, **kwargs) -> dict:
    position = args[2]
    plan = args[0]
    segment = plan["sequence"][position]
    return {
        "position": position,
        "index": segment["index"],
        "title": segment["title"],
        "use_timeline": segment["use_timeline"],
        "voiceover_hint": segment["voiceover_hint"],
        "voiceover": f"voice {position}",
    }


def test_plan_voiceover_returns_none_when_plan_is_missing(cfg: AppConfig):
    from clio.tasks.scripts import run_generate_plan_voiceovers

    with patch("clio.tasks.scripts.generate_plan_voiceover") as mock_generate:
        result = run_generate_plan_voiceovers(cfg, day_label="day1")

    assert result is None
    mock_generate.assert_not_called()


def test_plan_voiceover_writes_json_and_markdown(cfg: AppConfig):
    from clio.tasks.scripts import run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg)
    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover):
        result = run_generate_plan_voiceovers(cfg, day_label="day1")

    assert result is not None
    assert result["_schema_version"] == 2
    assert result["_lineage"]
    assert result["segments"][0]["voiceover"] == "voice 0"
    out_json = cfg.scripts_dir / "day1_plan_voiceover.json"
    out_md = cfg.scripts_dir / "day1_plan_voiceover.md"
    assert json.loads(out_json.read_text(encoding="utf-8")) == result
    markdown = out_md.read_text(encoding="utf-8")
    assert "Day 1" in markdown
    assert "A" in markdown
    assert "voice 0" in markdown


def test_plan_voiceover_same_lineage_skips_without_ai(cfg: AppConfig):
    from clio.tasks.scripts import run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg)
    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover):
        first = run_generate_plan_voiceovers(cfg, day_label="day1")

    out_json = cfg.scripts_dir / "day1_plan_voiceover.json"
    old_bytes = out_json.read_bytes()
    with patch("clio.tasks.scripts.generate_plan_voiceover") as mock_generate:
        second = run_generate_plan_voiceovers(cfg, day_label="day1")

    mock_generate.assert_not_called()
    assert second == first
    assert out_json.read_bytes() == old_bytes


def test_plan_voiceover_changed_analysis_regenerates(cfg: AppConfig):
    from clio.tasks.scripts import run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg)
    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover):
        run_generate_plan_voiceovers(cfg, day_label="day1")

    analysis_path = cfg.texts_dir / "001_A.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["summary"] = "changed harbor walk"
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover) as mock_generate:
        result = run_generate_plan_voiceovers(cfg, day_label="day1")

    assert result is not None
    mock_generate.assert_called_once()


def test_plan_voiceover_user_edit_requires_confirmation_then_backs_up(cfg: AppConfig):
    from clio.artifact_guard import ArtifactOverwriteConfirmationRequired, mark_user_edited
    from clio.tasks.scripts import run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg)
    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover):
        run_generate_plan_voiceovers(cfg, day_label="day1")

    out_json = cfg.scripts_dir / "day1_plan_voiceover.json"
    out_md = cfg.scripts_dir / "day1_plan_voiceover.md"
    edited = json.loads(out_json.read_text(encoding="utf-8"))
    mark_user_edited(edited, edited_at="2026-11-10T00:00:00Z")
    out_json.write_text(json.dumps(edited), encoding="utf-8")
    old_json = out_json.read_bytes()
    old_md = out_md.read_bytes()
    analysis_path = cfg.texts_dir / "001_A.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["summary"] = "new harbor walk"
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    with patch("clio.tasks.scripts.generate_plan_voiceover") as mock_generate:
        with pytest.raises(ArtifactOverwriteConfirmationRequired):
            run_generate_plan_voiceovers(cfg, day_label="day1")
        mock_generate.assert_not_called()
        assert out_json.read_bytes() == old_json
        assert out_md.read_bytes() == old_md

    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover) as mock_generate:
        run_generate_plan_voiceovers(cfg, day_label="day1", confirm_artifact_overwrite=True)

    mock_generate.assert_called_once()
    result = json.loads(out_json.read_text(encoding="utf-8"))
    assert "_user_edited" not in result
    backups = sorted(cfg.scripts_dir.glob("day1_plan_voiceover.*.bak.*"))
    assert {backup.name.split(".bak.", 1)[0] for backup in backups} == {
        "day1_plan_voiceover.json",
        "day1_plan_voiceover.md",
    }


def test_plan_voiceover_cancel_does_not_write_outputs(cfg: AppConfig):
    from threading import Event

    from clio.tasks.scripts import run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg)
    cancel_event = Event()
    cancel_event.set()
    with patch("clio.tasks.scripts.generate_plan_voiceover") as mock_generate:
        result = run_generate_plan_voiceovers(cfg, day_label="day1", cancel_event=cancel_event)

    assert result is None
    mock_generate.assert_not_called()
    assert not (cfg.scripts_dir / "day1_plan_voiceover.json").exists()
    assert not (cfg.scripts_dir / "day1_plan_voiceover.md").exists()


def test_plan_voiceover_tolerates_non_list_source_inputs(cfg: AppConfig):
    from clio.tasks.scripts import run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg, source_inputs="invalid")
    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover) as mock_generate:
        result = run_generate_plan_voiceovers(cfg, day_label="day1")

    assert result is not None
    mock_generate.assert_called_once()


def test_plan_voiceover_uses_match_stem_and_segment_offset(cfg: AppConfig):
    from clio.tasks.scripts import run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg, offset_sec=12.5)
    cfg.transcripts_dir.mkdir(parents=True, exist_ok=True)
    transcript = {"segments": [{"start": 12.5, "end": 15.0, "text": "source speech"}]}
    (cfg.transcripts_dir / "A_transcript.json").write_text(json.dumps(transcript), encoding="utf-8")
    captured: dict = {}

    def fake_generate(plan, clip_data, position, template, config, **kwargs):
        captured["clip_data"] = clip_data
        captured["transcript"] = kwargs["transcript"]
        return _fake_plan_voiceover(plan, clip_data, position, template, config, **kwargs)

    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=fake_generate):
        run_generate_plan_voiceovers(cfg, day_label="day1")

    assert captured["transcript"] == transcript
    assert captured["clip_data"]["segment_offset_sec"] == 12.5


def test_plan_voiceover_artifact_overwrite_required_preserves_same_lineage_edit(cfg: AppConfig):
    from clio.artifact_guard import mark_user_edited
    from clio.tasks.scripts import plan_voiceover_artifact_overwrite_required, run_generate_plan_voiceovers

    _write_plan_voiceover_inputs(cfg)
    with patch("clio.tasks.scripts.generate_plan_voiceover", side_effect=_fake_plan_voiceover):
        run_generate_plan_voiceovers(cfg, day_label="day1")

    out_json = cfg.scripts_dir / "day1_plan_voiceover.json"
    edited = json.loads(out_json.read_text(encoding="utf-8"))
    mark_user_edited(edited)
    out_json.write_text(json.dumps(edited), encoding="utf-8")

    assert plan_voiceover_artifact_overwrite_required(cfg, "day1") == []
    overwrite = plan_voiceover_artifact_overwrite_required(cfg, "day1", overwrite=True)
    assert {path.name for path in overwrite} == {
        "day1_plan_voiceover.json",
        "day1_plan_voiceover.md",
    }

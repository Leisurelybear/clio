from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

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
    return AppConfig(
        global_cfg=GlobalConfig(
            paths=GlobalPathsConfig(ffmpeg="", ffprobe=""),
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
    )


class TestCollectTargetFiles:
    def test_file_not_found(self, cfg):
        from clio.tasks.refine import _collect_target_files

        with pytest.raises(FileNotFoundError, match="路径不存在"):
            _collect_target_files(Path("/nonexistent"), cfg.texts_dir)

    def test_single_json_file(self, cfg):
        f = cfg.texts_dir / "001_test.json"
        f.write_text("{}")
        from clio.tasks.refine import _collect_target_files

        result = _collect_target_files(f, cfg.texts_dir)
        assert result == [f]

    def test_non_json_file_raises(self, cfg):
        f = cfg.texts_dir / "test.txt"
        f.write_text("hello")
        from clio.tasks.refine import _collect_target_files

        with pytest.raises(ValueError, match="仅支持 .json"):
            _collect_target_files(f, cfg.texts_dir)

    def test_directory_globs(self, cfg):
        for name in ["001.json", "002.json", "003.txt"]:
            (cfg.texts_dir / name).write_text("{}")
        from clio.tasks.refine import _collect_target_files

        result = _collect_target_files(None, cfg.texts_dir)
        assert len(result) == 2
        assert all(f.suffix == ".json" for f in result)

    def test_empty_directory(self, cfg):
        from clio.tasks.refine import _collect_target_files

        result = _collect_target_files(None, cfg.texts_dir)
        assert result == []


class TestLoadAnalysisForScript:
    def test_voiceover_stem_matches(self, cfg):
        analysis = {"title": "test"}
        (cfg.texts_dir / "001_test.json").write_text(json.dumps(analysis))
        script_path = cfg.scripts_dir / "001_test_voiceover.json"

        from clio.tasks.refine import _load_analysis_for_script

        result = _load_analysis_for_script(script_path, cfg.texts_dir)
        assert result == analysis

    def test_no_matching_analysis(self, cfg):
        script_path = cfg.scripts_dir / "001_test_voiceover.json"
        from clio.tasks.refine import _load_analysis_for_script

        result = _load_analysis_for_script(script_path, cfg.texts_dir)
        assert result is None

    def test_non_voiceover_stem_passthrough(self, cfg):
        (cfg.texts_dir / "001_test.json").write_text('{"key": "val"}')
        script_path = cfg.scripts_dir / "001_test.json"
        from clio.tasks.refine import _load_analysis_for_script

        result = _load_analysis_for_script(script_path, cfg.texts_dir)
        assert result == {"key": "val"}


class TestRunRefineTexts:
    def test_fix_without_single_file_raises(self, cfg):
        from clio.tasks.refine import run_refine_texts

        with pytest.raises(ValueError, match="--fix 必须配合 -i"):
            run_refine_texts(cfg, path=None, fix="fix this")

    def test_fix_with_directory_raises(self, cfg):
        from clio.tasks.refine import run_refine_texts

        with pytest.raises(ValueError, match="--fix 必须配合 -i"):
            run_refine_texts(cfg, path=cfg.texts_dir, fix="fix this")

    @patch("clio.tasks.refine.refine_text")
    def test_processes_single_file(self, mock_refine, cfg):
        data = {"title": "test", "content": "hello"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_refine.return_value = {"title": "fixed", "_changelog": ["fixed title"]}

        from clio.tasks.refine import run_refine_texts

        result = run_refine_texts(cfg)
        assert result == 1
        output = json.loads((cfg.texts_dir / "001.json").read_text(encoding="utf-8"))
        assert output["title"] == "fixed"

    @patch("clio.tasks.refine.refine_text")
    def test_skip_existing_not_applied(self, mock_refine, cfg):
        data = {"title": "t"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_refine.return_value = {"title": "fixed"}

        from clio.tasks.refine import run_refine_texts

        run_refine_texts(cfg)
        assert mock_refine.call_count == 1

    @patch("clio.tasks.refine.refine_text")
    def test_error_handling_continues(self, mock_refine, cfg):
        for name in ["001.json", "002.json"]:
            (cfg.texts_dir / name).write_text('{"title": "t"}')
        mock_refine.side_effect = [RuntimeError("fail"), {"title": "ok"}]

        from clio.tasks.refine import run_refine_texts

        result = run_refine_texts(cfg)
        assert result == -1  # one failure is reported as a negative count

    @patch("clio.tasks.refine.refine_text")
    def test_all_failures_return_negative_count(self, mock_refine, cfg):
        for name in ["001.json", "002.json"]:
            (cfg.texts_dir / name).write_text('{"title": "t"}')
        mock_refine.side_effect = RuntimeError("fail")

        from clio.tasks.refine import run_refine_texts

        assert run_refine_texts(cfg) == -2

    @patch("clio.tasks.refine.refine_text")
    def test_bad_json_counts_as_failure(self, mock_refine, cfg):
        (cfg.texts_dir / "001.json").write_text("{not json")
        mock_refine.return_value = {"title": "ok"}

        from clio.tasks.refine import run_refine_texts

        assert run_refine_texts(cfg) == -1
        mock_refine.assert_not_called()

    @patch("clio.tasks.refine.refine_text")
    def test_fix_mode_passes_fix_param(self, mock_refine, cfg):
        data = {"title": "test"}
        f = cfg.texts_dir / "001.json"
        f.write_text(json.dumps(data))
        mock_refine.return_value = {"title": "fixed"}

        from clio.tasks.refine import run_refine_texts

        run_refine_texts(cfg, path=f, fix="change title")
        mock_refine.assert_called_once()
        assert mock_refine.call_args.kwargs["fix"] == "change title"

    @patch("clio.tasks.refine.refine_text")
    def test_writes_txt_file(self, mock_refine, cfg):
        data = {"title": "test", "summary": "hello"}
        (cfg.texts_dir / "001.json").write_text(json.dumps(data))
        mock_refine.return_value = {"title": "test", "summary": "world", "_changelog": ["updated text"]}

        from clio.tasks.refine import run_refine_texts

        run_refine_texts(cfg)
        txt = cfg.texts_dir / "001.txt"
        assert txt.exists()
        content = txt.read_text(encoding="utf-8")
        assert "world" in content

    @patch("clio.tasks.refine.refine_text")
    def test_files_filter(self, mock_refine, cfg):
        for name in ("001_A.json", "002_B.json", "003_C.json"):
            (cfg.texts_dir / name).write_text('{"title": "t"}')
        mock_refine.return_value = {"title": "fixed", "_changelog": []}

        from clio.tasks.refine import run_refine_texts

        result = run_refine_texts(cfg, files=["002_B"])
        assert result == 1
        mock_refine.assert_called_once()


class TestRunRefineScripts:
    def test_fix_without_single_file_raises(self, cfg):
        from clio.tasks.refine import run_refine_scripts

        with pytest.raises(ValueError, match="--fix 必须配合 -i"):
            run_refine_scripts(cfg, path=None, fix="fix this")

    @patch("clio.tasks.refine.refine_script")
    def test_processes_voiceover_files(self, mock_refine, cfg):
        data = {"voiceover": "original script"}
        (cfg.scripts_dir / "001_test_voiceover.json").write_text(json.dumps(data))
        mock_refine.return_value = {"voiceover": "fixed script", "_changelog": []}

        from clio.tasks.refine import run_refine_scripts

        result = run_refine_scripts(cfg)
        assert result == 1
        output = json.loads((cfg.scripts_dir / "001_test_voiceover.json").read_text(encoding="utf-8"))
        assert output["voiceover"] == "fixed script"

    @patch("clio.tasks.refine.refine_script")
    def test_loads_analysis_for_script(self, mock_refine, cfg):
        analysis = {"title": "test"}
        (cfg.texts_dir / "001_test.json").write_text(json.dumps(analysis))
        data = {"voiceover": "script"}
        (cfg.scripts_dir / "001_test_voiceover.json").write_text(json.dumps(data))
        mock_refine.return_value = {"voiceover": "fixed"}

        from clio.tasks.refine import run_refine_scripts

        run_refine_scripts(cfg)
        assert mock_refine.call_args.args[1] == analysis

    @patch("clio.tasks.refine.refine_script")
    def test_writes_md_file(self, mock_refine, cfg):
        data = {"voiceover": "script", "title": "test", "edit_tip": "cut here"}
        (cfg.scripts_dir / "001_test_voiceover.json").write_text(json.dumps(data))
        mock_refine.return_value = {"voiceover": "fixed", "title": "test", "edit_tip": "cut here", "_changelog": []}

        from clio.tasks.refine import run_refine_scripts

        run_refine_scripts(cfg)
        md = cfg.scripts_dir / "001_test_voiceover.md"
        assert md.exists()
        content = md.read_text(encoding="utf-8")
        assert "fixed" in content

    @patch("clio.tasks.refine.refine_script")
    def test_files_filter(self, mock_refine, cfg):
        for name in ("001_A_voiceover.json", "002_B_voiceover.json", "003_C_voiceover.json"):
            (cfg.scripts_dir / name).write_text('{"voiceover": "s"}')
        mock_refine.return_value = {"voiceover": "fixed", "_changelog": []}

        from clio.tasks.refine import run_refine_scripts

        result = run_refine_scripts(cfg, files=["002_B_voiceover"])
        assert result == 1
        mock_refine.assert_called_once()

    @patch("clio.tasks.refine.refine_script")
    def test_all_failures_return_negative_count(self, mock_refine, cfg):
        for name in ("001_A_voiceover.json", "002_B_voiceover.json"):
            (cfg.scripts_dir / name).write_text('{"voiceover": "s"}')
        mock_refine.side_effect = RuntimeError("fail")

        from clio.tasks.refine import run_refine_scripts

        assert run_refine_scripts(cfg) == -2

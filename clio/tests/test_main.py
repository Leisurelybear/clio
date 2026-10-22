"""Tests for main.py CLI subcommands."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from main import main

MINIMAL_CONFIG = """\
paths:
  input_dir: .
  output_dir: ./output
  logs_dir: ./logs
proxy:
  enabled: false
ai:
  context: ''
  providers:
    gemini:
      type: gemini
      api_key: test-gemini-key
    deepseek:
      type: openai
      api_key: test-deepseek-key
      base_url: https://api.deepseek.com/v1
  tasks:
    video_analyze:
      provider: gemini
      model: gemini-2.5-flash
    voiceover:
      provider: deepseek
      model: deepseek-chat
    vlog_plan:
      provider: deepseek
      model: deepseek-chat
compress:
  target_size_mb: 5
  max_width: 640
  fps: 15
  remove_audio: true
whisper:
  enabled: true
  model_size: small
  language: zh
  device: cpu
  transcripts_subdir: transcripts
"""


@pytest.fixture(autouse=True)
def _mock_whisper_deps(monkeypatch):
    """Mock faster_whisper import so CLI imports work."""
    fake_fw = MagicMock()
    fake_fw.__version__ = "1.0.0"
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)


@pytest.fixture
def cli_runner():
    def _run(args):
        try:
            return main(args)
        except SystemExit as e:
            return e.code

    return _run


@pytest.fixture
def config_path(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return cfg


@patch("clio.transcribe.check_whisper", return_value=True)
def test_transcribe_subcommand(mock_check_whisper, cli_runner, config_path):
    result = cli_runner(["--config", str(config_path), "transcribe"])
    assert result == 0


def test_whisper_help(cli_runner, config_path):
    result = cli_runner(["--config", str(config_path), "whisper", "--help"])
    assert result == 0


def test_whisper_check_subcommand(cli_runner, config_path):
    result = cli_runner(["--config", str(config_path), "whisper", "check"])
    assert result == 0


def test_tokens_uses_project_output(cli_runner, config_path, tmp_path, capsys):
    project = tmp_path / "project"
    project.mkdir()
    project.joinpath("project.yaml").write_text("paths:\n  output_dir: project-output\n", encoding="utf-8")
    project_output = project / "project-output"
    project_output.mkdir()
    project_output.joinpath(".token_usage.json").write_text(
        '{"total": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}, '
        '"by_model": {}, "by_task": {}, "history": []}',
        encoding="utf-8",
    )

    result = cli_runner(["--config", str(config_path), "tokens", "-p", str(project)])

    assert result == 0
    assert '"total_tokens": 10' in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["-p", "-i"])
def test_missing_explicit_path_is_rejected(cli_runner, config_path, tmp_path, capsys, flag):
    missing = tmp_path / "missing"

    result = cli_runner(["--config", str(config_path), "compress", flag, str(missing)])

    assert result == 2
    assert "不存在" in capsys.readouterr().err


def test_project_path_must_be_directory(cli_runner, config_path, tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    result = cli_runner(["--config", str(config_path), "compress", "-p", str(video)])

    assert result == 2
    assert "不是目录" in capsys.readouterr().err


def test_input_file_rejected_for_directory_command(cli_runner, config_path, tmp_path, capsys):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake")

    result = cli_runner(["--config", str(config_path), "transcribe", "-i", str(video)])

    assert result == 2
    assert "需要目录" in capsys.readouterr().err


def test_custom_config_uses_sibling_project(cli_runner, tmp_path, capsys):
    config_root = tmp_path / "configs"
    config_root.mkdir()
    config_path = config_root / "config.yaml"
    config_path.write_text(MINIMAL_CONFIG, encoding="utf-8")
    config_root.joinpath("project.yaml").write_text("paths:\n  output_dir: side-output\n", encoding="utf-8")
    side_output = config_root / "side-output"
    side_output.mkdir()
    side_output.joinpath(".token_usage.json").write_text(
        '{"total": {"prompt_tokens": 2, "completion_tokens": 5, "total_tokens": 7}, '
        '"by_model": {}, "by_task": {}, "history": []}',
        encoding="utf-8",
    )

    result = cli_runner(["--config", str(config_path), "tokens"])

    assert result == 0
    assert '"total_tokens": 7' in capsys.readouterr().out


@patch("clio.plan_readiness.collect_project_indices", return_value=({"001"}, set()))
@patch("clio.export.export_plan")
def test_export_resolves_ffprobe(mock_export, _mock_indices, cli_runner, config_path, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    project.joinpath("project.yaml").write_text("paths:\n  output_dir: out\n", encoding="utf-8")
    out = project / "out"
    plan_dir = out / "plans"
    plan_dir.mkdir(parents=True)
    plan_dir.joinpath("day1_plan.json").write_text(
        json.dumps(
            {
                "total_estimated_sec": 180,
                "sequence": [
                    {
                        "index": "001",
                        "title": "开场",
                        "reason": "建立场景",
                        "use_timeline": "00:00-00:02",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with patch("clio.main.resolve_binary", return_value="C:/tools/ffprobe.exe"):
        result = cli_runner(["--config", str(config_path), "export", "-p", str(project)])

    assert result == 0
    assert mock_export.call_args.kwargs["ffprobe"] == "C:/tools/ffprobe.exe"


@patch("clio.main.run_doctor")
def test_doctor_subcommand(mock_run_doctor, cli_runner, config_path):
    mock_run_doctor.return_value = 0
    result = cli_runner(["--config", str(config_path), "doctor"])
    assert result == 0
    mock_run_doctor.assert_called_once_with(config_path, None)


@patch("clio.main.run_doctor")
def test_doctor_subcommand_handles_invalid_config_in_doctor(mock_run_doctor, cli_runner, tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("proxy:\n  enabled: true\n  url: ''\n", encoding="utf-8")
    mock_run_doctor.return_value = 1

    result = cli_runner(["--config", str(cfg), "doctor"])

    assert result == 1
    mock_run_doctor.assert_called_once_with(cfg, None)


@patch("clio.desktop.app.main")
def test_desktop_subcommand(mock_run_desktop, cli_runner, config_path):
    """desktop subcommand dispatches to clio.desktop.app.main with config_path."""
    mock_run_desktop.return_value = 0
    result = cli_runner(["--config", str(config_path), "desktop"])
    assert result == 0
    mock_run_desktop.assert_called_once_with(config_path=config_path)


@patch("clio.desktop.app.main")
def test_desktop_subcommand_without_config_dispatches(mock_run_desktop, cli_runner, tmp_path):
    """Missing config.yaml must not block desktop; app.main auto-generates it."""
    missing = tmp_path / "config.yaml"
    mock_run_desktop.return_value = 0
    result = cli_runner(["--config", str(missing), "desktop"])
    assert result == 0
    mock_run_desktop.assert_called_once_with(config_path=missing)


@patch("clio.tasks.transcribe.run_transcribe_all")
def test_transcribe_force_flag(mock_run, cli_runner, config_path):
    """--force 应设置 skip_existing=False"""
    cli_runner(["--config", str(config_path), "transcribe", "--force"])
    cfg_arg = mock_run.call_args[0][0]
    assert cfg_arg.analyze.skip_existing is False


@patch("clio.tasks.transcribe.run_transcribe_all")
def test_transcribe_default(mock_run, cli_runner, config_path):
    """默认 transcribe 应保持 skip_existing=True"""
    cli_runner(["--config", str(config_path), "transcribe"])
    cfg_arg = mock_run.call_args[0][0]
    assert cfg_arg.analyze.skip_existing is True


@patch("clio.pipeline.run_analyze_all")
@patch("clio.tasks.reindex.auto_reindex_if_needed")
def test_force_flag_after_subcommand(mock_reindex, mock_analyze, cli_runner, config_path):
    """The documented `analyze --force` form must be accepted."""
    result = cli_runner(["--config", str(config_path), "analyze", "--force"])

    assert result == 0
    mock_reindex.assert_called_once()
    cfg_arg = mock_analyze.call_args[0][0]
    assert cfg_arg.analyze.skip_existing is False


@patch("clio.pipeline.run_refine_scripts")
@patch("clio.pipeline.run_refine_texts")
def test_refine_accepts_project_directory(mock_texts, mock_scripts, cli_runner, config_path, tmp_path):
    project = tmp_path / "refine-project"
    project.mkdir()

    result = cli_runner(["--config", str(config_path), "refine", "-p", str(project), "--target", "texts"])

    assert result == 0
    cfg_arg = mock_texts.call_args[0][0]
    assert cfg_arg.project_dir == project.resolve()
    mock_scripts.assert_not_called()


@patch("clio.main.load_config")
@patch("clio.tasks.transcribe.run_transcribe_all")
def test_transcribe_with_input(mock_run, mock_load, cli_runner, config_path, tmp_path):
    """-i 参数应传递给 load_config 作为 project_dir"""
    inp = tmp_path / "my_videos"
    inp.mkdir()
    cli_runner(["--config", str(config_path), "transcribe", "-i", str(inp)])
    mock_load.assert_any_call(config_path, project_dir=inp)


@patch("clio.pipeline.run_plan_vlog")
def test_plan_no_transcripts_flag(mock_run_plan, cli_runner, config_path, tmp_path):
    """--no-transcripts 应设置 config.plan.use_transcripts=False"""
    result = cli_runner(["--config", str(config_path), "plan", "--no-transcripts"])
    assert result == 0
    cfg_arg = mock_run_plan.call_args[0][0]
    assert cfg_arg.plan.use_transcripts is False


@patch("clio.pipeline.run_plan_all_days")
def test_plan_all_days(mock_run_plan_all_days, cli_runner, config_path):
    result = cli_runner(["--config", str(config_path), "plan", "--all-days", "--no-transcripts"])
    assert result == 0
    cfg_arg = mock_run_plan_all_days.call_args[0][0]
    assert cfg_arg.plan.use_transcripts is False


@patch("clio.pipeline.run_full_pipeline", side_effect=RuntimeError("语音转录失败（退出码 1）"))
def test_full_run_failure_returns_nonzero(mock_run_full, cli_runner, config_path):
    result = cli_runner(["--config", str(config_path), "run"])

    assert result == 1
    mock_run_full.assert_called_once()

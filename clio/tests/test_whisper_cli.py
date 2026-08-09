from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from clio.whisper_cli import run_whisper_check, run_whisper_install

MINIMAL_CONFIG = """\
whisper:
  enabled: true
  model_size: small
  language: zh
  device: cpu
  transcripts_subdir: transcripts
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
      api_key: test
    deepseek:
      type: openai
      api_key: test
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
"""


@pytest.fixture
def config_file(tmp_path):
    cf = tmp_path / "config.yaml"
    cf.write_text(MINIMAL_CONFIG, encoding="utf-8")
    return cf


class TestRunWhisperCheck:
    @patch("clio.whisper_cli.load_config")
    @patch("builtins.print")
    def test_installed_cuda(self, mock_print, mock_load_config, config_file):
        mock_load_config.return_value.whisper.hf_endpoint = ""
        mock_load_config.return_value.whisper.model_size = "small"
        mock_load_config.return_value.whisper.cache_dir = None

        fake_fw = MagicMock()
        fake_fw.__version__ = "1.0.0"
        with (
            patch("clio.whisper_cli._resolve_cache_dir") as mock_cache,
            patch("ctranslate2.get_cuda_device_count", return_value=1),
            patch.dict("sys.modules", {"faster_whisper": fake_fw}),
        ):
            mock_cache.return_value = config_file.parent / "models"
            result = run_whisper_check(str(config_file))
            assert result == 0
            printed = [c[0][0] for c in mock_print.call_args_list]
            assert any("CUDA" in p and "✔" in p for p in printed)

    @patch("clio.whisper_cli.load_config")
    @patch("builtins.print")
    def test_installed_no_cuda(self, mock_print, mock_load_config, config_file):
        mock_load_config.return_value.whisper.hf_endpoint = ""
        mock_load_config.return_value.whisper.model_size = "small"
        mock_load_config.return_value.whisper.cache_dir = None
        mock_load_config.return_value.whisper.transcripts_subdir = "transcripts"

        fake_fw = MagicMock()
        fake_fw.__version__ = "1.0.0"
        with (
            patch("clio.whisper_cli._resolve_cache_dir") as mock_cache,
            patch("ctranslate2.get_cuda_device_count", return_value=0),
            patch.dict("sys.modules", {"faster_whisper": fake_fw}),
        ):
            mock_cache.return_value = config_file.parent / "models"
            result = run_whisper_check(str(config_file))
            assert result == 0

    @patch("builtins.print")
    def test_not_installed(self, mock_print, config_file):
        import builtins

        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "faster_whisper":
                raise ImportError("no module named faster_whisper")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=_fake_import):
            result = run_whisper_check(str(config_file))
            assert result == 1
            printed = [c[0][0] for c in mock_print.call_args_list]
            assert any("未安装" in p for p in printed)


class TestRunWhisperInstall:
    @patch("clio.whisper_cli.load_config")
    @patch("clio.whisper_cli.run_subprocess")
    @patch("builtins.print")
    def test_success(self, mock_print, mock_subproc, mock_load_config, config_file):
        mock_subproc.return_value = MagicMock(returncode=0)
        mock_load_config.return_value.whisper.hf_endpoint = ""
        mock_load_config.return_value.whisper.model_size = "small"
        mock_load_config.return_value.whisper.cache_dir = None

        mock_dl = MagicMock(return_value=str(config_file.parent / "models" / "snapshots"))
        with (
            patch("clio.whisper_cli._resolve_cache_dir") as mock_cache,
            patch("clio.whisper_cli.PROJECT_ROOT", config_file.parent),
            patch("clio.whisper_cli._snapshot_download", mock_dl),
            patch("clio.whisper_cli.check_cublas", return_value=True),
            patch("clio.whisper_cli._get_model", return_value=MagicMock()),
            patch("clio.whisper_cli._reload_whisper_import", return_value=True) as mock_reload,
            patch("ctranslate2.get_cuda_device_count", return_value=0),
        ):
            mock_cache.return_value = config_file.parent / "models"
            req = config_file.parent / "requirements-whisper.txt"
            req.write_text("faster-whisper==1.0.0")
            result = run_whisper_install(str(config_file))
            assert result == 0
            mock_dl.assert_called_once()
            mock_reload.assert_called_once()
            args, kwargs = mock_dl.call_args
            assert "Systran/faster-whisper-small" in str(kwargs["repo_id"])

    @patch("clio.whisper_cli.load_config")
    @patch("builtins.print")
    def test_missing_requirements(self, mock_print, mock_load_config, config_file):
        mock_load_config.return_value.whisper.hf_endpoint = ""
        mock_load_config.return_value.whisper.model_size = "small"
        mock_load_config.return_value.whisper.cache_dir = None

        with patch("clio.whisper_cli.PROJECT_ROOT", config_file.parent):
            result = run_whisper_install(str(config_file))
            assert result == 1
            printed = [c[0][0] for c in mock_print.call_args_list]
            assert any("未找到依赖文件" in p for p in printed)

    @patch("clio.whisper_cli.load_config")
    @patch("clio.whisper_cli.run_subprocess")
    @patch("builtins.print")
    def test_pip_failure(self, mock_print, mock_subproc, mock_load_config, config_file):
        mock_subproc.return_value = MagicMock(returncode=1, stderr="pip error")
        mock_load_config.return_value.whisper.hf_endpoint = ""
        mock_load_config.return_value.whisper.model_size = "small"
        mock_load_config.return_value.whisper.cache_dir = None

        with (
            patch("clio.whisper_cli.PROJECT_ROOT", config_file.parent),
            patch("ctranslate2.get_cuda_device_count", return_value=0),
        ):
            req = config_file.parent / "requirements-whisper.txt"
            req.write_text("faster-whisper==1.0.0")
            result = run_whisper_install(str(config_file))
            assert result == 1
            printed = [c[0][0] for c in mock_print.call_args_list]
            assert any("安装失败" in p for p in printed)

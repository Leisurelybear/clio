from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.config import AppConfig, WhisperConfig
from clio.config.models import (
    AnalyzeConfig,
    GlobalConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProjectWhisperConfig,
)
from clio.tasks.transcribe import run_transcribe_all
from clio.transcribe import (
    _get_model,
    _resolve_cache_dir,
    _resolve_compute_types,
    _resolve_device,
    check_cublas,
    check_whisper,
    transcribe_audio,
)


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig(
        global_cfg=GlobalConfig(),
        project_cfg=ProjectConfig(
            whisper=ProjectWhisperConfig(enabled=True, model_size="small", language="zh", device="cpu"),
        ),
    )


def test_check_cublas_returns_bool():
    assert isinstance(check_cublas(), bool)


def test_check_whisper_false_without_cublas():
    with patch("clio.transcribe.check_cublas", return_value=False):
        assert check_whisper() is False


def test_check_whisper_true_with_cublas_and_import():
    with (
        patch("clio.transcribe.check_cublas", return_value=True),
        patch.dict("sys.modules", {"faster_whisper": MagicMock()}),
    ):
        assert check_whisper() is True


class TestResolveCacheDir:
    def test_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.transcribe.PROJECT_ROOT", tmp_path)
        result = _resolve_cache_dir(MagicMock(whisper=WhisperConfig(cache_dir="")))
        assert result == tmp_path / "models"

    def test_custom(self, tmp_path):
        custom = tmp_path / "whisper_cache"
        result = _resolve_cache_dir(MagicMock(whisper=WhisperConfig(cache_dir=str(custom))))
        assert result == custom


class TestResolveDevice:
    def test_cpu(self):
        assert _resolve_device(MagicMock(whisper=WhisperConfig(device="cpu"))) == "cpu"

    def test_cuda(self):
        assert _resolve_device(MagicMock(whisper=WhisperConfig(device="cuda"))) == "cuda"

    @patch("ctranslate2.get_cuda_device_count")
    def test_auto_cpu(self, mock_cuda_count):
        mock_cuda_count.return_value = 0
        assert _resolve_device(MagicMock(whisper=WhisperConfig(device="auto"))) == "cpu"

    @patch("ctranslate2.get_cuda_device_count")
    def test_auto_cuda(self, mock_cuda_count):
        mock_cuda_count.return_value = 1
        assert _resolve_device(MagicMock(whisper=WhisperConfig(device="auto"))) == "cuda"


class TestResolveComputeType:
    def test_cpu(self):
        assert _resolve_compute_types("cpu") == ["int8", "default"]

    def test_cuda(self):
        assert _resolve_compute_types("cuda") == ["int8_float16", "float16", "default"]


class TestGetModel:
    @patch("clio.transcribe.WhisperModel")
    def test_singleton(self, mock_whisper_cls):
        cfg = MagicMock(whisper=WhisperConfig(model_size="small", cache_dir="/cache"))
        with (
            patch("clio.transcribe._whisper_model", None),
            patch("clio.transcribe._whisper_cache_key", None),
        ):
            m1 = _get_model(cfg)
            m2 = _get_model(cfg)
            assert m1 is m2
            mock_whisper_cls.assert_called_once()

    @patch("clio.transcribe.WhisperModel", None)
    @patch("clio.transcribe._reload_whisper_import", return_value=False)
    @patch("clio.transcribe._resolve_cache_dir")
    @patch("clio.transcribe._resolve_device", return_value="cpu")
    def test_get_model_import_error(self, mock_dev, mock_cache, mock_reload):
        """WhisperModel 未安装时抛 ImportError"""
        with pytest.raises(ImportError):
            _get_model(MagicMock(whisper=WhisperConfig(model_size="small")))

    @patch("clio.transcribe.WhisperModel")
    def test_get_model_cache_invalidation(self, mock_whisper_cls):
        """model_size 或 cache_dir 变化时重新加载模型"""
        mock_whisper_cls.side_effect = lambda *a, **kw: MagicMock()
        cfg1 = MagicMock(whisper=WhisperConfig(model_size="small", cache_dir="/cache1"))
        cfg2 = MagicMock(whisper=WhisperConfig(model_size="medium", cache_dir="/cache1"))
        with (
            patch("clio.transcribe._whisper_model", None),
            patch("clio.transcribe._whisper_cache_key", None),
        ):
            m1 = _get_model(cfg1)
            m2 = _get_model(cfg2)
            assert m1 is not m2
            assert mock_whisper_cls.call_count == 2

    @patch("clio.transcribe.WhisperModel")
    def test_cuda_fallback_on_value_error(self, mock_whisper_cls):
        """CUDA 所有 compute type 失败后回退到 CPU"""
        mock_whisper_cls.side_effect = [
            ValueError("int8_float16 error"),
            ValueError("float16 error"),
            ValueError("default error"),
            MagicMock(),
        ]
        cfg = MagicMock(whisper=WhisperConfig(model_size="small", cache_dir="/cache"))
        with (
            patch("clio.transcribe._whisper_model", None),
            patch("clio.transcribe._whisper_cache_key", None),
            patch("clio.transcribe._resolve_device", return_value="cuda"),
        ):
            result = _get_model(cfg)
            assert mock_whisper_cls.call_count == 4
            _, last_kwargs = mock_whisper_cls.call_args
            assert last_kwargs["device"] == "cpu"
            assert result is not None

    @patch("clio.transcribe.WhisperModel")
    def test_cuda_fallback_on_unexpected_exception(self, mock_whisper_cls):
        """CUDA 下 unexpected 异常（如 cuBLAS dll 缺失）也回退到 CPU"""
        mock_whisper_cls.side_effect = [
            TypeError("Library cublas64_12.dll is not found or cannot be loaded"),
            MagicMock(),
        ]
        cfg = MagicMock(whisper=WhisperConfig(model_size="small", cache_dir="/cache"))
        with (
            patch("clio.transcribe._whisper_model", None),
            patch("clio.transcribe._whisper_cache_key", None),
            patch("clio.transcribe._resolve_device", return_value="cuda"),
        ):
            result = _get_model(cfg)
            assert mock_whisper_cls.call_count == 2
            _, last_kwargs = mock_whisper_cls.call_args
            assert last_kwargs["device"] == "cpu"
            assert result is not None

    @patch("clio.transcribe.WhisperModel")
    def test_cuda_fallback_re_raises_on_non_cuda(self, mock_whisper_cls):
        """非 CUDA 设备抛异常时不回退"""
        mock_whisper_cls.side_effect = ValueError("model error")
        cfg = MagicMock(whisper=WhisperConfig(model_size="small", cache_dir="/cache"))
        with (
            patch("clio.transcribe._whisper_model", None),
            patch("clio.transcribe._whisper_cache_key", None),
            patch("clio.transcribe._resolve_device", return_value="cpu"),
            pytest.raises(ValueError, match="model error"),
        ):
            _get_model(cfg)

    @patch("clio.transcribe.WhisperModel")
    def test_sets_hf_endpoint_env(self, mock_whisper_cls):
        """HF_ENDPOINT 环境变量在模型加载期间被正确设置（finally 会恢复）"""
        import os

        captured = {}

        def _capture_env(*args, **kwargs):
            captured["HF_ENDPOINT"] = os.environ.get("HF_ENDPOINT")
            return MagicMock()

        mock_whisper_cls.side_effect = _capture_env
        cfg = MagicMock(
            whisper=WhisperConfig(model_size="small", cache_dir="/cache", hf_endpoint="https://hf-mirror.com")
        )
        old_val = os.environ.pop("HF_ENDPOINT", None)
        try:
            with (
                patch("clio.transcribe._whisper_model", None),
                patch("clio.transcribe._whisper_cache_key", None),
            ):
                _get_model(cfg)
                assert captured.get("HF_ENDPOINT") == "https://hf-mirror.com"
                assert os.environ.get("HF_ENDPOINT") != "https://hf-mirror.com"  # finally 已恢复
        finally:
            if old_val is not None:
                os.environ["HF_ENDPOINT"] = old_val
            else:
                os.environ.pop("HF_ENDPOINT", None)


class TestTranscribeAudio:
    @patch("clio.transcribe._get_model")
    def test_segments(self, mock_get_model):
        mock_model = MagicMock()
        seg1 = MagicMock(
            start=0.0,
            end=2.5,
            text=" 今天天气真好 ",
            avg_logprob=-0.1,
            no_speech_prob=0.01,
        )
        seg2 = MagicMock(
            start=2.5,
            end=5.0,
            text=" 我们来了 ",
            avg_logprob=-0.3,
            no_speech_prob=0.02,
        )
        seg3 = MagicMock(
            start=5.0,
            end=7.0,
            text=" 低置信度 ",
            avg_logprob=-0.9,
            no_speech_prob=0.5,
        )
        mock_model.transcribe.return_value = (
            [seg1, seg2, seg3],
            MagicMock(language="zh", language_probability=0.95, duration=100.0),
        )
        mock_get_model.return_value = mock_model

        callback = MagicMock()
        result = transcribe_audio(
            Path("/fake.wav"),
            MagicMock(whisper=WhisperConfig(language="zh")),
            callback,
        )

        assert len(result) == 3
        assert result[0]["start"] == 0.0
        assert result[0]["text"] == "今天天气真好"
        assert "low_confidence" not in result[0]
        assert result[1]["start"] == 2.5
        assert "low_confidence" not in result[1]
        assert result[2]["start"] == 5.0
        assert result[2]["low_confidence"] is True
        assert result[2]["text"] == "低置信度"
        callback.assert_called()

    @patch("clio.transcribe._get_model")
    def test_auto_language(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            [],
            MagicMock(language="en", language_probability=0.99, duration=50.0),
        )
        mock_get_model.return_value = mock_model

        result = transcribe_audio(
            Path("/fake.wav"),
            MagicMock(whisper=WhisperConfig(language="auto")),
        )
        assert result == []

        _, kwargs = mock_model.transcribe.call_args
        assert kwargs["language"] is None


class TestRunTranscribeAll:
    def test_transcribe_enabled_check_no_deps(self):
        """当 faster-whisper 不可导入时，run_transcribe_all 打印警告并返回 1（错误退出码）"""
        config = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(
                whisper=ProjectWhisperConfig(enabled=True),
            ),
        )

        with (
            patch("clio.tasks.transcribe.check_whisper", return_value=False),
            patch("builtins.print"),
        ):
            result = run_transcribe_all(config)
            assert result == 1

    def test_transcribe_skipped_when_disabled(self):
        """whisper.enabled=False 时跳过转录"""
        config = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(
                whisper=ProjectWhisperConfig(enabled=False),
            ),
        )

        with patch("builtins.print"):
            result = run_transcribe_all(config)
            assert result == 0

    def test_cancel_during_extract_marks_cancelled(self):
        """_extract_audio 因取消返回 None 时标记为 cancelled 并中止"""
        from threading import Event

        cancel_event = Event()
        config = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(
                paths=ProjectPathsConfig(
                    output_dir=MagicMock(),
                ),
                whisper=ProjectWhisperConfig(enabled=True, model_size="small", language="zh", device="cpu"),
                analyze=AnalyzeConfig(skip_existing=False),
            ),
        )

        mock_state = MagicMock()

        def _extract_and_cancel(*a, **kw):
            cancel_event.set()
            return None

        with (
            patch("clio.tasks.transcribe.check_whisper", return_value=True),
            patch("clio.tasks.transcribe.find_videos", return_value=[Path("test.mp4")]),
            patch("clio.tasks.transcribe._build_original_stem_map", return_value={"test": Path("test.mp4")}),
            patch("clio.tasks.transcribe._extract_audio", _extract_and_cancel),
            patch("clio.tasks.transcribe.ProcessingState", return_value=mock_state),
            patch("clio.tasks.transcribe.resolve_binary", return_value="ffmpeg"),
            patch("builtins.print"),
        ):
            run_transcribe_all(config, cancel_event=cancel_event)

        # Verify state.mark() was called with "cancelled", not "skipped"
        state_calls = [c for c in mock_state.mark.call_args_list if c[0][1] == "transcribe"]
        assert any(c[0][2] == "cancelled" for c in state_calls), f"Expected 'cancelled' state, got: {state_calls}"

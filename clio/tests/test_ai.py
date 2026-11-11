"""Tests for clio/ai/* — factory, gemini, openai_compat."""

from __future__ import annotations

import pytest

from clio.ai.base import TaskName
from clio.ai.factory import _build_provider, get_task_config, get_task_provider, get_video_provider
from clio.ai.gemini import GeminiProvider
from clio.ai.openai_compat import OpenAICompatProvider
from clio.config import TaskConfig


class TestTaskName:
    def test_values(self):
        assert TaskName.VIDEO_ANALYZE == "video_analyze"
        assert TaskName.VOICEOVER == "voiceover"
        assert TaskName.VLOG_PLAN == "vlog_plan"
        assert TaskName.REFINE_TEXT == "refine_text"


class TestGetTaskConfig:
    def test_known_task(self, loaded_config):
        cfg = get_task_config(loaded_config, "video_analyze")
        assert isinstance(cfg, TaskConfig)
        assert cfg.provider == "gemini"
        assert cfg.model == "gemini-2.5-flash"

    def test_unknown_task(self, loaded_config):
        with pytest.raises(ValueError, match="未配置 AI 任务"):
            get_task_config(loaded_config, "nonexistent")

    def test_voiceover_task_uses_deepseek(self, loaded_config):
        cfg = get_task_config(loaded_config, TaskName.VOICEOVER)
        assert cfg.provider == "deepseek"
        assert cfg.model == "deepseek-chat"

    def test_vlog_plan_task_uses_deepseek(self, loaded_config):
        cfg = get_task_config(loaded_config, TaskName.VLOG_PLAN)
        assert cfg.provider == "deepseek"
        assert cfg.model == "deepseek-chat"


class TestBuildProvider:
    def test_unknown_provider_name(self, loaded_config):
        with pytest.raises(ValueError, match="未定义的 AI 厂家"):
            _build_provider(loaded_config, "nonexistent")

    def test_ollama_maps_to_openai_compat(self):
        import clio.ai.factory as factory

        assert factory._PROVIDER_TYPES["ollama"] is OpenAICompatProvider

    def test_unsupported_provider_type(self, loaded_config, monkeypatch):
        monkeypatch.setattr(loaded_config.ai.providers["gemini"], "type", "nonexistent")
        with pytest.raises(ValueError, match="不支持的厂家类型"):
            _build_provider(loaded_config, "gemini")

    def test_expired_provider_closes_after_active_call(self, loaded_config, monkeypatch):
        import clio.ai.factory as factory

        class FakeProvider:
            provider_id = "fake"

            def __init__(self, *_args):
                self.close_count = 0

            def close(self):
                self.close_count += 1

        instances = []

        def build(*args):
            provider = FakeProvider(*args)
            instances.append(provider)
            return provider

        monkeypatch.setitem(factory._PROVIDER_TYPES, "gemini", build)
        loaded_config.global_cfg.ai.provider_ttl_min = 1
        clock = iter((0.0, 61.0, 62.0))
        monkeypatch.setattr(factory.time, "monotonic", lambda: next(clock))

        first = _build_provider(loaded_config, "gemini")
        with factory.provider_use(first):
            second = _build_provider(loaded_config, "gemini")
            assert second is not first
            assert instances[0].close_count == 0

        assert instances[0].close_count == 1


class TestGetTaskProvider:
    def test_gemini_task(self, loaded_config):
        provider, model = get_task_provider(loaded_config, TaskName.VIDEO_ANALYZE)
        assert model == "gemini-2.5-flash"
        assert isinstance(provider, GeminiProvider)

    def test_deepseek_task(self, loaded_config):
        provider, model = get_task_provider(loaded_config, TaskName.VOICEOVER)
        assert model == "deepseek-chat"
        assert isinstance(provider, OpenAICompatProvider)

    def test_task_by_string(self, loaded_config):
        provider, model = get_task_provider(loaded_config, "video_analyze")
        assert model == "gemini-2.5-flash"
        assert isinstance(provider, GeminiProvider)


class TestGetVideoProvider:
    def test_gemini_supports_video(self, loaded_config):
        provider, model = get_video_provider(loaded_config, TaskName.VIDEO_ANALYZE)
        assert hasattr(provider, "analyze_video")
        assert model == "gemini-2.5-flash"
        assert isinstance(provider, GeminiProvider)


class TestOpenAICompatHasNoAnalyzeVideo:
    """OpenAICompatProvider has analyze_video but it raises NotImplementedError."""

    def test_analyze_video_raises(self, loaded_config):
        provider, model = get_task_provider(loaded_config, TaskName.VOICEOVER)
        assert isinstance(provider, OpenAICompatProvider)
        with pytest.raises(NotImplementedError):
            provider.analyze_video("fake_file", "test prompt", "deepseek-chat")

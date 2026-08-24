from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.asr.base import ProviderCapabilities, TranscriptSegment
from clio.asr.factory import list_providers, register_provider


def _make_config(engine: str = "mock"):
    cfg = MagicMock()
    whisper = MagicMock()
    whisper.engine = engine
    whisper.language = "zh"
    cfg.attach_mock(whisper, "whisper")
    return cfg


@register_provider
class MockProvider:
    capabilities = ProviderCapabilities(id="mock", display_name="Mock")

    def __init__(self, config):
        self._config = config

    def transcribe(self, audio_path, language, progress_callback=None, cancel_event=None):
        if progress_callback:
            progress_callback(50)
        return [TranscriptSegment(start=0.5, end=3.2, text="大家好")]


class UnclassProvider:
    pass


def test_register_provider_adds_to_registry():
    caps_list = list_providers()
    assert any(c.id == "mock" for c in caps_list)
    assert any(c.id == "local" for c in caps_list)


def test_register_provider_rejects_missing_capabilities():
    with pytest.raises(TypeError):
        register_provider(UnclassProvider)


def test_transcript_segment_to_dict():
    seg = TranscriptSegment(start=0.1234, end=3.2567, text=" 你好 ")
    assert seg.to_dict() == {"start": 0.12, "end": 3.26, "text": "你好", "avg_logprob": 0.0}


def test_transcript_segment_low_confidence_flag():
    seg = TranscriptSegment(start=0, end=1, text="x", low_confidence=True)
    assert seg.to_dict()["low_confidence"] is True


def test_engine_dispatches_to_registered_provider():
    cfg = _make_config()
    from clio.transcribe import transcribe_audio

    with patch("clio.asr.factory._PROVIDERS", {"mock": MockProvider}):
        result = transcribe_audio(Path("fake.wav"), cfg)
    assert result == [{"start": 0.5, "end": 3.2, "text": "大家好", "avg_logprob": 0.0}]


def test_progress_callbacks_fire():
    cfg = _make_config()
    events = []
    from clio.transcribe import transcribe_audio

    with patch("clio.asr.factory._PROVIDERS", {"mock": MockProvider}):
        transcribe_audio(Path("fake.wav"), cfg, progress_callback=events.append)
    assert events == [0, 50]


def test_factory_unknown_engine_raises():
    from clio.asr.factory import build_provider

    with pytest.raises(RuntimeError, match="未知的 ASR engine"):
        build_provider("nonexistent", _make_config())


def test_factory_empty_engine_raises():
    from clio.asr.factory import build_provider

    with pytest.raises(RuntimeError, match="engine 未配置"):
        build_provider("", _make_config())

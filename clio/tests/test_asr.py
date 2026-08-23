from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.asr.base import TranscriptSegment
from clio.transcribe import _transcribe_cloud, transcribe_audio


def _make_config(engine: str = "cloud", provider: str = "mock"):
    cfg = MagicMock()
    whisper = MagicMock()
    whisper.engine = engine
    whisper.cloud_provider = provider
    whisper.language = "zh"
    cfg.attach_mock(whisper, "whisper")
    return cfg


class MockProvider:
    def transcribe(self, audio_path, language, progress_callback=None):
        if progress_callback:
            progress_callback(50)
        return [TranscriptSegment(start=0.5, end=3.2, text="大家好")]


def test_transcript_segment_to_dict():
    seg = TranscriptSegment(start=0.1234, end=3.2567, text=" 你好 ")
    assert seg.to_dict() == {"start": 0.12, "end": 3.26, "text": "你好", "avg_logprob": 0.0}


def test_transcript_segment_low_confidence_flag():
    seg = TranscriptSegment(start=0, end=1, text="x", low_confidence=True)
    assert seg.to_dict()["low_confidence"] is True


def test_engine_local_falls_through_to_whisper():
    cfg = _make_config(engine="local")
    with patch("clio.transcribe._get_model") as mock_get:
        model = MagicMock()
        model.transcribe.return_value = (iter([]), MagicMock(duration=10))
        mock_get.return_value = model
        result = transcribe_audio(Path("fake.wav"), cfg)
    assert result == []


def test_engine_cloud_dispatches_to_provider():
    cfg = _make_config()
    with patch("clio.asr.factory.build_provider", return_value=MockProvider()) as mock_build:
        result = transcribe_audio(Path("fake.wav"), cfg)
    mock_build.assert_called_once()
    args = mock_build.call_args
    assert args[0][0] == "mock"
    assert result == [{"start": 0.5, "end": 3.2, "text": "大家好", "avg_logprob": 0.0}]


def test_cloud_progress_callbacks_fire():
    cfg = _make_config()
    events = []
    with patch("clio.asr.factory.build_provider", return_value=MockProvider()):
        _transcribe_cloud(Path("fake.wav"), cfg, progress_callback=events.append)
    assert events == [0, 50, 100]


def test_factory_unknown_provider_raises():
    from clio.asr.factory import build_provider

    with pytest.raises(RuntimeError, match="未知的云端 ASR provider"):
        build_provider("nonexistent", _make_config())


def test_factory_empty_provider_raises():
    from clio.asr.factory import build_provider

    with pytest.raises(RuntimeError, match="provider 未配置"):
        build_provider("", _make_config())

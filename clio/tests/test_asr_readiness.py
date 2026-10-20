from __future__ import annotations

from unittest.mock import MagicMock, patch

from clio.asr.readiness import check_asr_ready


def _config(engine: str):
    cfg = MagicMock()
    cfg.whisper.engine = engine
    return cfg


def test_local_ready_when_whisper_is_available():
    with patch("clio.transcribe.check_whisper", return_value=True):
        readiness = check_asr_ready(_config("local"))

    assert readiness.ready is True
    assert readiness.error is None
    assert readiness.display_name == "本地 faster-whisper"


def test_local_reports_actionable_error_when_whisper_is_missing():
    with patch("clio.transcribe.check_whisper", return_value=False):
        readiness = check_asr_ready(_config("local"))

    assert readiness.ready is False
    assert "faster-whisper" in (readiness.error or "")
    assert "whisper install" in (readiness.error or "")


def test_cloud_engine_only_checks_cloud_credentials():
    with (
        patch("clio.transcribe.check_whisper") as check_whisper,
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}, clear=False),
    ):
        readiness = check_asr_ready(_config("aliyun"), environ={"DASHSCOPE_API_KEY": "test-key"})

    assert readiness.ready is True
    assert readiness.display_name == "阿里云 Paraformer"
    check_whisper.assert_not_called()


def test_cloud_engine_reports_missing_api_key_without_local_check():
    with patch("clio.transcribe.check_whisper") as check_whisper:
        readiness = check_asr_ready(_config("aliyun"), environ={})

    assert readiness.ready is False
    assert "DASHSCOPE_API_KEY" in (readiness.error or "")
    check_whisper.assert_not_called()


def test_unknown_engine_returns_registry_error_instead_of_raising():
    readiness = check_asr_ready(_config("missing"), environ={})

    assert readiness.ready is False
    assert "未知的 ASR engine" in (readiness.error or "")

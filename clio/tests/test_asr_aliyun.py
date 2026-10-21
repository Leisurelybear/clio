from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clio.asr.aliyun import AliyunASRProvider
from clio.asr.factory import list_providers


@pytest.fixture(autouse=True)
def _patch_interval(monkeypatch):
    monkeypatch.setattr("clio.asr.aliyun._POLL_INTERVAL_S", 0.01)


def test_capabilities_registered():
    caps = {c.id: c for c in list_providers()}
    assert "aliyun" in caps
    assert caps["aliyun"].supports_local_file is True
    assert caps["aliyun"].requires_public_url is False


def test_missing_api_key_raises():
    provider = AliyunASRProvider(MagicMock())
    with patch.dict("os.environ", {"DASHSCOPE_API_KEY": ""}, clear=False):
        with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
            provider.transcribe(None, "zh")


def _mock_upload():
    return patch("clio.asr.aliyun.dashscope_upload", return_value="oss://test/file.wav")


def _mock_submit(provider):
    return patch.object(provider, "_submit_task", return_value="task-123")


def _make_poll_result(status="SUCCEEDED"):
    return {"output": {"task_status": status}}


def _make_transcript_result():
    return {
        "output": {
            "task_status": "SUCCEEDED",
            "results": [{"transcription_url": "https://example.com/transcript.json"}],
        }
    }


def _make_transcript_json():
    return {
        "transcripts": [
            {
                "sentences": [
                    {"begin_time": 500, "end_time": 3200, "text": "大家好"},
                    {"begin_time": 3300, "end_time": 6000, "text": "今天天气不错"},
                ]
            }
        ]
    }


def test_full_transcribe_flow():
    from pathlib import Path

    provider = AliyunASRProvider(MagicMock())
    events = []

    mock_resp_submit = MagicMock()
    mock_resp_submit.json.return_value = {"output": {"task_id": "task-123"}}
    mock_resp_submit.raise_for_status.return_value = None

    mock_resp_poll = MagicMock()
    mock_resp_poll.json.return_value = _make_transcript_result()
    mock_resp_poll.raise_for_status.return_value = None

    mock_resp_dl = MagicMock()
    mock_resp_dl.json.return_value = _make_transcript_json()
    mock_resp_dl.raise_for_status.return_value = None

    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}),
        _mock_upload(),
        patch("httpx.post", return_value=mock_resp_submit),
        patch("httpx.get", side_effect=[mock_resp_poll, mock_resp_dl]),
    ):
        segments = provider.transcribe(Path("fake.wav"), "zh", progress_callback=events.append)

    assert len(segments) == 2
    assert segments[0].start == 0.5
    assert segments[0].end == 3.2
    assert segments[0].text == "大家好"
    assert segments[1].text == "今天天气不错"
    assert events[-1] == 100


def test_task_failed_raises():
    from pathlib import Path

    provider = AliyunASRProvider(MagicMock())
    mock_resp_submit = MagicMock()
    mock_resp_submit.json.return_value = {"output": {"task_id": "task-123"}}
    mock_resp_submit.raise_for_status.return_value = None

    mock_resp_fail = MagicMock()
    mock_resp_fail.json.return_value = {"output": {"task_status": "FAILED", "message": "audio error"}}
    mock_resp_fail.raise_for_status.return_value = None

    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}),
        _mock_upload(),
        patch.object(provider, "_submit_task", return_value="task-123"),
        patch("httpx.get", return_value=mock_resp_fail),
    ):
        with pytest.raises(RuntimeError, match="阿里云 ASR 转录失败"):
            provider.transcribe(Path("fake.wav"), "zh")


def test_subtask_failure_surfaces_provider_message():
    provider = AliyunASRProvider(MagicMock())

    with pytest.raises(RuntimeError, match="音频下载失败"):
        provider._parse_result(
            {
                "output": {
                    "results": [
                        {
                            "subtask_status": "FAILED",
                            "code": "InvalidFile.DownloadFailed",
                            "message": "音频下载失败",
                        }
                    ]
                }
            }
        )


def test_cancel_event_stops_polling():
    from pathlib import Path

    provider = AliyunASRProvider(MagicMock())
    cancel_event = threading.Event()
    cancel_event.set()

    mock_resp_running = MagicMock()
    mock_resp_running.json.return_value = {"output": {"task_status": "RUNNING"}}
    mock_resp_running.raise_for_status.return_value = None

    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}),
        _mock_upload(),
        patch.object(provider, "_submit_task", return_value="task-123"),
        patch("httpx.get", return_value=mock_resp_running),
    ):
        with pytest.raises(RuntimeError, match="已取消"):
            provider.transcribe(Path("fake.wav"), "zh", cancel_event=cancel_event)


def test_submit_retries_429():
    provider = AliyunASRProvider(MagicMock())
    rate_limited = httpx.Response(429, request=httpx.Request("POST", "https://example.com"))
    success = MagicMock()
    success.json.return_value = {"output": {"task_id": "task-123"}}

    with (
        patch("httpx.post", side_effect=[rate_limited, success]) as post,
        patch("clio.asr.retry.time.sleep"),
    ):
        task_id = provider._submit_task("oss://test/file.wav", "zh", "test-key")

    assert task_id == "task-123"
    assert post.call_count == 2


def test_poll_retries_503():
    provider = AliyunASRProvider(MagicMock())
    unavailable = httpx.Response(503, request=httpx.Request("GET", "https://example.com"))
    success = MagicMock()
    success.json.return_value = _make_poll_result()

    with (
        patch("httpx.get", side_effect=[unavailable, success]) as get,
        patch("clio.asr.retry.time.sleep"),
    ):
        result = provider._poll_task("task-123", "test-key", None, None)

    assert result == _make_poll_result()
    assert get.call_count == 2


def test_poll_cancel_interrupts_interval_wait():
    class CancelDuringWait:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            return True

    provider = AliyunASRProvider(MagicMock())
    running = MagicMock()
    running.json.return_value = {"output": {"task_status": "RUNNING"}}

    with patch("httpx.get", return_value=running):
        with pytest.raises(RuntimeError, match="云端 ASR 任务已取消"):
            provider._poll_task("task-123", "test-key", None, CancelDuringWait())


def test_transcribe_passes_cancel_event_to_all_cloud_stages():
    from pathlib import Path

    provider = AliyunASRProvider(MagicMock())
    cancel_event = threading.Event()

    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}),
        patch("clio.asr.aliyun.dashscope_upload", return_value="oss://test/file.wav") as upload,
        patch.object(provider, "_submit_task", return_value="task-123") as submit,
        patch.object(provider, "_poll_task", return_value=_make_poll_result()),
        patch.object(provider, "_parse_result", return_value=[]) as parse,
    ):
        provider.transcribe(Path("fake.wav"), "zh", cancel_event=cancel_event)

    upload.assert_called_once_with(
        Path("fake.wav"),
        progress_callback=None,
        cancel_event=cancel_event,
    )
    submit.assert_called_once_with(
        "oss://test/file.wav",
        "zh",
        "test-key",
        cancel_event=cancel_event,
    )
    parse.assert_called_once_with(_make_poll_result(), cancel_event=cancel_event)

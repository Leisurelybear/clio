from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clio.asr.upload import dashscope_upload


def _policy_response() -> MagicMock:
    response = MagicMock()
    response.json.return_value = {
        "data": {
            "upload_dir": "uploads",
            "upload_host": "https://oss.example.com",
            "oss_access_key_id": "key-id",
            "policy": "policy",
            "signature": "signature",
            "x_oss_object_acl": "private",
            "x_oss_forbid_overwrite": "true",
        }
    }
    return response


def test_upload_retries_policy_and_file_upload(tmp_path: Path, fake_time):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    rate_limited = httpx.Response(429, request=httpx.Request("GET", "https://example.com"))
    unavailable = httpx.Response(503, request=httpx.Request("POST", "https://example.com"))

    fake_time("clio.asr.retry")
    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}),
        patch("httpx.get", side_effect=[rate_limited, _policy_response()]) as get,
        patch("httpx.post", side_effect=[unavailable, MagicMock()]) as post,
    ):
        url = dashscope_upload(audio)

    assert url == "oss://uploads/audio.wav"
    assert get.call_count == 2
    assert post.call_count == 2


def test_upload_policy_403_is_not_retried(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    forbidden = httpx.Response(403, request=httpx.Request("GET", "https://example.com"))

    with (
        patch.dict("os.environ", {"DASHSCOPE_API_KEY": "test-key"}),
        patch("httpx.get", return_value=forbidden) as get,
        patch("httpx.post") as post,
    ):
        with pytest.raises(RuntimeError, match=r"获取音频上传凭证失败（HTTP 403"):
            dashscope_upload(audio)

    assert get.call_count == 1
    post.assert_not_called()


def test_upload_cancel_short_circuits_before_api_key_check(tmp_path: Path):
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    cancel_event = threading.Event()
    cancel_event.set()

    with patch("httpx.get") as get:
        with pytest.raises(RuntimeError, match="音频上传已取消"):
            dashscope_upload(audio, cancel_event=cancel_event)

    get.assert_not_called()

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clio.config import ProviderConfig, ProxyConfig


def _make_cfg(**kwargs) -> ProviderConfig:
    defaults: dict[str, Any] = dict(
        name="gemini",
        type="gemini",
        api_key="test-key",
        api_key_env="GEMINI_API_KEY",
        base_url="",
        requests_per_minute=0,
        poll_interval_sec=5,
        retry_attempts=2,
        max_tokens=0,
    )
    defaults.update(kwargs)
    return ProviderConfig(**defaults)


@pytest.fixture
def proxy_disabled():
    return ProxyConfig(enabled=False, url="")


@pytest.fixture
def proxy_enabled():
    return ProxyConfig(enabled=True, url="http://127.0.0.1:1080")


class TestInit:
    def test_missing_api_key_raises(self):
        cfg = _make_cfg(api_key="")
        with pytest.raises(ValueError, match="缺少 API Key"):
            from clio.ai.gemini import GeminiProvider

            GeminiProvider(cfg, ProxyConfig(enabled=False, url=""))

    def test_creates_client_without_proxy(self, proxy_disabled):
        with patch("clio.ai.gemini.genai.Client") as mock_client_cls:
            from clio.ai.gemini import GeminiProvider

            p = GeminiProvider(_make_cfg(), proxy_disabled)
            http_options = mock_client_cls.call_args.kwargs["http_options"]
            assert http_options.timeout == 120_000
            assert p._poll_interval == 5
            assert p._retry_attempts == 3  # retry_attempts+1

    def test_creates_client_with_proxy(self, proxy_enabled):
        with (
            patch("clio.ai.gemini.genai.Client") as mock_client_cls,
            patch("clio.ai.gemini.types.HttpOptions") as mock_http_opts,
        ):
            from clio.ai.gemini import GeminiProvider

            GeminiProvider(_make_cfg(), proxy_enabled)
            mock_http_opts.assert_called_once()
            mock_client_cls.assert_called_once()

    def test_rate_limiter_disabled(self, proxy_disabled):
        with patch("clio.ai.gemini.genai.Client"):
            from clio.ai.gemini import GeminiProvider

            p = GeminiProvider(_make_cfg(requests_per_minute=0), proxy_disabled)
            assert p._rl is None

    def test_rate_limiter_enabled(self, proxy_disabled):
        with patch("clio.ai.gemini.genai.Client"):
            from clio.ai.gemini import GeminiProvider

            p = GeminiProvider(_make_cfg(requests_per_minute=10), proxy_disabled)
            assert p._rl is not None


class TestIsRetryable:
    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("clio.ai.gemini.genai.Client"):
            from clio.ai.gemini import GeminiProvider

            self._prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            yield

    def test_oserror_is_retryable(self):
        assert self._prov._is_retryable(OSError("connection reset"))

    @pytest.mark.parametrize(
        "error",
        [
            httpx.ConnectError("connection refused"),
            httpx.ReadTimeout("read timed out"),
        ],
    )
    def test_httpx_transport_error_is_retryable(self, error):
        assert self._prov._is_retryable(error)

    def test_client_error_429_is_retryable(self):
        from google.genai import errors as genai_errors

        e = genai_errors.ClientError(code=429, response_json={})
        assert self._prov._is_retryable(e)

    def test_client_error_400_not_retryable(self):
        from google.genai import errors as genai_errors

        e = genai_errors.ClientError(code=400, response_json={})
        assert not self._prov._is_retryable(e)

    def test_client_error_resource_exhausted_retryable(self):
        from google.genai import errors as genai_errors

        e = genai_errors.ClientError(code=200, response_json={})
        e.status = "RESOURCE_EXHAUSTED"
        assert self._prov._is_retryable(e)

    def test_server_error_is_retryable(self):
        from google.genai import errors as genai_errors

        e = genai_errors.ServerError(code=500, response_json={})
        assert self._prov._is_retryable(e)

    def test_arbitrary_exception_not_retryable(self):
        assert not self._prov._is_retryable(ValueError("bad input"))

    def test_no_genai_errors_fallback(self):
        with (
            patch("clio.ai.gemini.genai.Client"),
            patch("clio.ai.gemini._HAS_GENAI_ERRORS", False),
        ):
            from clio.ai.gemini import GeminiProvider

            prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            assert prov._is_retryable(OSError("reset"))
            assert not prov._is_retryable(ValueError("nope"))


class TestCallWithRetry:
    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("clio.ai.gemini.genai.Client"):
            from clio.ai.gemini import GeminiProvider

            self._prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            yield

    def test_success(self):
        fn = MagicMock(return_value="ok")
        result = self._prov._call_with_retry(fn, "test", "model")
        assert result == "ok"
        fn.assert_called_once()

    def test_non_retryable_wraps_as_runtime(self):
        fn = MagicMock(side_effect=ValueError("bad"))
        with pytest.raises(RuntimeError, match="不可重试错误"):
            self._prov._call_with_retry(fn, "test", "model")

    def test_retryable_passes_through_to_with_retry(self):
        fn = MagicMock(side_effect=OSError("reset"))
        with pytest.raises(OSError, match="reset"):
            self._prov._call_with_retry(fn, "test", "model")
        assert fn.call_count >= 1

    def test_cancel_event_skips_fn(self):
        cancel = threading.Event()
        cancel.set()
        fn = MagicMock(return_value="ok")

        with pytest.raises(InterruptedError):
            self._prov._call_with_retry(fn, "test", "model", cancel)
        fn.assert_not_called()


class TestWaitForFile:
    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            from clio.ai.gemini import GeminiProvider

            self._client_mock = MagicMock()
            mock_cls.return_value = self._client_mock
            self._prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            yield

    def test_already_active(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.state = types.FileState.ACTIVE
        result = self._prov._wait_for_file(uploaded, timeout=10)
        assert result is uploaded

    def test_processing_to_active(self, fake_time):
        from google.genai import types

        processed = MagicMock(spec=types.File)
        processed.name = "files/test123"
        processed.state = types.FileState.PROCESSING
        active = MagicMock(spec=types.File)
        active.state = types.FileState.ACTIVE

        self._client_mock.files.get.return_value = active
        fake_time("clio.ai.gemini", 0.0)
        result = self._prov._wait_for_file(processed, timeout=300)
        assert result.state == types.FileState.ACTIVE

    def test_processing_to_failed(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        failed = MagicMock()
        failed.name = "files/test123"
        failed.state = types.FileState.FAILED

        self._client_mock.files.get.return_value = failed
        fake_time("clio.ai.gemini", 0.0)
        with pytest.raises(RuntimeError, match="视频处理失败"):
            self._prov._wait_for_file(uploaded, timeout=300)

    def test_timeout(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING

        fake_time("clio.ai.gemini", 0.0, 301.0, 302.0)
        with pytest.raises(TimeoutError, match="视频处理超时"):
            self._prov._wait_for_file(uploaded, timeout=300)

    def test_on_progress_called(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        active = MagicMock(spec=types.File)
        active.state = types.FileState.ACTIVE

        self._client_mock.files.get.return_value = active
        callback = MagicMock()
        fake_time("clio.ai.gemini", 0.0)
        self._prov._wait_for_file(uploaded, timeout=300, on_progress=callback)
        callback.assert_called()

    def test_transient_get_failure_retries_then_succeeds(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        active = MagicMock(spec=types.File)
        active.name = "files/test123"
        active.state = types.FileState.ACTIVE

        self._client_mock.files.get.side_effect = [OSError("temporary reset"), active]
        sleep = fake_time("clio.ai.gemini", 0.0).sleep
        result = self._prov._wait_for_file(uploaded, timeout=300)

        assert result is active
        assert self._client_mock.files.get.call_count == 2
        sleep.assert_any_call(5)
        sleep.assert_any_call(2.0)

    def test_non_retryable_get_failure_propagates_immediately(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        self._client_mock.files.get.side_effect = ValueError("permanent failure")

        fake_time("clio.ai.gemini", 0.0)
        with pytest.raises(ValueError, match="permanent failure"):
            self._prov._wait_for_file(uploaded, timeout=300)

        assert self._client_mock.files.get.call_count == 1

    def test_timeout_is_not_extended_by_retries(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        self._client_mock.files.get.side_effect = OSError("temporary reset")

        sleep = fake_time("clio.ai.gemini", 0.0, 0.0, 0.0, 0.0, 2.0).sleep
        with pytest.raises(TimeoutError, match="视频处理超时"):
            self._prov._wait_for_file(uploaded, timeout=1)

        assert self._client_mock.files.get.call_count == 1
        sleep.assert_called_once_with(5)

    def test_cancelled_before_poll_does_not_call_get(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        cancel_event = threading.Event()
        cancel_event.set()

        fake_time("clio.ai.gemini", 0.0)
        with pytest.raises(RuntimeError, match="视频处理被用户取消"):
            self._prov._wait_for_file(uploaded, timeout=300, cancel_event=cancel_event)

        self._client_mock.files.get.assert_not_called()

    def test_cancelled_during_poll_wait_does_not_call_get(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        cancel_event = MagicMock()
        cancel_event.is_set.return_value = False
        cancel_event.wait.return_value = True

        with pytest.raises(RuntimeError, match="视频处理被用户取消"):
            self._prov._wait_for_file(uploaded, timeout=300, cancel_event=cancel_event)

        self._client_mock.files.get.assert_not_called()

    def test_cancelled_during_retry_backoff(self, fake_time):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        self._client_mock.files.get.side_effect = OSError("temporary reset")
        cancel_event = MagicMock()
        cancel_event.is_set.return_value = False
        cancel_event.wait.side_effect = [False, True]

        fake_time("clio.ai.gemini", 0.0)
        with pytest.raises(RuntimeError, match="视频处理被用户取消"):
            self._prov._wait_for_file(uploaded, timeout=300, cancel_event=cancel_event)

        assert self._client_mock.files.get.call_count == 1


class TestGenerateText:
    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            from clio.ai.gemini import GeminiProvider

            self._client_mock = MagicMock()
            mock_cls.return_value = self._client_mock
            self._prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            yield

    def test_success(self):
        response_mock = MagicMock()
        response_mock.text = "hello world"
        self._client_mock.models.generate_content.return_value = response_mock

        result = self._prov.generate_text("hi", "gemini-2.5-flash")
        assert result.text == "hello world"
        self._client_mock.models.generate_content.assert_called_once_with(model="gemini-2.5-flash", contents="hi")

    def test_sends_system_instruction_when_provided(self):
        self._client_mock.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)

        self._prov.generate_text("hi", "gemini-2.5-flash", system="follow the rules")

        config = self._client_mock.models.generate_content.call_args.kwargs["config"]
        assert config.system_instruction == "follow the rules"

    def test_safety_block_reason_returns_normalized_response(self):
        self._client_mock.models.generate_content.return_value = SimpleNamespace(
            text="",
            usage_metadata=None,
            candidates=[],
            prompt_feedback=SimpleNamespace(block_reason="SAFETY"),
        )

        result = self._prov.generate_text("blocked prompt", "gemini-2.5-flash")

        assert result.text == ""
        assert result.block_reason == "SAFETY"
        assert result.blocked is True

    @pytest.mark.parametrize("finish_reason", ["SAFETY", "RECITATION"])
    def test_blocking_candidate_finish_reason_is_not_reported_as_empty_json(self, finish_reason):
        self._client_mock.models.generate_content.return_value = SimpleNamespace(
            text="",
            usage_metadata=None,
            candidates=[SimpleNamespace(finish_reason=finish_reason)],
            prompt_feedback=None,
        )

        result = self._prov.generate_text("blocked prompt", "gemini-2.5-flash")

        assert result.text == ""
        assert result.finish_reason == finish_reason
        assert result.block_reason == finish_reason
        assert result.blocked is True

    def test_unspecified_block_reason_does_not_raise(self):
        self._client_mock.models.generate_content.return_value = SimpleNamespace(
            text="ok",
            usage_metadata=None,
            candidates=[],
            prompt_feedback=SimpleNamespace(block_reason="BLOCK_REASON_UNSPECIFIED"),
        )

        result = self._prov.generate_text("hello", "gemini-2.5-flash")

        assert result.text == "ok"

    def test_returns_finish_reason(self):
        from google.genai import types

        response = SimpleNamespace(
            text='{"partial":',
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)],
            usage_metadata=None,
        )
        self._client_mock.models.generate_content.return_value = response

        result = self._prov.generate_text("hi", "gemini-2.5-flash")

        assert result.finish_reason == "MAX_TOKENS"

    def test_usage_includes_thinking_tokens(self):
        response = SimpleNamespace(
            text="ok",
            candidates=[SimpleNamespace(finish_reason="STOP")],
            usage_metadata=SimpleNamespace(
                prompt_token_count=4,
                candidates_token_count=10,
                thoughts_token_count=7,
                total_token_count=21,
            ),
        )
        self._client_mock.models.generate_content.return_value = response

        result = self._prov.generate_text("hi", "gemini-2.5-flash")

        assert result.token_usage is not None
        assert result.token_usage.prompt_tokens == 4
        assert result.token_usage.completion_tokens == 17
        assert result.token_usage.total_tokens == 21

    def test_empty_response(self):
        response_mock = MagicMock()
        response_mock.text = ""
        self._client_mock.models.generate_content.return_value = response_mock

        result = self._prov.generate_text("hi", "gemini-2.5-flash")
        assert result.text == ""

    def test_rate_limit_cancel_propagates(self):
        import time

        from clio.ratelimit import RateLimiter

        self._prov._rl = RateLimiter(60)
        self._prov._rl._next_at = time.monotonic() + 30
        cancel = MagicMock(spec=threading.Event)
        cancel.is_set.return_value = False
        cancel.wait.return_value = True

        with pytest.raises(InterruptedError, match="gemini"):
            self._prov.generate_text("hi", "gemini-2.5-flash", cancel_event=cancel)

        self._client_mock.models.generate_content.assert_not_called()

    def test_forwards_cancel_event_to_retry(self):
        cancel = threading.Event()

        with patch.object(self._prov, "_call_with_retry", return_value="ok") as retry_mock:
            result = self._prov.generate_text("hi", "gemini-2.5-flash", cancel_event=cancel)

        assert result == "ok"
        assert retry_mock.call_args.args[3] is cancel

    def test_analyze_images_sends_png_parts_then_prompt(self, tmp_path):
        from clio.ai.gemini import GeminiProvider

        first = tmp_path / "first.png"
        second = tmp_path / "second.png"
        first.write_bytes(b"first-image")
        second.write_bytes(b"second-image")
        self._client_mock.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)
        provider = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))

        with patch(
            "clio.ai.gemini.types.Part.from_bytes",
            side_effect=lambda data, mime_type: SimpleNamespace(data=data, mime_type=mime_type),
        ):
            result = provider.analyze_images(
                [str(first), str(second)],
                "review transitions",
                "gemini-2.5-flash",
            )

        contents = self._client_mock.models.generate_content.call_args.kwargs["contents"]
        assert result.text == "ok"
        assert [(part.data, part.mime_type) for part in contents[:-1]] == [
            (b"first-image", "image/png"),
            (b"second-image", "image/png"),
        ]
        assert contents[-1] == "review transitions"

    def test_analyze_images_forwards_cancel_and_structured_config(self, tmp_path):
        from clio.ai.base import StructuredOutput
        from clio.ai.gemini import GeminiProvider

        image = tmp_path / "review.png"
        image.write_bytes(b"image")
        cancel = threading.Event()
        schema = {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}
        self._client_mock.models.generate_content.return_value = MagicMock(text="{}", usage_metadata=None)
        provider = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))

        with patch.object(provider, "_call_with_retry", side_effect=lambda fn, *args, **kwargs: fn()) as retry_mock:
            provider.analyze_images(
                [str(image)],
                "review",
                "gemini-2.5-flash",
                cancel_event=cancel,
                temperature=0.2,
                structured_output=StructuredOutput("transition_review", schema),
            )

        assert retry_mock.call_args.args[3] is cancel
        config = self._client_mock.models.generate_content.call_args.kwargs["config"]
        assert config.temperature == 0.2
        assert config.response_mime_type == "application/json"
        assert config.response_json_schema == schema

    def test_positive_max_tokens_is_sent(self, proxy_disabled):
        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            from clio.ai.gemini import GeminiProvider

            client = MagicMock()
            mock_cls.return_value = client
            client.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)
            provider = GeminiProvider(_make_cfg(max_tokens=2048), proxy_disabled)

            provider.generate_text("hi", "gemini-2.5-flash")

            config = client.models.generate_content.call_args.kwargs["config"]
            assert config.max_output_tokens == 2048

    def test_temperature_is_sent_in_config(self, proxy_disabled):
        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            from clio.ai.gemini import GeminiProvider

            client = MagicMock()
            mock_cls.return_value = client
            client.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)
            provider = GeminiProvider(_make_cfg(), proxy_disabled)

            provider.generate_text("hi", "gemini-2.5-flash", temperature=0.7)

            config = client.models.generate_content.call_args.kwargs["config"]
            assert config.temperature == 0.7
            assert config.max_output_tokens is None

    def test_temperature_and_max_tokens_coexist(self, proxy_disabled):
        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            from clio.ai.gemini import GeminiProvider

            client = MagicMock()
            mock_cls.return_value = client
            client.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)
            provider = GeminiProvider(_make_cfg(max_tokens=2048), proxy_disabled)

            provider.generate_text("hi", "gemini-2.5-flash", temperature=0.2)

            config = client.models.generate_content.call_args.kwargs["config"]
            assert config.temperature == 0.2
            assert config.max_output_tokens == 2048

    def test_sends_json_schema_in_config(self, proxy_disabled):
        from clio.ai.base import StructuredOutput
        from clio.ai.gemini import GeminiProvider

        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client
            client.models.generate_content.return_value = MagicMock(text='{"answer":"ok"}', usage_metadata=None)
            provider = GeminiProvider(_make_cfg(), proxy_disabled)
            schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}

            provider.generate_text(
                "hi",
                "gemini-2.5-flash",
                structured_output=StructuredOutput("answer", schema),
            )

            config = client.models.generate_content.call_args.kwargs["config"]
            assert config.response_mime_type == "application/json"
            assert config.response_json_schema == schema

    def test_json_mode_without_schema_uses_mime_type_only(self, proxy_disabled):
        from clio.ai.base import StructuredOutput
        from clio.ai.gemini import GeminiProvider

        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            client = MagicMock()
            mock_cls.return_value = client
            client.models.generate_content.return_value = MagicMock(text="{}", usage_metadata=None)
            provider = GeminiProvider(_make_cfg(), proxy_disabled)

            provider.generate_text(
                "hi",
                "gemini-2.5-flash",
                structured_output=StructuredOutput("json_object"),
            )

            config = client.models.generate_content.call_args.kwargs["config"]
            assert config.response_mime_type == "application/json"
            assert config.response_json_schema is None

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("low", "MEDIA_RESOLUTION_LOW"),
            ("medium", "MEDIA_RESOLUTION_MEDIUM"),
            ("high", "MEDIA_RESOLUTION_HIGH"),
        ],
    )
    def test_media_resolution_mapping(self, value, expected):
        from google.genai import types

        config = self._prov._config_for(None, media_resolution=value)

        assert config.media_resolution == getattr(types.MediaResolution, expected)

    def test_media_resolution_rejects_unknown_value(self):
        with pytest.raises(ValueError, match="ultra"):
            self._prov._config_for(None, media_resolution="ultra")

    def test_text_request_does_not_include_media_resolution(self):
        self._client_mock.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)

        self._prov.generate_text("hi", "gemini-2.5-flash")

        assert "config" not in self._client_mock.models.generate_content.call_args.kwargs


class TestAnalyzeVideo:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        with patch("clio.ai.gemini.genai.Client") as mock_cls:
            from clio.ai.gemini import GeminiProvider

            self._client_mock = MagicMock()
            mock_cls.return_value = self._client_mock
            self._prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))

            self._video = tmp_path / "test.mp4"
            self._video.write_bytes(b"\x00" * 1024)
            yield

    def test_close_ignores_sdk_failure(self):
        self._client_mock.close.side_effect = RuntimeError("close failed")

        self._prov.close()

    def test_success(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded

        response_mock = MagicMock()
        response_mock.text = "analysis result"
        response_mock.candidates = [SimpleNamespace(finish_reason="STOP")]
        self._client_mock.models.generate_content.return_value = response_mock

        result = self._prov.analyze_video(str(self._video), "describe this", "gemini-2.5-flash")
        assert result.text == "analysis result"
        assert result.finish_reason == "STOP"
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_truncated_video_output_retries_without_reupload_and_increases_budget(self):
        from google.genai import types

        from clio.ai.gemini import GeminiProvider

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        first = SimpleNamespace(
            text='{"partial":',
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.MAX_TOKENS)],
            usage_metadata=SimpleNamespace(
                prompt_token_count=10,
                candidates_token_count=20,
                total_token_count=30,
            ),
        )
        second = SimpleNamespace(
            text='{"ok":true}',
            candidates=[SimpleNamespace(finish_reason=types.FinishReason.STOP)],
            usage_metadata=SimpleNamespace(
                prompt_token_count=12,
                candidates_token_count=8,
                total_token_count=20,
            ),
        )
        self._client_mock.models.generate_content.side_effect = [first, second]
        provider = GeminiProvider(_make_cfg(max_tokens=2048), ProxyConfig(enabled=False, url=""))

        result = provider.analyze_video(str(self._video), "describe this", "gemini-2.5-flash")

        assert result.text == '{"ok":true}'
        assert result.token_usage is not None
        assert result.token_usage.total_tokens == 50
        assert self._client_mock.files.upload.call_count == 1
        assert self._client_mock.models.generate_content.call_count == 2
        first_call, second_call = self._client_mock.models.generate_content.call_args_list
        assert first_call.kwargs["config"].max_output_tokens == 2048
        assert second_call.kwargs["config"].max_output_tokens == 4096
        assert "上一次输出因长度限制被截断" in second_call.kwargs["contents"][-1]
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_reused_upload_is_shared_across_window_calls_and_released(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.uri = "https://example.test/files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)

        self._prov.analyze_video(
            str(self._video),
            "window one",
            "gemini-2.5-flash",
            video_start_sec=0,
            video_end_sec=10,
            video_fps=3,
            reuse_upload=True,
        )
        self._prov.analyze_video(
            str(self._video),
            "window two",
            "gemini-2.5-flash",
            video_start_sec=10,
            video_end_sec=20,
            video_fps=3,
            reuse_upload=True,
        )

        self._client_mock.files.upload.assert_called_once()
        self._client_mock.files.delete.assert_not_called()
        self._prov.release_video_upload(str(self._video))
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_reused_upload_is_invalidated_when_file_changes(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.uri = "https://example.test/files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)

        self._prov.analyze_video(
            str(self._video),
            "first",
            "gemini-2.5-flash",
            video_start_sec=0,
            video_end_sec=10,
            video_fps=3,
            reuse_upload=True,
        )
        self._video.write_bytes(b"" * 2048)
        self._prov.analyze_video(
            str(self._video),
            "second",
            "gemini-2.5-flash",
            video_start_sec=0,
            video_end_sec=10,
            video_fps=3,
            reuse_upload=True,
        )

        assert self._client_mock.files.upload.call_count == 2
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_video_metadata_and_media_resolution_are_sent(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.uri = "https://example.test/files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)

        self._prov.analyze_video(
            str(self._video),
            "describe",
            "gemini-2.5-flash",
            video_start_sec=12.5,
            video_end_sec=30.0,
            video_fps=3,
            media_resolution="high",
        )

        call = self._client_mock.models.generate_content.call_args
        part = call.kwargs["contents"][0]
        assert isinstance(part, types.Part)
        assert part.file_data is not None
        assert part.video_metadata is not None
        assert part.file_data.file_uri == uploaded.uri
        assert part.file_data.mime_type == "video/mp4"
        assert part.video_metadata.start_offset == "12.5s"
        assert part.video_metadata.end_offset == "30.0s"
        assert part.video_metadata.fps == 3
        assert call.kwargs["config"].media_resolution == types.MediaResolution.MEDIA_RESOLUTION_HIGH

    def test_temperature_is_sent_in_video_config(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = MagicMock(text="ok", usage_metadata=None)

        self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash", temperature=0.4)

        config = self._client_mock.models.generate_content.call_args.kwargs["config"]
        assert config.temperature == 0.4

    def test_structured_output_is_sent_in_video_config(self):
        from google.genai import types

        from clio.ai.base import StructuredOutput

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = MagicMock(text="{}", usage_metadata=None)
        schema = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}

        self._prov.analyze_video(
            str(self._video),
            "desc",
            "gemini-2.5-flash",
            structured_output=StructuredOutput("analysis", schema),
        )

        config = self._client_mock.models.generate_content.call_args.kwargs["config"]
        assert config.response_mime_type == "application/json"
        assert config.response_json_schema == schema

    def test_file_too_large_reports_configured_limit(self, tmp_path):
        from clio.ai.gemini import GeminiProvider

        prov = GeminiProvider(_make_cfg(video_upload_max_mb=1), ProxyConfig(enabled=False, url=""))
        big = tmp_path / "big.mp4"
        big.write_bytes(b"\x00" * (2 * 1024 * 1024))
        with pytest.raises(ValueError, match="文件过大") as exc_info:
            prov.analyze_video(str(big), "desc", "gemini-2.5-flash")
        assert "1 MB" in str(exc_info.value)

    def test_custom_upload_limit_is_enforced(self, tmp_path):
        from clio.ai.gemini import GeminiProvider

        prov = GeminiProvider(_make_cfg(video_upload_max_mb=1), ProxyConfig(enabled=False, url=""))
        just_over = tmp_path / "over.mp4"
        just_over.write_bytes(b"\x00" * (1024 * 1024 + 1))
        with pytest.raises(ValueError, match="文件过大"):
            prov.analyze_video(str(just_over), "desc", "gemini-2.5-flash")

    def test_upload_retries_transient_failure_with_fresh_stream(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.side_effect = [OSError("connection reset"), uploaded]
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = SimpleNamespace(
            text="ok",
            usage_metadata=None,
            candidates=[],
        )

        with patch("clio.utils.time.sleep"):
            result = self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash")

        assert result.text == "ok"
        assert self._client_mock.files.upload.call_count == 2
        first_stream = self._client_mock.files.upload.call_args_list[0].kwargs["file"]
        second_stream = self._client_mock.files.upload.call_args_list[1].kwargs["file"]
        assert first_stream is not second_stream
        assert first_stream.closed
        assert second_stream.closed
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_upload_does_not_retry_non_retryable_failure(self):
        self._client_mock.files.upload.side_effect = ValueError("bad upload")

        with pytest.raises(ValueError, match="bad upload"):
            self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash")

        self._client_mock.files.upload.assert_called_once()

    def test_remote_cleanup_failure_does_not_mask_result(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = MagicMock(text="result", usage_metadata=None)
        self._client_mock.files.delete.side_effect = RuntimeError("delete failed")

        result = self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash")

        assert result.text == "result"

    def test_cleanup_on_failure(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded

        self._client_mock.models.generate_content.side_effect = RuntimeError("AI failed")

        with pytest.raises(RuntimeError, match="AI failed"):
            self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash")
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_blocked_response_is_normalized_and_cleans_up(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded
        self._client_mock.models.generate_content.return_value = SimpleNamespace(
            text="",
            usage_metadata=None,
            candidates=[],
            prompt_feedback=SimpleNamespace(block_reason="SAFETY"),
        )

        result = self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash")

        assert result.text == ""
        assert result.block_reason == "SAFETY"
        assert result.blocked is True
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_upload_uses_stream(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded

        response_mock = MagicMock()
        response_mock.text = "ok"
        self._client_mock.models.generate_content.return_value = response_mock

        self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash")

        call = self._client_mock.files.upload.call_args
        assert call is not None
        assert "file" in call.kwargs
        assert "config" in call.kwargs
        assert isinstance(call.kwargs["config"], types.UploadFileConfig)

    def test_progress_callback(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded

        response_mock = MagicMock()
        response_mock.text = "ok"
        self._client_mock.models.generate_content.return_value = response_mock

        callback = MagicMock()
        self._prov.analyze_video(str(self._video), "desc", "gemini-2.5-flash", progress_callback=callback)
        assert callback.call_count >= 1


class TestRetryableTypes:
    @pytest.fixture(autouse=True)
    def _setup(self):
        with patch("clio.ai.gemini.genai.Client"):
            from clio.ai.gemini import GeminiProvider

            self._prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            yield

    def test_includes_oserror_and_httpx_transport_error(self):
        types = self._prov._retryable_types()
        assert OSError in types
        assert httpx.TransportError in types

    def test_includes_client_and_server_errors(self):
        from google.genai import errors as genai_errors

        types = self._prov._retryable_types()
        assert genai_errors.ClientError in types
        assert genai_errors.ServerError in types

    def test_no_genai_errors_fallback(self):
        with (
            patch("clio.ai.gemini.genai.Client"),
            patch("clio.ai.gemini._HAS_GENAI_ERRORS", False),
        ):
            from clio.ai.gemini import GeminiProvider

            prov = GeminiProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            types = prov._retryable_types()
            assert types == (OSError, httpx.TransportError)

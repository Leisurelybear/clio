from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from clio.config import ProviderConfig, ProxyConfig


def _make_cfg(**kwargs) -> ProviderConfig:
    defaults = dict(
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

    def test_processing_to_active(self):
        from google.genai import types

        processed = MagicMock(spec=types.File)
        processed.name = "files/test123"
        processed.state = types.FileState.PROCESSING
        active = MagicMock(spec=types.File)
        active.state = types.FileState.ACTIVE

        self._client_mock.files.get.return_value = active
        with patch("clio.ai.gemini.time.sleep"):
            with patch("clio.ai.gemini.time.monotonic", return_value=0):
                result = self._prov._wait_for_file(processed, timeout=300)
        assert result.state == types.FileState.ACTIVE

    def test_processing_to_failed(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        failed = MagicMock()
        failed.name = "files/test123"
        failed.state = types.FileState.FAILED

        self._client_mock.files.get.return_value = failed
        with patch("clio.ai.gemini.time.sleep"):
            with patch("clio.ai.gemini.time.monotonic", return_value=0):
                with pytest.raises(RuntimeError, match="视频处理失败"):
                    self._prov._wait_for_file(uploaded, timeout=300)

    def test_timeout(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING

        with patch("clio.ai.gemini.time.sleep"):
            with patch("clio.ai.gemini.time.monotonic", side_effect=[0, 301, 302]):
                with pytest.raises(TimeoutError, match="视频处理超时"):
                    self._prov._wait_for_file(uploaded, timeout=300)

    def test_on_progress_called(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        active = MagicMock(spec=types.File)
        active.state = types.FileState.ACTIVE

        self._client_mock.files.get.return_value = active
        callback = MagicMock()
        with patch("clio.ai.gemini.time.sleep"):
            with patch("clio.ai.gemini.time.monotonic", return_value=0):
                self._prov._wait_for_file(uploaded, timeout=300, on_progress=callback)
        callback.assert_called()

    def test_transient_get_failure_retries_then_succeeds(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        active = MagicMock(spec=types.File)
        active.name = "files/test123"
        active.state = types.FileState.ACTIVE

        self._client_mock.files.get.side_effect = [OSError("temporary reset"), active]
        with (
            patch("clio.ai.gemini.time.sleep") as sleep,
            patch("clio.ai.gemini.time.monotonic", return_value=0),
        ):
            result = self._prov._wait_for_file(uploaded, timeout=300)

        assert result is active
        assert self._client_mock.files.get.call_count == 2
        sleep.assert_any_call(5)
        sleep.assert_any_call(2.0)

    def test_non_retryable_get_failure_propagates_immediately(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        self._client_mock.files.get.side_effect = ValueError("permanent failure")

        with (
            patch("clio.ai.gemini.time.sleep"),
            patch("clio.ai.gemini.time.monotonic", return_value=0),
        ):
            with pytest.raises(ValueError, match="permanent failure"):
                self._prov._wait_for_file(uploaded, timeout=300)

        assert self._client_mock.files.get.call_count == 1

    def test_timeout_is_not_extended_by_retries(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        self._client_mock.files.get.side_effect = OSError("temporary reset")

        with (
            patch("clio.ai.gemini.time.sleep") as sleep,
            patch("clio.ai.gemini.time.monotonic", side_effect=[0, 0, 0, 0, 2]),
        ):
            with pytest.raises(TimeoutError, match="视频处理超时"):
                self._prov._wait_for_file(uploaded, timeout=1)

        assert self._client_mock.files.get.call_count == 1
        sleep.assert_called_once_with(5)

    def test_cancelled_before_poll_does_not_call_get(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        cancel_event = threading.Event()
        cancel_event.set()

        with patch("clio.ai.gemini.time.monotonic", return_value=0):
            with pytest.raises(RuntimeError, match="视频处理被用户取消"):
                self._prov._wait_for_file(uploaded, timeout=300, cancel_event=cancel_event)

        self._client_mock.files.get.assert_not_called()

    def test_cancelled_during_retry_backoff(self):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.PROCESSING
        self._client_mock.files.get.side_effect = OSError("temporary reset")
        cancel_event = MagicMock()
        cancel_event.is_set.return_value = False
        cancel_event.wait.return_value = True

        with (
            patch("clio.ai.gemini.time.sleep"),
            patch("clio.ai.gemini.time.monotonic", return_value=0),
        ):
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

    def test_empty_response(self):
        response_mock = MagicMock()
        response_mock.text = ""
        self._client_mock.models.generate_content.return_value = response_mock

        result = self._prov.generate_text("hi", "gemini-2.5-flash")
        assert result.text == ""

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

    def test_success(self, tmp_path):
        from google.genai import types

        uploaded = MagicMock(spec=types.File)
        uploaded.name = "files/test123"
        uploaded.state = types.FileState.ACTIVE
        self._client_mock.files.upload.return_value = uploaded
        self._client_mock.files.get.return_value = uploaded

        response_mock = MagicMock()
        response_mock.text = "analysis result"
        self._client_mock.models.generate_content.return_value = response_mock

        result = self._prov.analyze_video(str(self._video), "describe this", "gemini-2.5-flash")
        assert result.text == "analysis result"
        self._client_mock.files.delete.assert_called_once_with(name="files/test123")

    def test_file_too_large(self, tmp_path):
        big = tmp_path / "big.mp4"
        big.write_bytes(b"\x00" * (201 * 1024 * 1024))
        with pytest.raises(ValueError, match="文件过大"):
            self._prov.analyze_video(str(big), "desc", "gemini-2.5-flash")

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

    def test_includes_oserror(self):
        types = self._prov._retryable_types()
        assert OSError in types

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
            assert types == (OSError,)

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clio.config import ProviderConfig, ProxyConfig


def _make_cfg(**kwargs) -> ProviderConfig:
    defaults = dict(
        name="deepseek",
        type="openai",
        api_key="sk-test-key",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
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
    return ProxyConfig(enabled=True, url="http://proxy:8080")


class TestInit:
    def test_missing_api_key_raises(self):
        cfg = _make_cfg(api_key="")
        with pytest.raises(ValueError, match="缺少 API Key"):
            from clio.ai.openai_compat import OpenAICompatProvider

            OpenAICompatProvider(cfg, ProxyConfig(enabled=False, url=""))

    def test_default_base_url(self, proxy_disabled):
        cfg = _make_cfg(base_url="")
        with patch("clio.ai.openai_compat.httpx.Client"):
            from clio.ai.openai_compat import OpenAICompatProvider

            p = OpenAICompatProvider(cfg, proxy_disabled)
            assert "api.openai.com/v1" in p._base_url

    def test_uses_custom_base_url(self, proxy_disabled):
        with patch("clio.ai.openai_compat.httpx.Client"):
            from clio.ai.openai_compat import OpenAICompatProvider

            p = OpenAICompatProvider(_make_cfg(), proxy_disabled)
            assert p._base_url == "https://api.deepseek.com"

    def test_strips_trailing_slash(self, proxy_disabled):
        with patch("clio.ai.openai_compat.httpx.Client"):
            from clio.ai.openai_compat import OpenAICompatProvider

            p = OpenAICompatProvider(_make_cfg(base_url="https://api.deepseek.com/"), proxy_disabled)
            assert p._base_url == "https://api.deepseek.com"

    def test_creates_client_without_proxy(self, proxy_disabled):
        with patch("clio.ai.openai_compat.httpx.Client") as mock_cls:
            from clio.ai.openai_compat import OpenAICompatProvider

            OpenAICompatProvider(_make_cfg(), proxy_disabled)
            mock_cls.assert_called_once_with(timeout=120.0)

    def test_creates_client_with_proxy(self, proxy_enabled):
        with patch("clio.ai.openai_compat.httpx.Client") as mock_cls:
            from clio.ai.openai_compat import OpenAICompatProvider

            OpenAICompatProvider(_make_cfg(), proxy_enabled)
            mock_cls.assert_called_once_with(timeout=120.0, proxy="http://proxy:8080")

    def test_rate_limiter_disabled(self, proxy_disabled):
        with patch("clio.ai.openai_compat.httpx.Client"):
            from clio.ai.openai_compat import OpenAICompatProvider

            p = OpenAICompatProvider(_make_cfg(requests_per_minute=0), proxy_disabled)
            assert p._rl is None

    def test_rate_limiter_enabled(self, proxy_disabled):
        with patch("clio.ai.openai_compat.httpx.Client"):
            from clio.ai.openai_compat import OpenAICompatProvider

            p = OpenAICompatProvider(_make_cfg(requests_per_minute=10), proxy_disabled)
            assert p._rl is not None


class TestClose:
    def test_closes_http_client(self):
        mock_client = MagicMock()
        with patch("clio.ai.openai_compat.httpx.Client", return_value=mock_client):
            from clio.ai.openai_compat import OpenAICompatProvider

            p = OpenAICompatProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            p.close()
            mock_client.close.assert_called_once()


class TestGenerateText:
    @pytest.fixture(autouse=True)
    def _setup(self):
        self._mock_client = MagicMock()
        with patch("clio.ai.openai_compat.httpx.Client", return_value=self._mock_client):
            from clio.ai.openai_compat import OpenAICompatProvider

            self._prov = OpenAICompatProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            yield

    def _mock_response(self, status_code=200, text='{"choices":[{"message":{"content":"ok"}}]}'):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.text = text
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        return resp

    def test_success(self):
        resp = self._mock_response()
        self._mock_client.post.return_value = resp

        result = self._prov.generate_text("hello", "deepseek-chat")
        assert result.text == "ok"
        self._mock_client.post.assert_called_once()

    def test_returns_normalized_completion_metadata(self):
        resp = self._mock_response()
        resp.json.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        self._mock_client.post.return_value = resp

        result = self._prov.generate_text("hello", "deepseek-chat")

        assert result.text == "ok"
        assert result.finish_reason == "stop"
        assert result.block_reason is None
        assert result.token_usage is not None
        assert result.token_usage.total_tokens == 5

    def test_content_filter_returns_blocked_completion(self):
        resp = self._mock_response()
        resp.json.return_value = {
            "choices": [
                {
                    "message": {"content": "unexpected text"},
                    "finish_reason": "content_filter",
                }
            ]
        }
        self._mock_client.post.return_value = resp

        result = self._prov.generate_text("hello", "deepseek-chat")

        assert result.text == ""
        assert result.finish_reason == "content_filter"
        assert result.block_reason == "content_filter"
        assert result.blocked is True

    def test_sends_correct_payload(self):
        resp = self._mock_response()
        self._mock_client.post.return_value = resp

        self._prov.generate_text("hello", "deepseek-chat")
        call_kwargs = self._mock_client.post.call_args.kwargs
        assert call_kwargs["json"]["model"] == "deepseek-chat"
        assert call_kwargs["json"]["messages"][0]["content"] == "hello"
        assert "max_tokens" not in call_kwargs["json"]  # 0 = unlimited → omit field
        assert "temperature" not in call_kwargs["json"]  # unset → let the provider decide
        assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test-key"

    def test_sends_configured_temperature(self):
        resp = self._mock_response()
        self._mock_client.post.return_value = resp

        self._prov.generate_text("hello", "deepseek-chat", temperature=0.7)

        call_kwargs = self._mock_client.post.call_args.kwargs
        assert call_kwargs["json"]["temperature"] == 0.7

    def test_positive_max_tokens_is_sent(self):
        from clio.ai.openai_compat import OpenAICompatProvider

        with patch("clio.ai.openai_compat.httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            prov = OpenAICompatProvider(_make_cfg(max_tokens=2048), ProxyConfig(enabled=False, url=""))
            resp = self._mock_response()
            mock_client.post.return_value = resp
            prov.generate_text("hello", "deepseek-chat")
            assert mock_client.post.call_args.kwargs["json"]["max_tokens"] == 2048

    def test_429_raises_http_status_error(self):
        resp = self._mock_response(status_code=429)
        self._mock_client.post.return_value = resp

        with pytest.raises(httpx.HTTPStatusError):
            self._prov.generate_text("hello", "deepseek-chat")

    def test_429_uses_retry_after_header(self):
        resp_429 = self._mock_response(status_code=429)
        resp_429.headers = {"Retry-After": "2"}
        self._mock_client.post.side_effect = [resp_429, self._mock_response()]
        delays = []

        with patch("clio.utils.time.sleep", side_effect=delays.append):
            result = self._prov.generate_text("hello", "deepseek-chat")

        assert result.text == "ok"
        assert delays == [2.0]

    def test_rate_limit_cancel_propagates(self):
        import time

        from clio.ratelimit import RateLimiter

        self._prov._rl = RateLimiter(60)
        self._prov._rl._next_at = time.monotonic() + 30
        cancel = MagicMock(spec=threading.Event)
        cancel.is_set.return_value = False
        cancel.wait.return_value = True

        with pytest.raises(InterruptedError, match="openai_compat"):
            self._prov.generate_text("hello", "deepseek-chat", cancel_event=cancel)

        self._mock_client.post.assert_not_called()

    def test_cancel_event_skips_request(self):
        cancel = threading.Event()
        cancel.set()

        with pytest.raises(InterruptedError):
            self._prov.generate_text("hello", "deepseek-chat", cancel_event=cancel)
        self._mock_client.post.assert_not_called()

    def test_500_raises_http_status_error(self):
        resp = self._mock_response(status_code=500)
        self._mock_client.post.return_value = resp

        with pytest.raises(httpx.HTTPStatusError):
            self._prov.generate_text("hello", "deepseek-chat")

    def test_400_raises_value_error(self):
        resp = self._mock_response(status_code=400, text='{"error":"bad request"}')
        self._mock_client.post.return_value = resp

        with pytest.raises(ValueError, match="400"):
            self._prov.generate_text("hello", "deepseek-chat")

    def test_404_raises_value_error(self):
        resp = self._mock_response(status_code=404, text="not found")
        self._mock_client.post.return_value = resp

        with pytest.raises(ValueError, match="404"):
            self._prov.generate_text("hello", "deepseek-chat")

    def test_connection_error_retries(self):
        self._mock_client.post.side_effect = httpx.ConnectError("connection refused")

        with pytest.raises(httpx.ConnectError):
            self._prov.generate_text("hello", "deepseek-chat")
        assert self._mock_client.post.call_count >= 2

    def test_uses_correct_url(self):
        resp = self._mock_response()
        self._mock_client.post.return_value = resp

        self._prov.generate_text("hello", "deepseek-chat")
        call_url = self._mock_client.post.call_args.args[0]
        assert call_url == "https://api.deepseek.com/chat/completions"


class TestAnalyzeVideo:
    def test_not_implemented(self):
        with patch("clio.ai.openai_compat.httpx.Client"):
            from clio.ai.openai_compat import OpenAICompatProvider

            p = OpenAICompatProvider(_make_cfg(), ProxyConfig(enabled=False, url=""))
            with pytest.raises(NotImplementedError, match="不支持视频分析"):
                p.analyze_video("video.mp4", "desc", "model")

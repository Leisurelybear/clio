"""Regression tests: non-JSON 200 replies and transient 4xx statuses are handled cleanly."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clio.config import ProviderConfig, ProxyConfig


def _response(status_code=200, text='{"choices":[{"message":{"content":"ok"}}]}'):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.headers = {}
    resp.json.side_effect = lambda: json.loads(text)
    return resp


@pytest.fixture
def provider():
    client = MagicMock()
    cfg = ProviderConfig(name="custom", type="openai", api_key="sk-test", base_url="https://example.com/v1")
    with patch("clio.ai.openai_compat.httpx.Client", return_value=client):
        from clio.ai.openai_compat import OpenAICompatProvider

        prov = OpenAICompatProvider(cfg, ProxyConfig(enabled=False, url=""))
    return prov, client


def test_html_200_is_a_readable_error_with_status(provider):
    prov, client = provider
    client.post.return_value = _response(200, "<html>proxy login</html>")

    with pytest.raises(ValueError, match="200") as excinfo:
        prov.generate_text("hi", "m")

    assert "example.com" in str(excinfo.value)
    assert client.post.call_count == 1


def test_non_object_json_is_a_readable_error(provider):
    prov, client = provider
    client.post.return_value = _response(200, "[1, 2]")

    with pytest.raises(ValueError, match="200"):
        prov.generate_text("hi", "m")


@pytest.mark.parametrize("status", [408, 409, 425])
def test_transient_client_statuses_are_retried(provider, status):
    prov, client = provider
    client.post.side_effect = [_response(status, "busy"), _response()]

    with patch("clio.utils.time.sleep"):
        result = prov.generate_text("hi", "m")

    assert result.text == "ok"
    assert client.post.call_count == 2

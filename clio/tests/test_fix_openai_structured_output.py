"""Regression tests: structured output fallback is scoped and strict mode is schema-aware."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from clio.ai.base import StructuredOutput
from clio.ai.openai_compat import _strict_schema_compatible
from clio.config import ProviderConfig, ProxyConfig


def _response(status_code=200, text='{"choices":[{"message":{"content":"ok"}}]}'):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    return resp


@pytest.fixture
def provider():
    client = MagicMock()
    cfg = ProviderConfig(name="deepseek", type="openai", api_key="sk-test", base_url="https://example.com/v1")
    with patch("clio.ai.openai_compat.httpx.Client", return_value=client):
        from clio.ai.openai_compat import OpenAICompatProvider

        prov = OpenAICompatProvider(cfg, ProxyConfig(enabled=False, url=""))
    return prov, client


def test_rejection_only_disables_that_model_and_schema(provider):
    prov, client = provider
    unsupported = _response(400, '{"error":"response_format is not supported"}')
    client.post.side_effect = [unsupported, _response(), _response(), _response()]
    out = StructuredOutput("json_object")

    prov.generate_text("hi", "model-a", structured_output=out)
    prov.generate_text("hi", "model-b", structured_output=out)
    prov.generate_text("hi", "model-a", structured_output=StructuredOutput("other"))

    payloads = [call.kwargs["json"] for call in client.post.call_args_list]
    assert "response_format" not in payloads[1]
    assert "response_format" in payloads[2]
    assert "response_format" in payloads[3]


def test_fallback_expires(provider, monkeypatch):
    prov, client = provider
    now = [1000.0]
    monkeypatch.setattr("clio.ai.openai_compat.time.monotonic", lambda: now[0])
    client.post.side_effect = [_response(400, '{"error":"json_schema unsupported"}'), _response(), _response()]
    out = StructuredOutput("json_object")

    prov.generate_text("hi", "model-a", structured_output=out)
    now[0] += 601
    prov.generate_text("hi", "model-a", structured_output=out)

    assert "response_format" in client.post.call_args_list[2].kwargs["json"]


def test_strict_only_for_fully_required_closed_schemas():
    closed = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
        "additionalProperties": False,
    }
    optional_field = {**closed, "properties": {"a": {"type": "string"}, "b": {"type": "string"}}}
    open_object = {k: v for k, v in closed.items() if k != "additionalProperties"}
    nested_open = {
        **closed,
        "properties": {"a": {"type": "array", "items": open_object}},
    }

    assert _strict_schema_compatible(closed) is True
    assert _strict_schema_compatible(optional_field) is False
    assert _strict_schema_compatible(open_object) is False
    assert _strict_schema_compatible(nested_open) is False


def test_pydantic_schema_with_defaults_is_sent_non_strict(provider):
    prov, client = provider
    client.post.return_value = _response()
    schema = {
        "type": "object",
        "properties": {"start": {"type": "string"}, "note": {"type": "string"}},
        "required": ["start"],
    }

    prov.generate_text("hi", "m", structured_output=StructuredOutput("timeline", schema))

    assert client.post.call_args.kwargs["json"]["response_format"]["json_schema"]["strict"] is False

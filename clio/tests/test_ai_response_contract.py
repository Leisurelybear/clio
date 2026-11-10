"""Shared output contract tests for every bundled AI provider."""

from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from clio.ai.base import AIResponse, TokenUsage
from clio.config import ProviderConfig, ProxyConfig


def _gemini_cfg() -> ProviderConfig:
    return ProviderConfig(name="gemini", type="gemini", api_key="test-key", api_key_env="GEMINI_API_KEY")


def _openai_cfg() -> ProviderConfig:
    return ProviderConfig(
        name="deepseek",
        type="openai",
        api_key="test-key",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.example.test/v1",
    )


def _assert_contract(response: AIResponse) -> None:
    assert tuple(field.name for field in fields(AIResponse)) == (
        "text",
        "token_usage",
        "finish_reason",
        "block_reason",
    )
    assert isinstance(response.text, str)
    assert response.token_usage is None or isinstance(response.token_usage, TokenUsage)
    assert response.finish_reason is None or (isinstance(response.finish_reason, str) and response.finish_reason)
    assert response.block_reason is None or (isinstance(response.block_reason, str) and response.block_reason)
    assert response.blocked is (response.block_reason is not None)
    if response.blocked:
        assert response.text == ""


def test_ai_response_rejects_invalid_completion_shapes() -> None:
    with pytest.raises(TypeError, match="text"):
        AIResponse(text=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finish_reason"):
        AIResponse(text="ok", finish_reason="")
    with pytest.raises(TypeError, match="token_usage"):
        AIResponse(text="ok", token_usage=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="prompt_tokens"):
        TokenUsage(prompt_tokens=-1)
    with pytest.raises(ValueError, match="blocked"):
        AIResponse(text="unexpected", block_reason="safety")


def test_gemini_provider_returns_shared_completion_contract() -> None:
    with patch("clio.ai.gemini.genai.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value = client
        client.models.generate_content.return_value = SimpleNamespace(
            text="ok",
            candidates=[SimpleNamespace(finish_reason="STOP")],
            usage_metadata=SimpleNamespace(prompt_token_count=3, candidates_token_count=2, total_token_count=5),
            prompt_feedback=None,
        )

        from clio.ai.gemini import GeminiProvider

        response = GeminiProvider(_gemini_cfg(), ProxyConfig(enabled=False, url="")).generate_text("hello", "gemini")

    _assert_contract(response)
    assert response.finish_reason == "STOP"
    assert response.token_usage == TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5)


def test_openai_compat_provider_returns_shared_completion_contract() -> None:
    with patch("clio.ai.openai_compat.httpx.Client") as client_cls:
        client = MagicMock()
        client_cls.return_value = client
        http_response = MagicMock(spec=httpx.Response)
        http_response.status_code = 200
        http_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        client.post.return_value = http_response

        from clio.ai.openai_compat import OpenAICompatProvider

        response = OpenAICompatProvider(_openai_cfg(), ProxyConfig(enabled=False, url="")).generate_text(
            "hello", "chat"
        )

    _assert_contract(response)
    assert response.finish_reason == "stop"
    assert response.token_usage == TokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=5)


@pytest.mark.parametrize(
    ("factory", "response"),
    [
        (
            "gemini",
            SimpleNamespace(
                text="", candidates=[], usage_metadata=None, prompt_feedback=SimpleNamespace(block_reason="SAFETY")
            ),
        ),
        (
            "openai",
            {
                "choices": [{"message": {"content": ""}, "finish_reason": "content_filter"}],
                "usage": None,
            },
        ),
    ],
)
def test_providers_normalize_blocked_completions(factory: str, response: object) -> None:
    if factory == "gemini":
        with patch("clio.ai.gemini.genai.Client") as client_cls:
            client = MagicMock()
            client_cls.return_value = client
            client.models.generate_content.return_value = response
            from clio.ai.gemini import GeminiProvider

            result = GeminiProvider(_gemini_cfg(), ProxyConfig(enabled=False, url="")).generate_text("hello", "gemini")
    else:
        with patch("clio.ai.openai_compat.httpx.Client") as client_cls:
            client = MagicMock()
            client_cls.return_value = client
            http_response = MagicMock(spec=httpx.Response)
            http_response.status_code = 200
            http_response.json.return_value = response
            client.post.return_value = http_response
            from clio.ai.openai_compat import OpenAICompatProvider

            result = OpenAICompatProvider(_openai_cfg(), ProxyConfig(enabled=False, url="")).generate_text(
                "hello", "chat"
            )

    _assert_contract(result)
    assert result.blocked is True

"""Regression tests: Gemini retry wrapper error classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from clio.config import ProviderConfig, ProxyConfig


@pytest.fixture
def provider():
    with patch("clio.ai.gemini.genai.Client"):
        from clio.ai.gemini import GeminiProvider

        yield GeminiProvider(
            ProviderConfig(name="gemini", type="gemini", api_key="k", retry_attempts=2),
            ProxyConfig(enabled=False, url=""),
        )


@pytest.mark.parametrize("exc_type", [KeyboardInterrupt, SystemExit])
def test_interrupts_are_not_wrapped(provider, exc_type):
    fn = MagicMock(side_effect=exc_type())

    with pytest.raises(exc_type):
        provider._call_with_retry(fn, "test", "model")

    fn.assert_called_once()

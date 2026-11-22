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


@pytest.mark.parametrize("exc", [FileNotFoundError("gone"), PermissionError("denied"), IsADirectoryError("dir")])
def test_local_file_errors_are_not_retryable(provider, exc):
    assert provider._is_retryable(exc) is False


def test_network_oserror_is_still_retryable(provider):
    assert provider._is_retryable(ConnectionResetError("reset")) is True


def test_missing_image_fails_without_retry_delay(provider, tmp_path):
    with patch("clio.utils.time.sleep") as sleep, pytest.raises(FileNotFoundError):
        provider.analyze_images([str(tmp_path / "missing.png")], "describe", "model")

    sleep.assert_not_called()
    provider._client.models.generate_content.assert_not_called()

"""Regression tests: interactive AI requests cannot hold a request thread for long."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clio.config import ProviderConfig
from clio.ui.services.ai_models_service import _bounded_timeout, list_provider_models
from clio.ui.services.ai_test_service import _draft_timeout


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5, 5.0), (3600, 30.0), (float("inf"), 30.0), (float("nan"), 30.0), (-1, 30.0), ("x", 30.0), (0.1, 1.0)],
)
def test_bounded_timeout(value, expected):
    assert _bounded_timeout(value, 30.0, 30.0) == expected


@pytest.mark.parametrize(("value", "expected"), [(None, 120.0), (10, 10.0), (1e9, 120.0), (float("inf"), 120.0)])
def test_draft_timeout(value, expected):
    assert _draft_timeout(value) == expected


def test_draft_timeout_rejects_garbage():
    with pytest.raises(ValueError):
        _draft_timeout("not-a-number")


def test_model_listing_uses_capped_timeout(monkeypatch):
    provider = ProviderConfig(
        name="custom", type="openai", api_key="k", base_url="https://example.com/v1", timeout_sec=900.0
    )
    config = SimpleNamespace(
        ai=SimpleNamespace(providers={"custom": provider}),
        global_cfg=SimpleNamespace(proxy=SimpleNamespace(enabled=False, url=""), credential_service=None),
    )
    response = MagicMock()
    response.iter_bytes.return_value = iter([b'{"data": []}'])
    client = MagicMock()
    client.__enter__.return_value = client
    client.stream.return_value.__enter__.return_value = response
    factory = MagicMock(return_value=client)
    monkeypatch.setattr("httpx.Client", factory)

    result = list_provider_models(config, provider_name="custom")

    assert result["ok"] is True
    assert factory.call_args.kwargs["timeout"] == 30.0
    assert provider.timeout_sec == 900.0  # saved config object is not mutated

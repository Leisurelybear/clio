from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from clio.config import ProviderConfig
from clio.ui.services.ai_models_service import _list_gemini_models, list_provider_models


def _config(provider: ProviderConfig):
    return SimpleNamespace(
        ai=SimpleNamespace(providers={provider.name: provider}),
        global_cfg=SimpleNamespace(
            proxy=SimpleNamespace(enabled=False, url=""),
            credential_service=None,
        ),
    )


def test_openai_models_are_normalized_and_sorted(monkeypatch) -> None:
    provider = ProviderConfig(
        name="deepseek",
        type="openai",
        api_key="secret",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://example.com/v1",
    )
    response = MagicMock()
    response.iter_bytes.return_value = iter([b'{"data": [{"id": "models/z"}, {"id": "a"}, {"id": "a"}, {"id": ""}]}'])
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.stream.return_value.__enter__.return_value = response
    client.stream.return_value.__exit__.return_value = None
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=client))

    result = list_provider_models(_config(provider), provider_name="deepseek")

    assert result == {"ok": True, "provider": "deepseek", "models": ["a", "z"]}
    client.stream.assert_called_once_with(
        "GET",
        "https://example.com/v1/models",
        headers={"Accept": "application/json", "Authorization": "Bearer secret"},
    )


def test_openai_model_discovery_rejects_invalid_response_shape(monkeypatch) -> None:
    provider = ProviderConfig(name="custom", type="openai", api_key="secret", base_url="https://example.com/v1")
    response = MagicMock()
    response.iter_bytes.return_value = iter([b'{"data": {}}'])
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.stream.return_value.__enter__.return_value = response
    client.stream.return_value.__exit__.return_value = None
    monkeypatch.setattr("httpx.Client", MagicMock(return_value=client))

    result = list_provider_models(_config(provider), provider_name="custom")

    assert result["ok"] is False
    assert result["error"] == "model list response has invalid format"


def test_unsaved_provider_override_can_load_models(monkeypatch) -> None:
    provider = ProviderConfig(name="existing", type="openai", api_key="", api_key_env="")
    monkeypatch.setattr(
        "clio.ui.services.ai_models_service._list_openai_models",
        lambda loaded, config: ["m2", "m1"],
    )

    result = list_provider_models(
        _config(provider),
        provider_name="draft",
        provider_override={
            "type": "openai",
            "api_key": "draft-secret",
            "api_key_env": "DRAFT_API_KEY",
            "base_url": "https://example.com/v1",
        },
    )

    assert result == {"ok": True, "provider": "draft", "models": ["m2", "m1"]}


def test_model_discovery_masks_provider_secret_on_failure(monkeypatch) -> None:
    provider = ProviderConfig(
        name="deepseek",
        type="openai",
        api_key="sk-secret-value",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://example.com/v1",
    )
    monkeypatch.setattr(
        "clio.ui.services.ai_models_service._list_openai_models",
        lambda loaded, config: (_ for _ in ()).throw(RuntimeError("failed sk-secret-value")),
    )

    result = list_provider_models(_config(provider), provider_name="deepseek")

    assert result["ok"] is False
    assert "sk-secret-value" not in result["error"]
    assert "***" in result["error"]


def test_model_discovery_masks_arbitrary_provider_secret_on_failure(monkeypatch) -> None:
    provider = ProviderConfig(
        name="custom",
        type="openai",
        api_key="plain-secret",
        api_key_env="CUSTOM_API_KEY",
        base_url="https://example.com/v1",
    )
    monkeypatch.setattr(
        "clio.ui.services.ai_models_service._list_openai_models",
        lambda loaded, config: (_ for _ in ()).throw(RuntimeError("failed plain-secret")),
    )

    result = list_provider_models(_config(provider), provider_name="custom")

    assert result["ok"] is False
    assert "plain-secret" not in result["error"]
    assert "***" in result["error"]


def test_model_discovery_returns_structured_error_for_invalid_override() -> None:
    provider = ProviderConfig(name="custom", type="openai", api_key="", api_key_env="CUSTOM_API_KEY")

    result = list_provider_models(
        _config(provider),
        provider_name="draft",
        provider_override={"type": "openai", "timeout_sec": "not-a-number"},
    )

    assert result["ok"] is False
    assert result["error"] == "invalid provider configuration"


def test_model_discovery_rejects_credential_retarget_without_proof(monkeypatch) -> None:
    provider = ProviderConfig(
        name="deepseek",
        type="openai",
        api_key="old-secret",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://old.example/v1",
    )
    monkeypatch.setattr(
        "clio.ui.services.ai_models_service._list_openai_models",
        lambda loaded, config: ["should-not-call"],
    )

    result = list_provider_models(
        _config(provider),
        provider_name="deepseek",
        provider_override={
            "type": "openai",
            "credential_ref": "deepseek_api_key",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://attacker.example/v1",
        },
    )

    assert result["ok"] is False
    assert "re-enter" in result["error"]


def test_model_discovery_rebind_error_keeps_provider_name(monkeypatch) -> None:
    provider = ProviderConfig(
        name="deepseek",
        type="openai",
        api_key="old-secret",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://old.example/v1",
    )
    monkeypatch.setattr(
        "clio.ui.services.ai_models_service._list_openai_models",
        lambda loaded, config: ["should-not-call"],
    )

    result = list_provider_models(
        _config(provider),
        provider_name="deepseek",
        provider_override={
            "type": "openai",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://new.example/v1",
        },
    )

    assert result["ok"] is False
    assert result["provider"] == "deepseek"
    assert "deepseek" in result["error"]
    assert "old-secret" not in result["error"]


def test_gemini_model_discovery_uses_supported_actions(monkeypatch) -> None:
    models = [
        SimpleNamespace(name="models/text", supported_actions=["generateContent"]),
        SimpleNamespace(name="models/embed", supported_actions=["embedContent"]),
        SimpleNamespace(name="models/unknown", supported_actions=None),
    ]
    client = MagicMock()
    client.models.list.return_value = models
    client.close = MagicMock()
    monkeypatch.setattr("google.genai.Client", MagicMock(return_value=client))
    provider = ProviderConfig(name="gemini", type="gemini", api_key="key", api_key_env="GEMINI_API_KEY")

    result = _list_gemini_models(provider, _config(provider))

    assert result == ["text", "unknown"]


def test_gemini_model_discovery_bounds_pager_scan(monkeypatch) -> None:
    seen = 0

    def models():
        nonlocal seen
        for _ in range(10_005):
            seen += 1
            yield SimpleNamespace(name=f"models/model-{seen}", supported_actions=["generateContent"])

    client = MagicMock()
    client.models.list.return_value = models()
    client.close = MagicMock()
    monkeypatch.setattr("google.genai.Client", MagicMock(return_value=client))
    provider = ProviderConfig(name="gemini", type="gemini", api_key="key", api_key_env="GEMINI_API_KEY")

    result = _list_gemini_models(provider, _config(provider))

    assert len(result) == 1000
    assert seen == 1001

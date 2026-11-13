from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

from clio.ai.base import AIResponse
from clio.config import AppConfig, GlobalAIConfig, GlobalConfig, ProviderConfig, TaskConfig


def _config(*, models: list[str] | None = None) -> AppConfig:
    provider = ProviderConfig(
        name="deepseek",
        type="openai",
        api_key="sk-secret-value",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        models=["deepseek-chat"] if models is None else models,
    )
    return cast(
        AppConfig,
        SimpleNamespace(
            ai=SimpleNamespace(
                providers={"deepseek": provider},
                tasks={"voiceover": TaskConfig(provider="deepseek", model="deepseek-chat")},
            ),
            proxy=SimpleNamespace(url="", enabled=False),
        ),
    )


def test_unknown_provider_returns_structured_failure() -> None:
    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(), provider_name="missing", model="x")

    assert result["ok"] is False
    assert result["provider"] == "missing"
    assert result["model"] == "x"
    assert "missing" in result["error"]


def test_unknown_provider_masks_key_like_provider_name() -> None:
    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(), provider_name="sk-secret-value", model="m")

    assert result["ok"] is False
    assert result["model"] == "m"
    assert result["provider"] != "sk-secret-value"
    assert "sk-secret-value" not in result["provider"]
    assert "sk-secret-value" not in result["error"]
    assert "***" in result["provider"]
    assert "***" in result["error"]


def test_multiple_models_require_model() -> None:
    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(models=["a", "b"]), provider_name="deepseek", model="")

    assert result["ok"] is False
    assert result["provider"] == "deepseek"
    assert result["model"] == ""
    assert "model" in result["error"].lower()


def test_zero_models_require_model() -> None:
    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(models=[]), provider_name="deepseek", model=None)

    assert result["ok"] is False
    assert result["provider"] == "deepseek"
    assert result["model"] == ""
    assert "model" in result["error"].lower()


def test_real_global_only_app_config_can_test_single_model(monkeypatch) -> None:
    fake_provider = MagicMock()
    fake_provider.generate_text.return_value = AIResponse("ok")
    provider_cfg = ProviderConfig(
        name="deepseek",
        type="openai",
        api_key="sk-secret-value",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com/v1",
        models=["deepseek-chat"],
    )
    config = AppConfig(
        global_cfg=GlobalConfig(ai=GlobalAIConfig(providers={"deepseek": provider_cfg})),
        project_cfg=None,
    )
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(config, provider_name="deepseek")

    assert result["ok"] is True
    assert result["provider"] == "deepseek"
    assert result["model"] == "deepseek-chat"
    fake_provider.generate_text.assert_called_once_with("Reply with exactly: ok", "deepseek-chat")


def test_single_model_can_be_inferred(monkeypatch) -> None:
    fake_provider = MagicMock()
    fake_provider.generate_text.return_value = AIResponse("ok")
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(models=["deepseek-chat"]), provider_name="deepseek", model="")

    assert result["ok"] is True
    assert result["provider"] == "deepseek"
    assert result["model"] == "deepseek-chat"
    assert result["elapsed_ms"] >= 0
    assert result["message"] == "Connection test succeeded"
    fake_provider.generate_text.assert_called_once_with("Reply with exactly: ok", "deepseek-chat")


def test_success_uses_explicit_model(monkeypatch) -> None:
    fake_provider = MagicMock()
    fake_provider.generate_text.return_value = AIResponse("ok")
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(models=["a", "b"]), provider_name="deepseek", model="b")

    assert result["ok"] is True
    assert result["provider"] == "deepseek"
    assert result["model"] == "b"
    fake_provider.generate_text.assert_called_once_with("Reply with exactly: ok", "b")


def test_blocked_provider_response_is_reported_as_failure(monkeypatch) -> None:
    fake_provider = MagicMock()
    fake_provider.generate_text.return_value = AIResponse(
        "", finish_reason="content_filter", block_reason="content_filter"
    )
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(), provider_name="deepseek", model="deepseek-chat")

    assert result["ok"] is False
    assert "内容" in result["error"]
    assert "content_filter" in result["error"]


def test_provider_exception_is_sanitized(monkeypatch) -> None:
    fake_provider = MagicMock()
    fake_provider.generate_text.side_effect = RuntimeError("bad key sk-secret-value and token x" * 20)
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(), provider_name="deepseek", model="deepseek-chat")

    assert result["ok"] is False
    assert result["provider"] == "deepseek"
    assert result["model"] == "deepseek-chat"
    assert "sk-secret-value" not in result["error"]
    assert "***" in result["error"]


def test_missing_key_error_preserves_actionable_env_var(monkeypatch) -> None:
    fake_provider = MagicMock()
    fake_provider.generate_text.side_effect = RuntimeError("缺少 API key，请设置 DEEPSEEK_API_KEY")
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(), provider_name="deepseek", model="deepseek-chat")

    assert result["ok"] is False
    assert "DEEPSEEK_API_KEY" in result["error"]


def test_key_like_api_key_env_is_rejected_before_provider_call(monkeypatch) -> None:
    fake_provider = MagicMock()
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    config = _config()
    config.ai.providers["deepseek"].api_key_env = "sk-env-secret"
    result = test_provider_connection(config, provider_name="deepseek", model="deepseek-chat")

    assert result["ok"] is False
    assert "sk-env-secret" not in result["error"]
    assert "DEEPSEEK_API_KEY" in result["error"]
    fake_provider.generate_text.assert_not_called()


def test_remote_plain_http_provider_url_is_rejected_before_provider_call(monkeypatch) -> None:
    fake_provider = MagicMock()
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    config = _config()
    config.ai.providers["deepseek"].base_url = "http://attacker.example/v1"
    result = test_provider_connection(config, provider_name="deepseek", model="deepseek-chat")

    assert result["ok"] is False
    assert "HTTPS" in result["error"]
    fake_provider.generate_text.assert_not_called()


def test_known_provider_failure_masks_key_like_model(monkeypatch) -> None:
    fake_provider = MagicMock()
    fake_provider.generate_text.side_effect = RuntimeError("unknown model sk-model-secret")
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda cfg, name: fake_provider)

    from clio.ui.services.ai_test_service import test_provider_connection

    result = test_provider_connection(_config(), provider_name="deepseek", model="sk-model-secret")

    assert result["ok"] is False
    assert result["provider"] == "deepseek"
    assert result["model"] != "sk-model-secret"
    assert "sk-model-secret" not in result["model"]
    assert "sk-model-secret" not in result["error"]
    assert "***" in result["model"]
    assert "***" in result["error"]


def test_public_service_function_is_not_pytest_test() -> None:
    from clio.ui.services.ai_test_service import test_provider_connection

    assert vars(test_provider_connection).get("__test__") is False

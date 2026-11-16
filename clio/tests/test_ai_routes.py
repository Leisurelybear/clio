from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from clio.ui.routes.ai import handle_post_ai_models, handle_post_ai_test


def test_ai_test_requires_provider() -> None:
    handler = MagicMock()

    handle_post_ai_test(handler, {}, {"model": "deepseek-chat"})

    handler._send_json.assert_called_once_with({"ok": False, "error": "provider is required"}, 400)
    handler._resolve_project_dir.assert_not_called()
    handler._get_config.assert_not_called()


def test_ai_test_rejects_blank_provider(monkeypatch) -> None:
    handler = MagicMock()
    service = MagicMock()
    monkeypatch.setattr("clio.ui.routes.ai.test_provider_connection", service)

    handle_post_ai_test(handler, {}, {"provider": "   ", "model": "deepseek-chat"})

    handler._send_json.assert_called_once_with({"ok": False, "error": "provider is required"}, 400)
    handler._resolve_project_dir.assert_not_called()
    handler._get_config.assert_not_called()
    service.assert_not_called()


def test_ai_test_rejects_non_string_provider(monkeypatch) -> None:
    handler = MagicMock()
    service = MagicMock()
    monkeypatch.setattr("clio.ui.routes.ai.test_provider_connection", service)

    handle_post_ai_test(handler, {}, {"provider": 123, "model": "deepseek-chat"})

    handler._send_json.assert_called_once_with({"ok": False, "error": "provider must be a string"}, 400)
    handler._resolve_project_dir.assert_not_called()
    handler._get_config.assert_not_called()
    service.assert_not_called()


def test_ai_test_rejects_non_string_model(monkeypatch) -> None:
    handler = MagicMock()
    service = MagicMock()
    monkeypatch.setattr("clio.ui.routes.ai.test_provider_connection", service)

    handle_post_ai_test(handler, {}, {"provider": "deepseek", "model": []})

    handler._send_json.assert_called_once_with({"ok": False, "error": "model must be a string"}, 400)
    handler._resolve_project_dir.assert_not_called()
    handler._get_config.assert_not_called()
    service.assert_not_called()


def test_ai_test_calls_service_with_resolved_config(monkeypatch) -> None:
    handler = MagicMock()
    qs = {"input_dir": ["G:/trip"]}
    proj_dir = Path("G:/trip")
    cfg = MagicMock()
    result = {"ok": True, "provider": "deepseek", "model": "deepseek-chat"}
    service = MagicMock(return_value=result)
    handler._resolve_project_dir.return_value = proj_dir
    handler._get_config.return_value = cfg
    monkeypatch.setattr("clio.ui.routes.ai.test_provider_connection", service)

    handle_post_ai_test(
        handler,
        qs,
        {"provider": " deepseek ", "model": " deepseek-chat "},
    )

    handler._resolve_project_dir.assert_called_once_with(qs)
    handler._get_config.assert_called_once_with(proj_dir)
    service.assert_called_once_with(cfg, provider_name="deepseek", model="deepseek-chat")
    handler._send_json.assert_called_once_with(result)


def test_ai_test_passes_missing_model_as_none(monkeypatch) -> None:
    handler = MagicMock()
    cfg = MagicMock()
    service = MagicMock(return_value={"ok": True})
    handler._resolve_project_dir.return_value = Path("G:/trip")
    handler._get_config.return_value = cfg
    monkeypatch.setattr("clio.ui.routes.ai.test_provider_connection", service)

    handle_post_ai_test(handler, {}, {"provider": "deepseek"})

    service.assert_called_once_with(cfg, provider_name="deepseek", model="")
    handler._send_json.assert_called_once_with({"ok": True})


def test_ai_test_passes_unsaved_provider_candidate(monkeypatch) -> None:
    handler = MagicMock()
    cfg = MagicMock()
    service = MagicMock(return_value={"ok": True})
    handler._resolve_project_dir.return_value = Path("G:/trip")
    handler._get_config.return_value = cfg
    monkeypatch.setattr("clio.ui.routes.ai.test_provider_connection", service)
    candidate = {"type": "openai", "base_url": "https://example.com/v1", "models": ["m1"]}

    handle_post_ai_test(
        handler,
        {},
        {"provider": "draft", "model": "m1", "provider_config": candidate},
    )

    service.assert_called_once_with(cfg, provider_name="draft", model="m1", provider_override=candidate)


def test_ai_models_requires_provider(monkeypatch) -> None:
    handler = MagicMock()
    service = MagicMock()
    monkeypatch.setattr("clio.ui.routes.ai.list_provider_models", service)

    handle_post_ai_models(handler, {}, {})

    handler._send_json.assert_called_once_with({"ok": False, "error": "provider is required"}, 400)
    service.assert_not_called()


def test_ai_models_calls_service_with_unsaved_candidate(monkeypatch) -> None:
    handler = MagicMock()
    cfg = MagicMock()
    result = {"ok": True, "provider": "draft", "models": ["m1", "m2"]}
    service = MagicMock(return_value=result)
    handler._resolve_project_dir.return_value = Path("G:/trip")
    handler._get_config.return_value = cfg
    monkeypatch.setattr("clio.ui.routes.ai.list_provider_models", service)
    candidate = {"type": "openai", "base_url": "https://example.com/v1", "api_key": "secret"}

    handle_post_ai_models(handler, {"project": ["trip"]}, {"provider": " draft ", "provider_config": candidate})

    service.assert_called_once_with(cfg, provider_name="draft", provider_override=candidate)
    handler._send_json.assert_called_once_with(result)


def test_ai_models_returns_service_failure(monkeypatch) -> None:
    handler = MagicMock()
    handler._resolve_project_dir.return_value = Path("G:/trip")
    handler._get_config.return_value = MagicMock()
    result = {"ok": False, "provider": "draft", "error": "connection failed"}
    monkeypatch.setattr("clio.ui.routes.ai.list_provider_models", MagicMock(return_value=result))

    handle_post_ai_models(handler, {}, {"provider": "draft"})

    handler._send_json.assert_called_once_with(result)

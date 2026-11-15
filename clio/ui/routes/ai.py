from __future__ import annotations

from typing import Any

from clio.ui.handler_protocol import HandlerProtocol
from clio.ui.services.ai_test_service import test_provider_connection


def handle_post_ai_test(handler: HandlerProtocol, qs: dict[str, Any], obj: dict[str, Any]) -> None:
    raw_provider = obj.get("provider")
    if raw_provider is None:
        return handler._send_json({"ok": False, "error": "provider is required"}, 400)
    if not isinstance(raw_provider, str):
        return handler._send_json({"ok": False, "error": "provider must be a string"}, 400)

    provider = raw_provider.strip()
    if not provider:
        return handler._send_json({"ok": False, "error": "provider is required"}, 400)

    raw_model = obj.get("model")
    if raw_model is None:
        model = ""
    elif isinstance(raw_model, str):
        model = raw_model.strip()
    else:
        return handler._send_json({"ok": False, "error": "model must be a string"}, 400)

    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    # The settings dialog can test a provider before it is persisted.  The
    # candidate is used only for this request and is never written to disk.
    candidate = obj.get("provider_config")
    if candidate is not None and not isinstance(candidate, dict):
        return handler._send_json({"ok": False, "error": "provider_config must be an object"}, 400)
    if candidate is None:
        result = test_provider_connection(cfg, provider_name=provider, model=model)
    else:
        result = test_provider_connection(cfg, provider_name=provider, model=model, provider_override=candidate)
    return handler._send_json(result)

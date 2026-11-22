"""Regression tests: /api/env tolerates a UTF-8 BOM in .env (Windows Notepad)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.routes.env_routes import handle_get_env, handle_put_env


def _handler(tmp_path: Path) -> MagicMock:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("ai: {}\n", encoding="utf-8")
    handler = MagicMock()
    handler.config_path = config_path
    handler.__class__._config_cache = MagicMock()
    return handler


def test_get_env_strips_bom_and_masks_first_key(tmp_path: Path):
    handler = _handler(tmp_path)
    (tmp_path / ".env").write_bytes("\ufeffGEMINI_API_KEY=secret-1\nOTHER=1\n".encode())

    handle_get_env(handler, {})

    payload = handler._send_json.call_args.args[0]
    assert not payload["content"].startswith("\ufeff")
    assert payload["content"].startswith("GEMINI_API_KEY=********")
    assert "secret-1" not in payload["content"]


def test_put_env_restores_masked_first_key_from_bom_file(tmp_path: Path):
    handler = _handler(tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_bytes("\ufeffGEMINI_API_KEY=secret-1\n".encode())

    with (
        patch("clio.ui.routes.env_routes._load_dotenv"),
        patch("clio.ui.routes.env_routes._sync_env_to_environ"),
        patch("clio.ui.routes.env_routes._clear_provider_cache"),
    ):
        handle_put_env(handler, {}, {"content": "GEMINI_API_KEY=********\nNEW=2\n"})

    assert handler._send_json.call_args.args[0]["ok"] is True
    saved = env_path.read_bytes().decode("utf-8")
    assert not saved.startswith("\ufeff")
    assert "GEMINI_API_KEY=secret-1" in saved

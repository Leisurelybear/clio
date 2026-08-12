"""Tests for clio/ui/routes/env_routes.py."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.routes.env_routes import handle_get_env, handle_put_env


class TestHandleGetEnv:
    def test_returns_content(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        dotenv = tmp_path / ".env"
        dotenv.write_text("KEY=val\n", encoding="utf-8")
        handler.config_path = cfg
        handler._send_json = MagicMock()

        handle_get_env(handler, {})

        handler._send_json.assert_called_once_with({"content": "KEY=val\n", "path": str(dotenv)})

    def test_returns_template_when_missing(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        # No .env file exists
        handler._send_json = MagicMock()

        handle_get_env(handler, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        content = args[0][0]["content"]
        assert "#" in content  # should return a template with comments

    def test_no_dotenv_path_returns_error(self):
        handler = MagicMock()
        handler.config_path = None
        handler._send_json = MagicMock()

        handle_get_env(handler, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert "content" in args[0][0]


class TestHandlePutEnv:
    def test_invalidates_cache_after_save(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        with (
            patch("clio.ui.routes.env_routes._load_dotenv"),
            patch("clio.ui.routes.env_routes._clear_provider_cache") as mock_clear,
        ):
            handle_put_env(handler, {}, {"content": "K=v\n"})

        handler.__class__._config_cache.invalidate_all.assert_called_once()
        mock_clear.assert_called_once()

    def test_saves_to_dotenv(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        with (
            patch("clio.ui.routes.env_routes._load_dotenv") as mock_load,
            patch("clio.ui.routes.env_routes._clear_provider_cache"),
        ):
            handle_put_env(handler, {}, {"content": "NEW_KEY=val\n"})

        dotenv = tmp_path / ".env"
        assert dotenv.read_text(encoding="utf-8") == "NEW_KEY=val\n"
        mock_load.assert_called_once()

    def test_returns_path_in_response(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.env_routes._load_dotenv"):
            handle_put_env(handler, {}, {"content": "K=v\n"})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        dotenv = tmp_path / ".env"
        assert args[0][0]["path"] == str(dotenv)

    def test_empty_content_saves(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.env_routes._load_dotenv"):
            handle_put_env(handler, {}, {"content": ""})

        dotenv = tmp_path / ".env"
        assert dotenv.read_text(encoding="utf-8") == ""
        handler._send_json.assert_called_once_with({"ok": True, "path": str(dotenv)})

    def test_overrides_existing_env_var(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        dotenv = tmp_path / ".env"
        dotenv.write_text("MY_KEY=new_value\n", encoding="utf-8")

        with patch("clio.ui.routes.env_routes._load_dotenv") as mock_load:
            handle_put_env(handler, {}, {"content": "MY_KEY=new_value\n"})

        mock_load.assert_called_once_with(tmp_path, override=True)

    def test_new_file_created(self, tmp_path: Path):
        """New .env file is created when it doesn't exist."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.env_routes._load_dotenv"):
            handle_put_env(handler, {}, {"content": "NEW=val\n"})

        dotenv = tmp_path / ".env"
        assert dotenv.read_text(encoding="utf-8") == "NEW=val\n"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits not applicable on Windows")
    def test_403_on_overwrite_of_insecure_existing(self, tmp_path: Path):
        """Existing .env readable/writable by others must not be overwritten."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text("key: val\n", encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        dotenv = tmp_path / ".env"
        dotenv.write_text("OLD=1\n", encoding="utf-8")
        os.chmod(dotenv, 0o644)

        with patch("clio.ui.routes.env_routes._load_dotenv"):
            handle_put_env(handler, {}, {"content": "NEW=2\n"})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 403
        assert args[0][0]["ok"] is False
        assert dotenv.read_text(encoding="utf-8") == "OLD=1\n"

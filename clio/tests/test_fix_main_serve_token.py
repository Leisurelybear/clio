"""Regression tests for serve API-token sources (A-14)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clio.main import SERVE_TOKEN_ENV, _resolve_serve_token


def _config(token: str = ""):
    return SimpleNamespace(server=SimpleNamespace(api_token=token))


def test_token_file_is_preferred_over_env_and_config(tmp_path, monkeypatch):
    token_file = tmp_path / "token.txt"
    token_file.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv(SERVE_TOKEN_ENV, "from-env")

    args = SimpleNamespace(token=None, token_file=token_file)

    assert _resolve_serve_token(args, _config("from-config")) == "from-file"


def test_env_token_is_used_without_cli_flags(monkeypatch):
    monkeypatch.setenv(SERVE_TOKEN_ENV, "from-env")

    assert _resolve_serve_token(SimpleNamespace(token=None, token_file=None), _config("cfg")) == "from-env"


def test_config_token_is_the_fallback(monkeypatch):
    monkeypatch.delenv(SERVE_TOKEN_ENV, raising=False)

    assert _resolve_serve_token(SimpleNamespace(token=None, token_file=None), _config("cfg")) == "cfg"
    assert _resolve_serve_token(SimpleNamespace(token=None, token_file=None), _config("")) is None


def test_cli_token_still_works_but_warns(monkeypatch, capsys):
    monkeypatch.delenv(SERVE_TOKEN_ENV, raising=False)

    assert _resolve_serve_token(SimpleNamespace(token="cli", token_file=None), _config()) == "cli"
    assert "--token" in capsys.readouterr().err


def test_empty_token_file_is_rejected(tmp_path):
    token_file = tmp_path / "token.txt"
    token_file.write_text("  \n", encoding="utf-8")

    with pytest.raises(ValueError):
        _resolve_serve_token(SimpleNamespace(token=None, token_file=token_file), _config())

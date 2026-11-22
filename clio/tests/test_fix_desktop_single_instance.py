"""Regression tests: a stale lock whose port now hosts another server is taken over."""

from __future__ import annotations

import io
import json
import urllib.error
from email.message import Message

import pytest

from clio.desktop.single_instance import focus_first_instance


def _raise(code: int, body: bytes = b""):
    def _urlopen(*args, **kwargs):
        raise urllib.error.HTTPError("http://127.0.0.1:9/api/desktop/focus", code, "x", Message(), io.BytesIO(body))

    return _urlopen


@pytest.mark.parametrize("code", [404, 405, 501])
def test_foreign_server_on_stale_port_is_not_alive(monkeypatch, code):
    monkeypatch.setattr("urllib.request.urlopen", _raise(code, b"<html>Not Found</html>"))
    assert focus_first_instance("127.0.0.1", 9) is False


def test_clio_json_error_means_alive(monkeypatch):
    body = json.dumps({"ok": False, "error": "desktop window unavailable"}).encode()
    monkeypatch.setattr("urllib.request.urlopen", _raise(503, body))
    assert focus_first_instance("127.0.0.1", 9) is True


def test_server_error_without_body_still_means_alive(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", _raise(500))
    assert focus_first_instance("127.0.0.1", 9) is True

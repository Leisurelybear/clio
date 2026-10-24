"""Tests for clio/instance_probe.py."""

from __future__ import annotations

import urllib.error

import pytest

from clio.instance_probe import probe_web_ui


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def test_probe_detects_clio_marker(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: _FakeResponse(b"<html><title>Vlog</title></html>"),
    )

    assert probe_web_ui("127.0.0.1", 8800) == "clio"


def test_probe_reports_other_for_unrelated_page(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: _FakeResponse(b"<html>something else</html>"),
    )

    assert probe_web_ui("127.0.0.1", 8800) == "other"


def test_probe_reports_other_for_non_200(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_a, **_k: _FakeResponse(b"Vlog", status=404),
    )

    assert probe_web_ui("127.0.0.1", 8800) == "other"


def test_probe_reports_other_for_http_error(monkeypatch):
    def raise_http_error(*_a, **_k):
        raise urllib.error.HTTPError("http://127.0.0.1:8800/", 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", raise_http_error)

    assert probe_web_ui("127.0.0.1", 8800) == "other"


@pytest.mark.parametrize(
    "error",
    [ConnectionRefusedError(10061, "refused"), TimeoutError("timed out")],
)
def test_probe_reports_unreachable_for_os_errors(monkeypatch, error):
    def raise_error(*_a, **_k):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", raise_error)

    assert probe_web_ui("127.0.0.1", 8800) == "unreachable"

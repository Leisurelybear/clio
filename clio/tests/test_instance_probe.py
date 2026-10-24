"""Tests for clio/instance_probe.py."""

from __future__ import annotations

import http.client
import http.server
import threading
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


def _patch_open(monkeypatch, response=None, error=None):
    def fake_open(*_args, **_kwargs):
        if error is not None:
            raise error
        return response

    monkeypatch.setattr("clio.instance_probe._open_url", fake_open)


def test_probe_detects_clio_marker(monkeypatch):
    _patch_open(monkeypatch, _FakeResponse(b"<html><title>Vlog</title></html>"))

    assert probe_web_ui("127.0.0.1", 8800) == "clio"


def test_probe_reports_other_for_unrelated_page(monkeypatch):
    _patch_open(monkeypatch, _FakeResponse(b"<html>something else</html>"))

    assert probe_web_ui("127.0.0.1", 8800) == "other"


def test_probe_reports_other_for_non_200(monkeypatch):
    _patch_open(monkeypatch, _FakeResponse(b"Vlog", status=404))

    assert probe_web_ui("127.0.0.1", 8800) == "other"


def test_probe_reports_other_for_http_error(monkeypatch):
    error = urllib.error.HTTPError("http://127.0.0.1:8800/", 403, "Forbidden", {}, None)
    _patch_open(monkeypatch, error=error)

    assert probe_web_ui("127.0.0.1", 8800) == "other"


def test_probe_reports_other_for_malformed_http_response(monkeypatch):
    _patch_open(monkeypatch, error=http.client.BadStatusLine("garbage"))

    assert probe_web_ui("127.0.0.1", 8800) == "other"


@pytest.mark.parametrize(
    "error",
    [ConnectionRefusedError(10061, "refused"), TimeoutError("timed out")],
)
def test_probe_reports_unreachable_for_os_errors(monkeypatch, error):
    _patch_open(monkeypatch, error=error)

    assert probe_web_ui("127.0.0.1", 8800) == "unreachable"


class _MarkerHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<title>Vlog</title>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def test_probe_ignores_http_proxy_environment(monkeypatch):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MarkerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("HTTP_PROXY", "http://10.255.255.1:9")
        monkeypatch.setenv("http_proxy", "http://10.255.255.1:9")
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        assert probe_web_ui("127.0.0.1", server.server_address[1]) == "clio"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

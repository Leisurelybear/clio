"""Tests for bounded HTTP server (GAP-P2-09)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from unittest.mock import MagicMock, patch

from clio.ui.http_server import (
    MAX_CONCURRENT_REQUESTS,
    MAX_HEADER_BYTES,
    REQUEST_TIMEOUT_SEC,
    BoundedThreadingHTTPServer,
    header_bytes_too_large,
)


class _Handler(BaseHTTPRequestHandler):
    timeout = REQUEST_TIMEOUT_SEC

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class TestBoundedThreadingHTTPServer:
    def test_sets_socket_timeout_on_accept(self):
        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_workers=4)
        try:
            sock = MagicMock()
            with patch("socketserver.TCPServer.get_request", return_value=(sock, ("127.0.0.1", 1))):
                out_sock, _addr = server.get_request()
            assert out_sock is sock
            sock.settimeout.assert_called_once_with(REQUEST_TIMEOUT_SEC)
        finally:
            server.server_close()

    def test_rejects_when_workers_saturated(self):
        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_workers=1)
        try:
            assert server._worker_slots.acquire(blocking=False) is True
            closed = MagicMock()
            server.process_request(closed, ("127.0.0.1", 9))
            closed.close.assert_called_once()
            server._worker_slots.release()
        finally:
            server.server_close()

    def test_handler_timeout_default_applied(self):
        class Bare(BaseHTTPRequestHandler):
            def do_GET(self):
                pass

            def log_message(self, *args):
                return

        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), Bare, max_workers=2)
        try:
            assert Bare.timeout == REQUEST_TIMEOUT_SEC
        finally:
            server.server_close()


class TestHeaderBudget:
    def test_header_bytes_limit_constant_is_finite(self):
        assert MAX_HEADER_BYTES > 0
        assert MAX_CONCURRENT_REQUESTS >= 1

    def test_header_bytes_too_large_detects_oversize(self):
        assert header_bytes_too_large(b"GET / HTTP/1.1\r\n", {"X": "a" * (MAX_HEADER_BYTES + 1)})
        assert not header_bytes_too_large(b"GET / HTTP/1.1\r\n", {"X": "ok"})

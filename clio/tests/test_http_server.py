"""Tests for bounded HTTP server (GAP-P2-09)."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.http_server import (
    BUSY_ERROR,
    BUSY_RESPONSE,
    MAX_CONCURRENT_REQUESTS,
    MAX_CONCURRENT_STREAMS,
    MAX_CONNECTION_THREADS,
    MAX_HEADER_BYTES,
    REQUEST_TIMEOUT_SEC,
    STREAM_PATHS,
    BoundedThreadingHTTPServer,
    RequestBudget,
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

    def test_rejects_with_503_when_connection_cap_reached(self):
        """A silently closed socket reaches the browser as an empty response."""
        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_workers=1, max_streams=1)
        try:
            assert server._worker_slots.acquire(blocking=False) is True
            assert server._worker_slots.acquire(blocking=False) is True
            rejected = MagicMock()
            server.process_request(rejected, ("127.0.0.1", 9))
            rejected.sendall.assert_called_once_with(BUSY_RESPONSE)
            rejected.close.assert_called_once()
            server._worker_slots.release()
            server._worker_slots.release()
        finally:
            server.server_close()

    def test_busy_response_is_a_json_503(self):
        head, _, body = BUSY_RESPONSE.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 503")
        assert b"Content-Type: application/json" in head
        assert b"Connection: close" in head
        assert json.loads(body) == {"ok": False, "error": BUSY_ERROR}

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

    def test_injects_split_budget_into_handler(self):
        class Bare(BaseHTTPRequestHandler):
            def do_GET(self):
                pass

            def log_message(self, *args):
                return

        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), Bare, max_workers=3, max_streams=5)
        try:
            budget = Bare.request_budget  # type: ignore[attr-defined]
            assert isinstance(budget, RequestBudget)
            assert budget._requests._value == 3
            assert budget._streams._value == 5
        finally:
            server.server_close()

    def test_rejects_non_positive_budgets(self):
        with pytest.raises(ValueError):
            BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_workers=0)
        with pytest.raises(ValueError):
            BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_streams=0)


class TestRequestBudget:
    def test_stream_paths_are_recognised_with_query_string(self):
        for path in STREAM_PATHS:
            assert RequestBudget.is_stream(path)
            assert RequestBudget.is_stream(f"{path}?after=7")
        assert not RequestBudget.is_stream("/api/projects")
        assert not RequestBudget.is_stream("/api/tasks")

    def test_streams_do_not_consume_the_request_budget(self):
        budget = RequestBudget(requests=1, streams=1)
        assert budget.acquire("/api/notifications/stream?after=0") is True
        assert budget.acquire("/api/projects") is True
        assert budget.acquire("/api/projects") is False
        assert budget.acquire("/api/tasks/stream") is False
        budget.release("/api/projects")
        budget.release("/api/notifications/stream?after=0")
        assert budget.acquire("/api/projects") is True

    def test_rejects_non_positive_budgets(self):
        with pytest.raises(ValueError):
            RequestBudget(requests=0, streams=1)
        with pytest.raises(ValueError):
            RequestBudget(requests=1, streams=0)


class TestConnectionErrorSuppression:
    def test_process_request_swallows_connection_aborted(self):
        from clio.ui.http_server import BoundedThreadingHTTPServer

        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_workers=2)
        try:
            initial = server._worker_slots._value
            server._worker_slots.acquire(blocking=False)
            with patch.object(server, "finish_request", side_effect=ConnectionAbortedError()):
                with patch.object(server, "handle_error") as mock_handle:
                    server._process_request_thread(MagicMock(), ("127.0.0.1", 1))
                    mock_handle.assert_not_called()
            assert server._worker_slots._value == initial
        finally:
            server.server_close()

    def test_process_request_still_handles_other_errors(self):
        from clio.ui.http_server import BoundedThreadingHTTPServer

        server = BoundedThreadingHTTPServer(("127.0.0.1", 0), _Handler, max_workers=2)
        try:
            initial = server._worker_slots._value
            server._worker_slots.acquire(blocking=False)
            with patch.object(server, "finish_request", side_effect=ValueError("boom")):
                with patch.object(server, "handle_error") as mock_handle:
                    server._process_request_thread(MagicMock(), ("127.0.0.1", 1))
                    mock_handle.assert_called_once()
            assert server._worker_slots._value == initial
        finally:
            server.server_close()


class TestHeaderBudget:
    def test_header_bytes_limit_constant_is_finite(self):
        assert MAX_HEADER_BYTES > 0
        assert MAX_CONCURRENT_REQUESTS >= 1
        assert MAX_CONCURRENT_STREAMS >= 1
        assert MAX_CONNECTION_THREADS == MAX_CONCURRENT_REQUESTS + MAX_CONCURRENT_STREAMS

    def test_header_bytes_too_large_detects_oversize(self):
        assert header_bytes_too_large(b"GET / HTTP/1.1\r\n", {"X": "a" * (MAX_HEADER_BYTES + 1)})
        assert not header_bytes_too_large(b"GET / HTTP/1.1\r\n", {"X": "ok"})

"""Bounded ThreadingHTTPServer with request timeouts (GAP-P2-09).

Concurrency is split into two budgets on purpose. Short-lived requests and
long-lived SSE streams used to share one ceiling, so every open browser tab
(two streams each) permanently ate into the request budget until a handful of
tabs starved the whole API: the pool rejected new connections and the client
saw an empty response instead of an HTTP status. Streams now have their own
budget and an exhausted pool answers 503.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from http.server import ThreadingHTTPServer
from typing import Any

# Per-connection read deadline for headers + body (seconds).
REQUEST_TIMEOUT_SEC = 60.0
# Concurrent short-lived request handlers.
MAX_CONCURRENT_REQUESTS = 32
# Concurrent long-lived streams (SSE); a tab holds one per open stream, so this
# must not be carved out of MAX_CONCURRENT_REQUESTS.
MAX_CONCURRENT_STREAMS = 64
# Hard ceiling on connection threads.
MAX_CONNECTION_THREADS = MAX_CONCURRENT_REQUESTS + MAX_CONCURRENT_STREAMS
# Endpoints that hold their worker for the whole client session.
STREAM_PATHS = frozenset({"/api/notifications/stream", "/api/tasks/stream"})
# Combined request-line + header field budget (bytes).
MAX_HEADER_BYTES = 64 * 1024

# Returned when a request cannot be admitted; never drop a connection silently.
BUSY_ERROR = "server busy: too many concurrent requests"
_BUSY_BODY = json.dumps({"ok": False, "error": BUSY_ERROR}, separators=(",", ":")).encode("utf-8")
BUSY_RESPONSE = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Content-Type: application/json; charset=utf-8\r\n"
    b"Content-Length: " + str(len(_BUSY_BODY)).encode("ascii") + b"\r\n"
    b"Cache-Control: no-store\r\n"
    b"Connection: close\r\n\r\n" + _BUSY_BODY
)


def header_bytes_too_large(raw_requestline: bytes, headers: Mapping[str, str]) -> bool:
    """True when request-line + header names/values exceed MAX_HEADER_BYTES."""
    total = len(raw_requestline)
    for key, value in headers.items():
        total += len(key) + len(value)
        if total > MAX_HEADER_BYTES:
            return True
    return total > MAX_HEADER_BYTES


class RequestBudget:
    """Concurrency ceiling split into short-lived requests and long-lived streams."""

    def __init__(
        self,
        requests: int = MAX_CONCURRENT_REQUESTS,
        streams: int = MAX_CONCURRENT_STREAMS,
    ) -> None:
        if requests < 1 or streams < 1:
            raise ValueError("request and stream budgets must be >= 1")
        self._requests = threading.BoundedSemaphore(requests)
        self._streams = threading.BoundedSemaphore(streams)

    @staticmethod
    def is_stream(path: str) -> bool:
        return path.split("?", 1)[0] in STREAM_PATHS

    def _semaphore(self, path: str) -> threading.BoundedSemaphore:
        return self._streams if self.is_stream(path) else self._requests

    def acquire(self, path: str) -> bool:
        """Take a slot for one request; False when that budget is exhausted."""
        return self._semaphore(path).acquire(blocking=False)

    def release(self, path: str) -> None:
        self._semaphore(path).release()


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a worker ceiling and socket read timeouts."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = MAX_CONNECTION_THREADS

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type,
        *,
        max_workers: int = MAX_CONCURRENT_REQUESTS,
        max_streams: int = MAX_CONCURRENT_STREAMS,
        request_timeout: float = REQUEST_TIMEOUT_SEC,
        bind_and_activate: bool = True,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_streams < 1:
            raise ValueError("max_streams must be >= 1")
        self.request_budget = RequestBudget(max_workers, max_streams)
        self._worker_slots = threading.BoundedSemaphore(max_workers + max_streams)
        self.request_timeout = float(request_timeout)
        # Ensure handlers inherit the same deadline when they call setup().
        if getattr(RequestHandlerClass, "timeout", None) in (None, 0):
            RequestHandlerClass.timeout = self.request_timeout  # type: ignore[attr-defined]
        # The handler charges the split budget once it has parsed the path.
        RequestHandlerClass.request_budget = self.request_budget  # type: ignore[attr-defined]
        super().__init__(server_address, RequestHandlerClass, bind_and_activate=bind_and_activate)

    def get_request(self) -> tuple[Any, Any]:
        sock, addr = super().get_request()
        try:
            sock.settimeout(self.request_timeout)
        except OSError:
            sock.close()
            raise
        return sock, addr

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self._reject_busy(request, client_address)
            return

        t = threading.Thread(
            target=self._process_request_thread,
            args=(request, client_address),
            daemon=True,
        )
        t.start()

    def _reject_busy(self, request: Any, client_address: Any) -> None:
        """Answer 503 and close; a silent close shows up as an empty response."""
        print(f"  [serve] {client_address[0]} - 503 {BUSY_ERROR} (connection limit)")
        try:
            request.sendall(BUSY_RESPONSE)
        except OSError:
            pass
        finally:
            try:
                request.close()
            except OSError:
                pass

    def _process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            self.handle_error(request, client_address)
        finally:
            try:
                self.shutdown_request(request)
            finally:
                self._worker_slots.release()

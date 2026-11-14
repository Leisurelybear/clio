"""Bounded ThreadingHTTPServer with request timeouts (GAP-P2-09).

Concurrency is split into two budgets on purpose. Short-lived requests and
long-lived SSE streams used to share one ceiling, so every open browser tab
(two streams each) permanently ate into the request budget until a handful of
tabs starved the whole API: the pool rejected new connections and the client
saw an empty response instead of an HTTP status. Streams now have their own
budget and an exhausted pool answers 503.
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
from collections.abc import Iterable
from http.server import ThreadingHTTPServer
from typing import Any, Protocol

# Per-connection read deadline for headers + body (seconds).
REQUEST_TIMEOUT_SEC = 60.0
# Concurrent short-lived request handlers.
MAX_CONCURRENT_REQUESTS = 32
# Concurrent long-lived streams (SSE); a tab holds one per open stream, so this
# must not be carved out of MAX_CONCURRENT_REQUESTS.
MAX_CONCURRENT_STREAMS = 64
# Hard ceiling on connection threads.
MAX_CONNECTION_THREADS = MAX_CONCURRENT_REQUESTS + MAX_CONCURRENT_STREAMS
# Upper bound for waiting on active workers during orderly shutdown.
SHUTDOWN_GRACE_SEC = 5.0
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


class HeaderSource(Protocol):
    def items(self) -> Iterable[tuple[str, str]]: ...


def header_bytes_too_large(raw_requestline: bytes, headers: HeaderSource) -> bool:
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
    # Windows SO_REUSEADDR lets a second server silently share the port, so
    # requests land on whichever process bound it; fail with EADDRINUSE instead.
    allow_reuse_address = sys.platform != "win32"
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
        self._worker_threads: set[threading.Thread] = set()
        self._worker_threads_lock = threading.Lock()
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

        thread = threading.Thread(
            target=self._process_request_thread,
            args=(request, client_address),
            name="clio-http-worker",
            daemon=True,
        )
        with self._worker_threads_lock:
            self._worker_threads.add(thread)
        try:
            thread.start()
        except BaseException:
            with self._worker_threads_lock:
                self._worker_threads.discard(thread)
            self._worker_slots.release()
            raise

    def _reject_busy(self, request: Any, client_address: Any) -> None:
        """Answer 503 and close; a silent close shows up as an empty response."""
        print(f"  [serve] {client_address[0]} - 503 {BUSY_ERROR} (connection limit)")
        try:
            request.sendall(BUSY_RESPONSE)
        except OSError:
            pass
        finally:
            with contextlib.suppress(OSError):
                request.close()

    def _process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:  # noqa: BLE001 - request worker boundary must delegate every failure to handle_error
            self.handle_error(request, client_address)
        finally:
            try:
                try:
                    self.shutdown_request(request)
                finally:
                    self._worker_slots.release()
            finally:
                with self._worker_threads_lock:
                    self._worker_threads.discard(threading.current_thread())

    def worker_threads(self) -> tuple[threading.Thread, ...]:
        """Return a snapshot of live request workers for shutdown diagnostics."""
        with self._worker_threads_lock:
            return tuple(self._worker_threads)

    def _stop_stream_waiters(self) -> None:
        """Retire SSE handlers blocked in the task store during shutdown."""
        manager = getattr(self.RequestHandlerClass, "_task_manager", None)
        store = getattr(manager, "store", None)
        stop = getattr(store, "stop_waiters", None)
        if callable(stop):
            stop()

    def server_close(self) -> None:
        """Close the listener and drain request workers before returning."""
        self._stop_stream_waiters()
        try:
            super().server_close()
        finally:
            deadline = time.monotonic() + SHUTDOWN_GRACE_SEC
            while True:
                workers = [
                    thread
                    for thread in self.worker_threads()
                    if thread is not threading.current_thread() and thread.is_alive()
                ]
                if not workers:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                for thread in workers:
                    thread.join(timeout=remaining)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break

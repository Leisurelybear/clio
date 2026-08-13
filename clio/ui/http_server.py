"""Bounded ThreadingHTTPServer with request timeouts (GAP-P2-09)."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from http.server import ThreadingHTTPServer
from typing import Any

# Per-connection read deadline for headers + body (seconds).
REQUEST_TIMEOUT_SEC = 60.0
# Cap concurrent handler threads so slow clients cannot exhaust the process.
MAX_CONCURRENT_REQUESTS = 32
# Combined request-line + header field budget (bytes).
MAX_HEADER_BYTES = 64 * 1024


def header_bytes_too_large(raw_requestline: bytes, headers: Mapping[str, str]) -> bool:
    """True when request-line + header names/values exceed MAX_HEADER_BYTES."""
    total = len(raw_requestline)
    for key, value in headers.items():
        total += len(key) + len(value)
        if total > MAX_HEADER_BYTES:
            return True
    return total > MAX_HEADER_BYTES


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a worker ceiling and socket read timeouts."""

    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = MAX_CONCURRENT_REQUESTS

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type,
        *,
        max_workers: int = MAX_CONCURRENT_REQUESTS,
        request_timeout: float = REQUEST_TIMEOUT_SEC,
        bind_and_activate: bool = True,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        self.request_timeout = float(request_timeout)
        # Ensure handlers inherit the same deadline when they call setup().
        if getattr(RequestHandlerClass, "timeout", None) in (None, 0):
            RequestHandlerClass.timeout = self.request_timeout  # type: ignore[attr-defined]
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
            try:
                request.close()
            except OSError:
                pass
            return

        t = threading.Thread(
            target=self._process_request_thread,
            args=(request, client_address),
            daemon=True,
        )
        t.start()

    def _process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            try:
                self.shutdown_request(request)
            finally:
                self._worker_slots.release()

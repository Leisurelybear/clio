"""Retry helpers for transient cloud ASR HTTP failures."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import httpx

from clio.i18n import t
from clio.utils import parse_retry_after

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def _status_message(status_code: int) -> str:
    if status_code == 429:
        return t("asr.retry.too_many_requests")
    if status_code >= 500:
        return t("asr.retry.service_unavailable")
    return t("asr.retry.request_rejected")


def _retry_after_seconds(
    response: httpx.Response,
    *,
    now: Callable[[], float] | None = None,
) -> float | None:
    return parse_retry_after(response.headers.get("Retry-After"), now=now)


def request_with_retry(
    request: Callable[[], httpx.Response],
    *,
    label: str,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    cancel_event: threading.Event | None = None,
) -> httpx.Response:
    """Run a request and retry only transient network/service failures."""

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if base_delay < 0 or max_delay < 0:
        raise ValueError("retry delays must be non-negative")

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError(t("asr.retry.cancelled", {"label": label}))

    def wait_before_retry(seconds: float) -> None:
        if cancel_event is None:
            sleep(seconds)
        elif cancel_event.wait(seconds):
            raise RuntimeError(t("asr.retry.cancelled", {"label": label}))

    last_status: int | None = None
    for attempt in range(attempts):
        retry_after: float | None = None
        check_cancelled()
        try:
            response = request()
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_status = status
            if status not in RETRYABLE_STATUS_CODES or attempt == attempts - 1:
                reason = _status_message(status)
                raise RuntimeError(
                    t("asr.retry.http_failed", {"label": label, "status": status, "reason": reason})
                ) from exc
            retry_after = _retry_after_seconds(exc.response)
        except httpx.RequestError:
            if attempt == attempts - 1:
                raise RuntimeError(t("asr.retry.network_failed", {"label": label})) from None

        delay = min(base_delay * (2**attempt), max_delay)
        if retry_after is not None:
            delay = min(retry_after, max_delay)
        wait_before_retry(delay)

    check_cancelled()

    if last_status is not None:
        reason = _status_message(last_status)
        raise RuntimeError(t("asr.retry.http_failed", {"label": label, "status": last_status, "reason": reason}))
    raise RuntimeError(t("asr.retry.network_failed", {"label": label}))

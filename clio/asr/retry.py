"""Retry helpers for transient cloud ASR HTTP failures."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import httpx

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def _status_message(status_code: int) -> str:
    if status_code == 429:
        return "请求过于频繁"
    if status_code >= 500:
        return "服务暂时不可用"
    return "请求被拒绝"


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

    def check_cancelled() -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError(f"{label}已取消")

    def wait_before_retry(seconds: float) -> None:
        if cancel_event is None:
            sleep(seconds)
        elif cancel_event.wait(seconds):
            raise RuntimeError(f"{label}已取消")

    last_status: int | None = None
    for attempt in range(attempts):
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
                raise RuntimeError(f"{label}失败（HTTP {status}，{reason}），请稍后重试") from exc
        except httpx.RequestError:
            if attempt == attempts - 1:
                raise RuntimeError(f"{label}失败：网络连接不稳定，请检查网络后重试") from None

        wait_before_retry(min(base_delay * (2**attempt), max_delay))

    check_cancelled()

    if last_status is not None:
        reason = _status_message(last_status)
        raise RuntimeError(f"{label}失败（HTTP {last_status}，{reason}），请稍后重试")
    raise RuntimeError(f"{label}失败：网络连接不稳定，请检查网络后重试")

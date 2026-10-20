from __future__ import annotations

import threading

import httpx
import pytest

from clio.asr.retry import request_with_retry


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://example.com"))


def test_retries_429_then_succeeds():
    responses = iter([_response(429), _response(200)])
    delays: list[float] = []

    result = request_with_retry(
        lambda: next(responses),
        label="测试请求",
        sleep=delays.append,
    )

    assert result.status_code == 200
    assert delays == [0.5]


def test_retries_server_errors_and_network_errors():
    outcomes = iter([_response(503), _response(200)])
    delays: list[float] = []
    calls = 0

    def request():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise httpx.ConnectError("offline")
        return next(outcomes)

    result = request_with_retry(request, label="测试请求", sleep=delays.append)

    assert result.status_code == 200
    assert delays == [0.5, 1.0]


def test_non_retryable_status_fails_immediately():
    calls = 0

    def request():
        nonlocal calls
        calls += 1
        return _response(401)

    with pytest.raises(RuntimeError, match=r"测试请求失败（HTTP 401，请求被拒绝）"):
        request_with_retry(request, label="测试请求", sleep=lambda _: None)

    assert calls == 1


def test_exhausted_network_retries_are_readable():
    calls = 0

    def request():
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow")

    with pytest.raises(RuntimeError, match="网络连接不稳定"):
        request_with_retry(request, label="测试请求", attempts=2, sleep=lambda _: None)

    assert calls == 2


def test_cancelled_before_request_does_not_call_network():
    cancel_event = threading.Event()
    cancel_event.set()
    calls = 0

    def request():
        nonlocal calls
        calls += 1
        return _response(200)

    with pytest.raises(RuntimeError, match="测试请求已取消"):
        request_with_retry(request, label="测试请求", cancel_event=cancel_event)

    assert calls == 0


def test_cancel_interrupts_retry_backoff():
    class CancelDuringWait:
        def is_set(self) -> bool:
            return False

        def wait(self, timeout: float) -> bool:
            return True

    with pytest.raises(RuntimeError, match="测试请求已取消"):
        request_with_retry(
            lambda: _response(503),
            label="测试请求",
            sleep=lambda _: pytest.fail("sleep should not run when cancellation is supported"),
            cancel_event=CancelDuringWait(),
        )

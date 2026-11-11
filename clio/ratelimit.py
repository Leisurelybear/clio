from __future__ import annotations

import threading
import time

from clio.i18n import t


class RateLimiter:
    """每分钟固定次数限流器，线程安全。"""

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be > 0")
        self._interval = 60.0 / requests_per_minute
        self._lock = threading.Lock()
        self._next_at = 0.0
        self._logged = False

    def _reserve(self) -> float:
        """在锁内计算等待时长并推进下一次可用时间。"""
        wait = 0.0
        with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                wait = self._next_at - now
                if not self._logged:
                    print(t("ratelimit.wait", {"wait": f"{wait:.1f}", "interval": f"{self._interval:.1f}"}))
                    self._logged = True
                self._next_at += self._interval
            else:
                self._next_at = now + self._interval
                self._logged = False
        return wait

    def __enter__(self) -> None:
        """单线程限流上下文管理器。

        __enter__ 在锁外 sleep；多线程或需要取消时优先使用 wait()。
        """
        wait = self._reserve()
        if wait > 0:
            time.sleep(wait)

    def acquire(self) -> float:
        """非阻塞获取限流许可，返回调用方需要等待的秒数。"""
        return self._reserve()

    def wait(self, cancel_event: threading.Event | None = None, *, what: str = "") -> None:
        """阻塞等待限流许可；cancel_event 可在等待期间中断。"""
        wait = self._reserve()
        if wait <= 0:
            return
        if cancel_event is None:
            time.sleep(wait)
            return
        if cancel_event.wait(wait):
            raise InterruptedError(t("utils.retry.cancelled", {"what": what}))

    def __exit__(self, *exc_info) -> None:
        pass


def make_rate_limiter(requests_per_minute: int) -> RateLimiter | None:
    """若启用了限流则返回 RateLimiter，否则返回 None。"""
    if requests_per_minute <= 0:
        return None
    return RateLimiter(requests_per_minute)


def wait_for_rate_limit(
    limiter: RateLimiter | None,
    cancel_event: threading.Event | None = None,
    *,
    what: str = "",
) -> None:
    """Provider 共享的限流入口；未启用限流时直接返回。"""
    if limiter is not None:
        limiter.wait(cancel_event, what=what)

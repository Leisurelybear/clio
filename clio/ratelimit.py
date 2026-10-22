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

    def __enter__(self) -> None:
        """单线程限流上下文管理器。

        注意：__enter__ 在锁内 sleep，多线程场景应使用 acquire()。
        使用 acquire() 时调用方在锁外 sleep，避免锁阻塞。
        """
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
        if wait > 0:
            time.sleep(wait)

    def acquire(self) -> float:
        """非阻塞获取限流许可。

        快速返回（不 sleep），由调用方在锁外 sleep。
        适合多线程场景：锁只持有很短时间用于计算等待时长。
        """
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

    def __exit__(self, *exc_info) -> None:
        pass


def make_rate_limiter(requests_per_minute: int) -> RateLimiter | None:
    """若启用了限流则返回 RateLimiter，否则返回 None。"""
    if requests_per_minute <= 0:
        return None
    return RateLimiter(requests_per_minute)

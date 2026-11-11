from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from clio.ratelimit import RateLimiter, make_rate_limiter, wait_for_rate_limit


class TestRateLimiterInit:
    def test_negative_raises(self):
        with pytest.raises(ValueError, match="must be > 0"):
            RateLimiter(0)
        with pytest.raises(ValueError, match="must be > 0"):
            RateLimiter(-1)

    def test_sets_interval(self):
        rl = RateLimiter(60)
        assert rl._interval == 1.0

    def test_interval_120_rpm(self):
        rl = RateLimiter(120)
        assert rl._interval == 0.5


class TestRateLimiterContext:
    def test_first_call_no_wait(self, fake_time):
        rl = RateLimiter(60)
        mock_sleep = fake_time("clio.ratelimit", 100.0).sleep
        with rl:
            pass
        mock_sleep.assert_not_called()
        assert rl._next_at == 101.0

    def test_second_call_within_interval_waits(self, fake_time):
        rl = RateLimiter(60)
        rl._next_at = 105.0
        mock_sleep = fake_time("clio.ratelimit", 100.0).sleep
        with rl:
            pass
        mock_sleep.assert_called_once_with(5.0)
        assert rl._next_at == 106.0

    def test_exact_interval_no_wait(self, fake_time):
        rl = RateLimiter(60)
        rl._next_at = 100.0
        mock_sleep = fake_time("clio.ratelimit", 100.0).sleep
        with rl:
            pass
        mock_sleep.assert_not_called()
        assert rl._next_at == 101.0

    def test_logs_rate_limit_message(self, capsys, fake_time):
        rl = RateLimiter(10)
        rl._next_at = 200.0
        fake_time("clio.ratelimit", 100.0)
        with rl:
            pass
        captured = capsys.readouterr()
        assert "限流" in captured.out

    def test_only_logs_once_per_wait(self, capsys, fake_time):
        rl = RateLimiter(10)
        rl._next_at = 200.0
        fake_time("clio.ratelimit", 100.0)
        with rl:
            pass
        captured = capsys.readouterr()
        assert "限流" in captured.out

        # Second rate-limited call should NOT log again (flag stays True)
        rl._next_at = 300.0
        fake_time("clio.ratelimit", 100.0)
        with rl:
            pass
        captured2 = capsys.readouterr()
        assert "限流" not in captured2.out

    def test_logged_remains_true_while_rate_limited(self, fake_time):
        rl = RateLimiter(10)
        rl._next_at = 200.0
        assert rl._logged is False
        fake_time("clio.ratelimit", 100.0)
        with rl:
            pass
        assert rl._logged is True

    def test_resets_logged_flag_when_not_rate_limited(self, fake_time):
        rl = RateLimiter(10)
        rl._next_at = 0.0
        assert rl._logged is False
        fake_time("clio.ratelimit", 100.0)
        with rl:
            pass
        assert rl._logged is False


class TestMakeRateLimiter:
    def test_zero_returns_none(self):
        assert make_rate_limiter(0) is None

    def test_negative_returns_none(self):
        assert make_rate_limiter(-1) is None

    def test_positive_returns_instance(self):
        rl = make_rate_limiter(10)
        assert isinstance(rl, RateLimiter)
        assert rl._interval == 6.0


class TestRateLimiterWait:
    def test_wait_without_cancel_event_sleeps(self, fake_time):
        rl = RateLimiter(60)
        rl._next_at = 105.0
        mock_sleep = fake_time("clio.ratelimit", 100.0).sleep
        rl.wait()
        mock_sleep.assert_called_once_with(5.0)

    def test_wait_returns_immediately_without_delay(self, fake_time):
        rl = RateLimiter(60)
        cancel = MagicMock(spec=threading.Event)
        mock_sleep = fake_time("clio.ratelimit", 100.0).sleep
        rl.wait(cancel)
        mock_sleep.assert_not_called()
        cancel.wait.assert_not_called()

    def test_wait_raises_when_cancelled(self, fake_time):
        rl = RateLimiter(60)
        rl._next_at = 105.0
        cancel = MagicMock(spec=threading.Event)
        cancel.wait.return_value = True
        mock_sleep = fake_time("clio.ratelimit", 100.0).sleep
        with pytest.raises(InterruptedError, match="provider-x"):
            rl.wait(cancel, what="provider-x")
        mock_sleep.assert_not_called()
        cancel.wait.assert_called_once_with(5.0)

    def test_wait_continues_when_not_cancelled(self, fake_time):
        rl = RateLimiter(60)
        rl._next_at = 105.0
        cancel = MagicMock(spec=threading.Event)
        cancel.wait.return_value = False
        fake_time("clio.ratelimit", 100.0)
        rl.wait(cancel, what="provider-x")
        cancel.wait.assert_called_once_with(5.0)


class TestWaitForRateLimit:
    def test_none_limiter_is_noop(self):
        wait_for_rate_limit(None, what="provider-x")

    def test_delegates_to_limiter(self):
        limiter = MagicMock(spec=RateLimiter)
        cancel = MagicMock(spec=threading.Event)
        wait_for_rate_limit(limiter, cancel, what="provider-x")
        limiter.wait.assert_called_once_with(cancel, what="provider-x")

"""Tests for clio/log.py — pure formatting functions and TeeWriter."""

from __future__ import annotations

import io
import logging
import os

import pytest

from clio import session_log
from clio.log import (
    _MAX_CONSECUTIVE_FAILURES,
    _HourlyFileHandler,
    _TeeWriter,
    format_duration,
    format_size,
    setup_logging,
    teardown_logging,
)

# ── format_size ─────────────────────────────────────────────────────


class TestFormatSize:
    def test_zero_bytes(self):
        assert format_size(0) == "0 B"

    def test_small_bytes(self):
        assert format_size(512) == "512 B"

    def test_kilobytes(self):
        assert format_size(1_024) == "1.0 KB"

    def test_megabytes(self):
        assert format_size(1_024 * 1_024) == "1.00 MB"

    def test_gigabytes(self):
        assert format_size(1_024 * 1_024 * 1_024) == "1.00 GB"

    def test_fractional_kb(self):
        assert format_size(1_500) == "1.5 KB"

    def test_fractional_mb(self):
        assert format_size(1_024 * 1_024 * 2 + 500_000) == "2.48 MB"

    def test_negative(self):
        assert format_size(-100) == "-100 B"


# ── format_duration ─────────────────────────────────────────────────


class TestFormatDuration:
    def test_zero_seconds(self):
        assert format_duration(0) == "0s"

    def test_under_minute(self):
        assert format_duration(45) == "45s"

    def test_one_minute(self):
        assert format_duration(60) == "1m00s"

    def test_minutes_and_seconds(self):
        assert format_duration(83) == "1m23s"

    def test_one_hour(self):
        assert format_duration(3600) == "1h00m00s"

    def test_hours_minutes_seconds(self):
        assert format_duration(3723) == "1h02m03s"

    def test_negative_returns_zero_prefix(self):
        assert format_duration(-10) == "0s"

    def test_large_duration(self):
        assert format_duration(10000) == "2h46m40s"


# ── _TeeWriter ─────────────────────────────────────────────────


class TestTeeWriter:
    def test_writes_to_original(self):
        buf = io.StringIO()
        logger = logging.getLogger("test_tee_w")
        logger.setLevel(logging.INFO)
        tw = _TeeWriter(buf, logger, logging.INFO)
        tw.write("hello")
        tw.flush()
        assert buf.getvalue() == "hello"

    def test_skip_empty_message(self):
        buf = io.StringIO()
        logger = logging.getLogger("test_tee_empty")
        logger.setLevel(logging.INFO)
        tw = _TeeWriter(buf, logger, logging.INFO)
        assert tw.write("") == 0

    def test_isatty_delegates(self):
        buf = io.StringIO()
        logger = logging.getLogger("test_tee_tty")
        logger.setLevel(logging.INFO)
        tw = _TeeWriter(buf, logger, logging.INFO)
        assert tw.isatty() is False

    def test_flush_delegates(self):
        buf = io.StringIO()
        logger = logging.getLogger("test_tee_flush")
        logger.setLevel(logging.INFO)
        tw = _TeeWriter(buf, logger, logging.INFO)
        tw.write("data")
        tw.flush()
        assert buf.getvalue() == "data"

    def test_print_style_split_write_skips_blank_session_log(self):
        """print() writes content then end='\\n' separately; blank end must not
        become an empty session_log entry (UI shows a second blank '信息' row).
        """
        session_log.clear()
        buf = io.StringIO()
        logger = logging.getLogger("test_tee_blank_session")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        tw = _TeeWriter(buf, logger, logging.INFO)
        # Simulate print("[serve] ...")
        tw.write('[serve] 127.0.0.1 - "GET /api/logs" 200 -')
        tw.write("\n")
        result = session_log.read()
        assert result["total"] == 1
        assert result["logs"][0]["text"] == '[serve] 127.0.0.1 - "GET /api/logs" 200 -'
        assert buf.getvalue() == '[serve] 127.0.0.1 - "GET /api/logs" 200 -\n'


# ── setup_logging / teardown_logging ───────────────────────────


class TestSetupLogging:
    def test_teardown_restores_stdout(self, tmp_path):
        import sys

        original = sys.stdout
        try:
            setup_logging(tmp_path)
            assert sys.stdout is not original
        finally:
            teardown_logging()
        assert sys.stdout is original

    def test_teardown_restores_stderr(self, tmp_path):
        import sys

        original = sys.stderr
        try:
            setup_logging(tmp_path)
            assert sys.stderr is not original
        finally:
            teardown_logging()
        assert sys.stderr is original

    def test_setup_is_idempotent(self, tmp_path):
        setup_logging(tmp_path)
        logger1 = setup_logging(tmp_path)
        teardown_logging()
        assert logger1 is not None

    def test_teardown_before_setup_noop(self):
        import sys

        stdout = sys.stdout
        stderr = sys.stderr
        teardown_logging()
        teardown_logging()
        assert sys.stdout is stdout
        assert sys.stderr is stderr


# ── _HourlyFileHandler failure handling (GAP-P1-10) ───────────────


def _make_record(message: str = "boom") -> logging.LogRecord:
    return logging.LogRecord("clio", logging.ERROR, __file__, 42, message, None, None)


class _FailingFile:
    """File-like object whose write/flush always fail (disk full / read-only)."""

    def __init__(self) -> None:
        self.failures = 0

    def tell(self) -> int:
        return 0

    def write(self, _text: str) -> int:
        self.failures += 1
        raise OSError(28, "No space left on device")

    def flush(self) -> None:
        raise OSError(28, "No space left on device")

    def close(self) -> None:
        pass


class TestHourlyFileHandlerFailure:
    def test_emit_failure_goes_to_protected_stderr_not_tee(self, tmp_path, monkeypatch):
        """Write failure must be reported on the real stderr, never re-enter the
        logger (which would recurse through _TeeWriter -> same failing handler)."""
        protected = io.StringIO()
        monkeypatch.setattr("sys.__stderr__", protected)
        handler = _HourlyFileHandler(tmp_path)
        failing = _FailingFile()
        handler._current_file = failing

        handler.emit(_make_record())

        out = protected.getvalue()
        assert "日志写入失败" in out
        assert "No space left on device" in out
        assert failing.failures == 1

    def test_emit_failure_does_not_recurse_or_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.__stderr__", io.StringIO())
        handler = _HourlyFileHandler(tmp_path)
        handler._current_file = _FailingFile()

        # Repeated emits must not raise RecursionError even though the handler
        # keeps failing; the protected-stderr path breaks the feedback loop.
        for _ in range(_MAX_CONSECUTIVE_FAILURES * 3):
            handler.emit(_make_record())

    def test_handler_disables_after_repeated_failures(self, tmp_path, monkeypatch):
        protected = io.StringIO()
        monkeypatch.setattr("sys.__stderr__", protected)
        handler = _HourlyFileHandler(tmp_path)
        failing = _FailingFile()
        handler._current_file = failing

        for _ in range(_MAX_CONSECUTIVE_FAILURES):
            handler.emit(_make_record())

        assert handler._failed is True
        assert "已停用文件日志" in protected.getvalue()

    def test_quota_pauses_writes_with_warning(self, tmp_path, monkeypatch):
        protected = io.StringIO()
        monkeypatch.setattr("sys.__stderr__", protected)

        class _HugeFile(_FailingFile):
            def tell(self) -> int:
                return 64 * 1024 * 1024

        handler = _HourlyFileHandler(tmp_path)
        handler._current_file = _HugeFile()

        handler.emit(_make_record())

        assert "限额" in protected.getvalue()

    @pytest.mark.skipif(
        os.name == "nt",
        reason="POSIX permission bit semantics not enforced on Windows",
    )
    def test_readonly_dir_is_handled_gracefully(self, tmp_path, monkeypatch):
        protected = io.StringIO()
        monkeypatch.setattr("sys.__stderr__", protected)
        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(0o500)
        try:
            handler = _HourlyFileHandler(readonly)
            handler.emit(_make_record())
            assert "日志写入失败" in protected.getvalue()
        finally:
            readonly.chmod(0o700)


class TestClearDiskLogs:
    def test_removes_log_files(self, tmp_path):
        from clio.log import clear_disk_logs

        (tmp_path / "a.log").write_text("x", encoding="utf-8")
        (tmp_path / "keep.txt").write_text("y", encoding="utf-8")
        assert clear_disk_logs(tmp_path) == 1
        assert not (tmp_path / "a.log").exists()
        assert (tmp_path / "keep.txt").exists()


class TestTeeWriterRedaction:
    def test_session_log_masks_secrets(self):
        session_log.clear()
        buf = io.StringIO()
        logger = logging.getLogger("clio-test-tee-redact")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        tee = _TeeWriter(buf, logger, logging.INFO)
        tee.write("api_key=sk-live-supersecretvalue\n")
        entries = session_log.read()["logs"]
        assert entries
        assert "supersecretvalue" not in entries[-1]["text"]
        assert "sk-" in entries[-1]["text"] or "***" in entries[-1]["text"]
        # Console still receives original for interactive debug
        assert "supersecretvalue" in buf.getvalue()

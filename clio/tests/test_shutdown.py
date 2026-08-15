from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import clio.shutdown as shutdown_mod


@pytest.fixture(autouse=True)
def _reset_globals():
    """Reset all module-level state between tests by modifying module attributes directly."""
    with shutdown_mod._called_lock, shutdown_mod._processes_lock:
        shutdown_mod._called = False
        shutdown_mod._hooks_installed = False
        shutdown_mod._running_processes.clear()
    yield
    with shutdown_mod._called_lock, shutdown_mod._processes_lock:
        shutdown_mod._called = False
        shutdown_mod._hooks_installed = False
        shutdown_mod._running_processes.clear()


def test_register_adds_process():
    proc = MagicMock()
    shutdown_mod.register_process(proc)
    assert shutdown_mod._running_processes == [proc]


def test_register_adds_multiple():
    a = MagicMock()
    b = MagicMock()
    shutdown_mod.register_process(a)
    shutdown_mod.register_process(b)
    assert shutdown_mod._running_processes == [a, b]


def test_unregister_removes_process():
    proc = MagicMock()
    shutdown_mod.register_process(proc)
    shutdown_mod.unregister_process(proc)
    assert shutdown_mod._running_processes == []


def test_unregister_silent_when_not_found():
    proc = MagicMock()
    shutdown_mod.unregister_process(proc)
    assert shutdown_mod._running_processes == []


def test_unregister_removes_correct_one():
    a = MagicMock()
    b = MagicMock()
    shutdown_mod.register_process(a)
    shutdown_mod.register_process(b)
    shutdown_mod.unregister_process(a)
    assert shutdown_mod._running_processes == [b]


def test_before_stop_idempotent():
    with patch("clio.ai.factory._clear_provider_cache") as mock_clear:
        shutdown_mod.before_stop()
        shutdown_mod.before_stop()
        assert mock_clear.call_count == 1


def test_before_stop_terminates_alive_processes():
    alive = MagicMock()
    alive.pid = 12345
    alive.poll.return_value = None

    shutdown_mod.register_process(alive)

    with patch("clio.ai.factory._clear_provider_cache"):
        shutdown_mod.before_stop()

    alive.terminate.assert_called_once()
    alive.wait.assert_called_once_with(timeout=5)
    alive.kill.assert_not_called()


def test_before_stop_kills_when_terminate_times_out():
    proc = MagicMock()
    proc.pid = 12345
    proc.poll.return_value = None
    proc.terminate.side_effect = None
    proc.wait.side_effect = [Exception("timeout")]

    shutdown_mod.register_process(proc)

    with patch("clio.ai.factory._clear_provider_cache"):
        shutdown_mod.before_stop()

    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_before_stop_handles_no_processes():
    with patch("clio.ai.factory._clear_provider_cache") as mock_clear:
        shutdown_mod.before_stop()
        mock_clear.assert_called_once()


def test_before_stop_clears_process_list():
    a = MagicMock()
    a.poll.return_value = None
    b = MagicMock()
    b.poll.return_value = None
    shutdown_mod.register_process(a)
    shutdown_mod.register_process(b)

    with patch("clio.ai.factory._clear_provider_cache"):
        shutdown_mod.before_stop()

    assert shutdown_mod._running_processes == []


def test_before_stop_calls_clear_provider_cache():
    with patch("clio.ai.factory._clear_provider_cache") as mock_clear:
        shutdown_mod.before_stop()
        mock_clear.assert_called_once()


def test_before_stop_skips_already_exited():
    proc = MagicMock()
    proc.pid = 12345
    proc.poll.return_value = 0

    shutdown_mod.register_process(proc)

    with patch("clio.ai.factory._clear_provider_cache"):
        shutdown_mod.before_stop()

    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()


def test_install_hooks_idempotent():
    with (
        patch("clio.shutdown.atexit") as mock_atexit,
        patch("clio.shutdown.signal") as mock_signal,
    ):
        shutdown_mod.install_hooks()
        shutdown_mod.install_hooks()

        assert mock_atexit.register.call_count == 1
        mock_signal.signal.assert_called_once()


def test_install_hooks_registers_atexit():
    with (
        patch("clio.shutdown.atexit") as mock_atexit,
        patch("clio.shutdown.signal") as mock_signal,
    ):
        shutdown_mod.install_hooks()

        mock_atexit.register.assert_called_once_with(shutdown_mod.before_stop)
        mock_signal.signal.assert_called_once()


def test_install_hooks_signal_sigterm():
    with (
        patch("clio.shutdown.atexit"),
        patch("clio.shutdown.signal") as mock_signal,
    ):
        shutdown_mod.install_hooks()

        mock_signal.signal.assert_called_once_with(mock_signal.SIGTERM, shutdown_mod._signal_handler)


def test_before_stop_ignores_clear_cache_error():
    proc = MagicMock()
    proc.pid = 12345
    proc.poll.return_value = None

    shutdown_mod.register_process(proc)

    with patch("clio.ai.factory._clear_provider_cache", side_effect=Exception("fail")):
        shutdown_mod.before_stop()

    proc.terminate.assert_called_once()


def test_sprint_falls_back_when_print_raises_unicode_error(capsys):
    """Chinese messages printed at interpreter exit must not raise on cp1252 consoles."""
    calls = []
    cp1252_stream = type("Stream", (), {"encoding": "cp1252"})()

    def _raising_print(msg):
        calls.append(msg)
        if any(ord(c) > 127 for c in str(msg)):
            raise UnicodeEncodeError("cp1252", str(msg), 0, 1, "characters map to <undefined>")

    with patch("builtins.print", side_effect=_raising_print), patch.object(shutdown_mod.sys, "stdout", cp1252_stream):
        shutdown_mod._sprint("开始清理资源...")

    assert len(calls) == 2
    assert calls[0] == "开始清理资源..."
    assert all(ord(c) < 128 for c in str(calls[1]))


def test_sprint_prints_normally_when_encodable(capsys):
    shutdown_mod._sprint("[beforeStop] ok")
    assert capsys.readouterr().out == "[beforeStop] ok\n"


def test_sprint_does_not_raise_when_stdout_closed():
    """A closed stdout at interpreter exit must be swallowed (exit code 120 guard)."""
    with (
        patch("builtins.print", side_effect=OSError(9, "Bad file descriptor")),
        patch.object(shutdown_mod.sys, "stdout", type("Closed", (), {})()),
    ):
        shutdown_mod._sprint("  [beforeStop] 清理完成")


def test_sprint_flushes_after_print():
    """Messages must be flushed immediately so nothing is left buffered at exit."""
    stdout = MagicMock()
    stdout.encoding = "utf-8"
    stdout.flush = MagicMock()

    with patch("builtins.print", return_value=None), patch.object(shutdown_mod.sys, "stdout", stdout):
        shutdown_mod._sprint("hello")

    stdout.flush.assert_called_once()


def test_sprint_survives_flush_error():
    """A failing flush must not propagate out of _sprint."""
    stdout = MagicMock()
    stdout.encoding = "utf-8"
    stdout.flush.side_effect = OSError(9, "Bad file descriptor")

    with patch("builtins.print", return_value=None), patch.object(shutdown_mod.sys, "stdout", stdout):
        shutdown_mod._sprint("hello")

"""Regression tests for transcribe audio extraction fixes."""

from __future__ import annotations

import subprocess
import threading
from unittest.mock import MagicMock

from clio.tasks import transcribe as tr


def test_stop_process_kills_when_terminate_is_ignored():
    proc = MagicMock()
    proc.poll.return_value = None
    proc.wait.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5), 0]

    tr._stop_process(proc)

    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_cancel_stops_a_silent_process():
    proc = MagicMock()
    state = {"alive": True}
    proc.poll.side_effect = lambda: None if state["alive"] else 0

    def terminate():
        state["alive"] = False

    proc.terminate.side_effect = terminate
    cancel = threading.Event()
    watcher = threading.Thread(target=tr._kill_on_cancel, args=(proc, cancel))
    watcher.start()
    cancel.set()
    watcher.join(5)

    assert not watcher.is_alive()
    proc.terminate.assert_called_once()

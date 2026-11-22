"""Regression tests: a failing quit dialog never cancels running tasks unasked."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

from clio.desktop import app as app_mod


def _broken_tk(monkeypatch) -> None:
    fake_tk = types.ModuleType("tkinter")
    fake_tk.Tk = MagicMock(side_effect=RuntimeError("no display"))
    fake_tk.messagebox = MagicMock()
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)


def test_confirm_quit_defaults_to_stay_open_when_dialog_fails(monkeypatch):
    _broken_tk(monkeypatch)
    assert app_mod._confirm_quit() is False


def test_launch_prompt_still_fails_open(monkeypatch):
    _broken_tk(monkeypatch)
    assert app_mod._confirm_web_continue() is True


def test_closing_with_active_task_and_broken_dialog_keeps_tasks(monkeypatch):
    _broken_tk(monkeypatch)
    monkeypatch.setattr(app_mod, "fetch_active_tasks", lambda host, port, token="": [{"id": "t1"}])
    cancelled = []
    monkeypatch.setattr(app_mod, "request_run_cancel", lambda host, port, token="": cancelled.append(port))

    assert app_mod._handle_closing("127.0.0.1", 1234) is False
    assert cancelled == []

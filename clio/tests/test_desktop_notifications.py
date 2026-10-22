"""Tests for cross-platform best-effort desktop notifications."""

from __future__ import annotations

import subprocess

from clio.desktop import notifications
from clio.desktop.api import DesktopApi


class _Run:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, self.returncode)


def test_windows_uses_environment_not_shell(monkeypatch):
    runner = _Run()
    result = notifications.send_desktop_notification(
        "Build\nfailed",
        "line one\nline two",
        platform="win32",
        run=runner,
        which=lambda name: "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    )

    assert result is True
    cmd, kwargs = runner.calls[0]
    assert cmd[0].endswith("powershell.exe")
    assert "Build failed" not in " ".join(cmd)
    assert kwargs["env"]["CLIO_NOTIFY_TITLE"] == "Build failed"
    assert kwargs["env"]["CLIO_NOTIFY_MESSAGE"] == "line one\nline two"
    assert kwargs["timeout"] == 10.0
    assert "shell" not in kwargs


def test_macos_passes_arguments_separately():
    runner = _Run()
    result = notifications.send_desktop_notification(
        'Title "quoted"',
        "message $(touch /tmp/nope)",
        platform="darwin",
        run=runner,
        which=lambda name: "/usr/bin/osascript",
    )

    assert result is True
    cmd, kwargs = runner.calls[0]
    assert cmd[:3] == ["/usr/bin/osascript", "-e", notifications._macos_script()]
    assert cmd[-2:] == ['Title "quoted"', "message $(touch /tmp/nope)"]
    assert kwargs["timeout"] == 10.0


def test_linux_uses_notify_send():
    runner = _Run()
    result = notifications.send_desktop_notification(
        "Clio",
        "done",
        platform="linux",
        run=runner,
        which=lambda name: "/usr/bin/notify-send",
    )

    assert result is True
    cmd, _ = runner.calls[0]
    assert cmd == ["/usr/bin/notify-send", "-a", "Clio", "Clio", "done"]


def test_missing_binary_is_false_without_running():
    runner = _Run()
    assert (
        notifications.send_desktop_notification("x", "y", platform="linux", run=runner, which=lambda name: None)
        is False
    )
    assert runner.calls == []


def test_nonzero_exit_and_exception_are_false():
    assert (
        notifications.send_desktop_notification(
            "x", "y", platform="linux", run=_Run(1), which=lambda name: "/usr/bin/notify-send"
        )
        is False
    )

    def boom(*args, **kwargs):
        raise OSError("missing")

    assert (
        notifications.send_desktop_notification(
            "x", "y", platform="linux", run=boom, which=lambda name: "/usr/bin/notify-send"
        )
        is False
    )


def test_text_is_sanitized_and_timeout_clamped():
    runner = _Run()
    notifications.send_desktop_notification(
        "  a\x00 b  ",
        "x" * 2000,
        timeout_sec=999,
        platform="linux",
        run=runner,
        which=lambda name: "/usr/bin/notify-send",
    )

    cmd, kwargs = runner.calls[0]
    assert cmd[-2] == "a b"
    assert len(cmd[-1]) == notifications._MAX_MESSAGE
    assert kwargs["timeout"] == 30.0


def test_desktop_api_envelope(monkeypatch):
    monkeypatch.setattr("clio.desktop.api.send_desktop_notification", lambda title, message: True)
    assert DesktopApi.__new__(DesktopApi).notify("t", "m") == {"ok": True, "delivered": True}


def test_desktop_api_swallows_unexpected_errors(monkeypatch):
    def boom(title, message):
        raise RuntimeError("native backend unavailable")

    monkeypatch.setattr("clio.desktop.api.send_desktop_notification", boom)
    result = DesktopApi.__new__(DesktopApi).notify("t", "m")
    assert result["ok"] is False
    assert result["delivered"] is False
    assert "native backend" in result["error"]

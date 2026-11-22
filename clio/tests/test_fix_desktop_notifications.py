"""Regression tests: notifications use a registered AUMID and safe notify-send args."""

from __future__ import annotations

import subprocess

from clio.desktop import notifications


def test_windows_toast_uses_registered_app_id():
    script = notifications._windows_script()

    assert "CreateToastNotifier('Clio')" not in script
    assert "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe" in script


def test_linux_title_starting_with_dash_is_not_an_option():
    calls = []

    def _run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    notifications.send_desktop_notification(
        "-u critical", "body", platform="linux", run=_run, which=lambda name: "/usr/bin/notify-send"
    )

    assert calls[0] == ["/usr/bin/notify-send", "-a", "Clio", "--", "-u critical", "body"]

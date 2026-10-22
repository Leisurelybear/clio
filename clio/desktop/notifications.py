"""Best-effort operating-system notifications for the desktop shell."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from collections.abc import Callable

from clio.utils import run_subprocess

logger = logging.getLogger("clio.desktop.notifications")

_MAX_TITLE = 160
_MAX_MESSAGE = 1200


def _clean(value: str, limit: int, *, single_line: bool = False) -> str:
    text = "".join(ch for ch in str(value or "") if ch in "\n\t" or ord(ch) >= 32)
    if single_line:
        text = " ".join(text.replace("\t", " ").split())
    else:
        text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return text[:limit]


def _windows_script() -> str:
    return r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
    [Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$nodes = $template.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($template.CreateTextNode($env:CLIO_NOTIFY_TITLE)) > $null
$nodes.Item(1).AppendChild($template.CreateTextNode($env:CLIO_NOTIFY_MESSAGE)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Clio').Show($toast)
""".strip()


def _macos_script() -> str:
    return "on run argv\n  display notification (item 2 of argv) with title (item 1 of argv)\nend run"


def send_desktop_notification(
    title: str,
    message: str,
    *,
    timeout_sec: float = 10.0,
    platform: str | None = None,
    run: Callable[..., object] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> bool:
    """Deliver a native notification when possible; never raise."""
    clean_title = _clean(title, _MAX_TITLE, single_line=True) or "Clio"
    clean_message = _clean(message, _MAX_MESSAGE)
    platform = platform or sys.platform
    runner = run or run_subprocess
    resolver = which or shutil.which
    timeout = min(max(float(timeout_sec), 0.5), 30.0)

    try:
        if platform == "win32":
            powershell = resolver("powershell.exe") or resolver("powershell")
            if not powershell:
                return False
            env = dict(os.environ)
            env["CLIO_NOTIFY_TITLE"] = clean_title
            env["CLIO_NOTIFY_MESSAGE"] = clean_message
            result = runner(
                [powershell, "-NoProfile", "-NonInteractive", "-Command", _windows_script()],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        elif platform == "darwin":
            osascript = resolver("osascript")
            if not osascript:
                return False
            result = runner(
                [osascript, "-e", _macos_script(), "--", clean_title, clean_message],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        elif platform.startswith("linux"):
            notify_send = resolver("notify-send")
            if not notify_send:
                return False
            result = runner(
                [notify_send, "-a", "Clio", clean_title, clean_message],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        else:
            return False
    except Exception:  # noqa: BLE001 - native delivery is strictly best-effort
        logger.debug("desktop notification delivery failed", exc_info=True)
        return False
    return getattr(result, "returncode", 1) == 0

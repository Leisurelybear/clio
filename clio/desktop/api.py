# clio/desktop/api.py
"""js_api surface for the pywebview desktop shell.

Thread decision: tkinter dialogs are invoked on the pywebview js_api worker
thread (not the UI main thread). If pick_* hangs under pywebview on Windows,
switch dialogs.py to Win32 IFileOpenDialog (COM) while keeping these method names.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTENSIONS
from clio.desktop import dialogs
from clio.desktop.state import resolve_initial_dir, save_last_dir


class DesktopApi:
    def __init__(self, config_dir: Path) -> None:
        self._config_dir = Path(config_dir)
        self._window: Any | None = None

    def bind_window(self, window: Any) -> None:
        self._window = window

    def _initial(self, initial_dir: str = "", scope: str = "config", project_dir: str = "") -> str | None:
        base_dir = project_dir if scope == "project" and project_dir else self._config_dir
        return resolve_initial_dir(self._config_dir, initial_dir or None, base_dir=base_dir)

    def _native_paths(
        self,
        *,
        folder: bool,
        initial_dir: str | None,
        kind: str = "video",
        multiple: bool = False,
    ) -> tuple[bool, list[str]]:
        """Return (native_available, paths), falling back only on native errors."""
        if self._window is None or not hasattr(self._window, "create_file_dialog"):
            return False, []
        try:
            import webview

            dialog_type = webview.FOLDER_DIALOG if folder else webview.OPEN_DIALOG
            raw = self._window.create_file_dialog(
                dialog_type,
                directory=initial_dir or "",
                allow_multiple=multiple,
                file_types=() if folder else _native_file_types(kind),
            )
        except Exception:  # noqa: BLE001 - Tk remains the compatibility path
            return False, []
        if not raw:
            return True, []
        if isinstance(raw, str):
            raw = [raw]
        return True, [str(Path(path).expanduser().resolve()) for path in raw if path]

    def pick_folder(self, initial_dir: str = "", scope: str = "config", project_dir: str = "") -> dict:
        try:
            initial = self._initial(initial_dir, scope, project_dir)
            native, paths = self._native_paths(folder=True, initial_dir=initial)
            path = (paths[0] if paths else None) if native else dialogs.pick_folder(initial)
            if path:
                save_last_dir(self._config_dir, path, is_file=False)
            return dialogs.envelope_path(path)
        except Exception as e:  # noqa: BLE001 — surface to JS
            return dialogs.envelope_error(str(e))

    def pick_file(
        self,
        initial_dir: str = "",
        kind: str = "video",
        scope: str = "config",
        project_dir: str = "",
    ) -> dict:
        try:
            initial = self._initial(initial_dir, scope, project_dir)
            native, paths = self._native_paths(folder=False, initial_dir=initial, kind=kind)
            path = (paths[0] if paths else None) if native else dialogs.pick_file(initial, exts=_exts_for_kind(kind))
            if path:
                save_last_dir(self._config_dir, path, is_file=True)
            return dialogs.envelope_path(path)
        except Exception as e:  # noqa: BLE001
            return dialogs.envelope_error(str(e))

    def pick_files(
        self,
        initial_dir: str = "",
        kind: str = "video",
        scope: str = "config",
        project_dir: str = "",
    ) -> dict:
        try:
            initial = self._initial(initial_dir, scope, project_dir)
            native, paths = self._native_paths(
                folder=False,
                initial_dir=initial,
                kind=kind,
                multiple=True,
            )
            if not native:
                paths = dialogs.pick_files(initial, multiple=True, exts=_exts_for_kind(kind))
            if paths:
                save_last_dir(self._config_dir, paths[0], is_file=True)
            return dialogs.envelope_paths(paths)
        except Exception as e:  # noqa: BLE001
            return dialogs.envelope_error(str(e))


def _exts_for_kind(kind: str) -> list[str] | None:
    if kind == "exe":
        return ["exe"] if sys.platform == "win32" else []
    if kind == "any":
        # Empty list → dialogs._video_filetypes returns all-files only.
        return []
    return None  # default video set inside dialogs


def _native_file_types(kind: str) -> tuple[str, ...]:
    if kind == "exe":
        if sys.platform == "win32":
            return ("Executable (*.exe)", "All files (*.*)")
        return ("All files (*.*)",)
    if kind == "any":
        return ("All files (*.*)",)
    patterns = ";".join(f"*{ext}" for ext in sorted(VIDEO_EXTENSIONS))
    return (f"Videos ({patterns})", "All files (*.*)")

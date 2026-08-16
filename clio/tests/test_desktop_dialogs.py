# clio/tests/test_desktop_dialogs.py
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

from clio.desktop import dialogs
from clio.desktop.api import DesktopApi


def test_envelope_path_success_and_cancel():
    assert dialogs.envelope_path(r"D:\trip") == {"ok": True, "path": r"D:\trip"}
    assert dialogs.envelope_path(None) == {"ok": False, "cancelled": True}
    assert dialogs.envelope_path("") == {"ok": False, "cancelled": True}


def test_envelope_paths_success_and_cancel():
    assert dialogs.envelope_paths([r"D:\a.mp4"]) == {"ok": True, "paths": [r"D:\a.mp4"]}
    assert dialogs.envelope_paths([]) == {"ok": False, "cancelled": True}


def test_envelope_error():
    assert dialogs.envelope_error("boom") == {"ok": False, "error": "boom"}


def test_pick_folder_uses_initial_and_returns_abs(monkeypatch, tmp_path):
    chosen = tmp_path / "out"
    chosen.mkdir()
    seen = {}

    def fake_ask(initialdir=None, **kwargs):
        seen["initialdir"] = initialdir
        return str(chosen)

    monkeypatch.setattr(dialogs, "_askdirectory", fake_ask)
    assert dialogs.pick_folder(str(tmp_path)) == str(chosen.resolve())
    assert seen["initialdir"] == str(tmp_path)


def test_pick_folder_cancel(monkeypatch):
    monkeypatch.setattr(dialogs, "_askdirectory", lambda **kwargs: "")
    assert dialogs.pick_folder() is None


def test_pick_files_filters_and_multiple(monkeypatch, tmp_path):
    f1 = tmp_path / "a.mp4"
    f2 = tmp_path / "b.mov"
    f1.write_bytes(b"x")
    f2.write_bytes(b"y")
    monkeypatch.setattr(
        dialogs,
        "_askopenfilenames",
        lambda **kwargs: (str(f1), str(f2)),
    )
    paths = dialogs.pick_files(str(tmp_path), multiple=True)
    assert paths == [str(f1.resolve()), str(f2.resolve())]


def test_pick_file_single(monkeypatch, tmp_path):
    f1 = tmp_path / "a.mp4"
    f1.write_bytes(b"x")
    monkeypatch.setattr(dialogs, "_askopenfilename", lambda **kwargs: str(f1))
    assert dialogs.pick_file(str(tmp_path)) == str(f1.resolve())


def test_desktop_api_prefers_pywebview_native_dialog(monkeypatch, tmp_path):
    fake_webview = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    chosen = tmp_path / "trip"
    chosen.mkdir()
    window = MagicMock()
    window.create_file_dialog.return_value = [str(chosen)]
    api = DesktopApi(tmp_path)
    api.bind_window(window)
    tkinter_picker = MagicMock()
    monkeypatch.setattr(dialogs, "pick_folder", tkinter_picker)

    result = api.pick_folder()

    assert result == {"ok": True, "path": str(chosen.resolve())}
    window.create_file_dialog.assert_called_once()
    tkinter_picker.assert_not_called()


def test_desktop_api_native_cancel_does_not_open_tk(monkeypatch, tmp_path):
    fake_webview = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    window = MagicMock()
    window.create_file_dialog.return_value = None
    api = DesktopApi(tmp_path)
    api.bind_window(window)
    tkinter_picker = MagicMock()
    monkeypatch.setattr(dialogs, "pick_file", tkinter_picker)

    result = api.pick_file(kind="video")

    assert result == {"ok": False, "cancelled": True}
    tkinter_picker.assert_not_called()


def test_desktop_api_falls_back_to_tk_when_native_raises(monkeypatch, tmp_path):
    fake_webview = types.SimpleNamespace(FOLDER_DIALOG=20, OPEN_DIALOG=10)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    chosen = tmp_path / "clip.mp4"
    chosen.write_bytes(b"x")
    window = MagicMock()
    window.create_file_dialog.side_effect = RuntimeError("native unavailable")
    api = DesktopApi(tmp_path)
    api.bind_window(window)
    monkeypatch.setattr(dialogs, "pick_file", lambda initial, exts=None: str(chosen.resolve()))

    assert api.pick_file(kind="video") == {"ok": True, "path": str(chosen.resolve())}


def test_desktop_api_resolves_project_relative_initial_dir(tmp_path):
    project_dir = tmp_path / "project"
    output_dir = project_dir / "output"
    output_dir.mkdir(parents=True)
    api = DesktopApi(tmp_path)

    assert api._initial("./output", "project", str(project_dir)) == str(output_dir.resolve())


def test_unix_executable_picker_does_not_require_exe_suffix(monkeypatch):
    monkeypatch.setattr("clio.desktop.api.sys.platform", "linux")
    from clio.desktop.api import _exts_for_kind, _native_file_types

    assert _exts_for_kind("exe") == []
    assert _native_file_types("exe") == ("All files (*.*)",)

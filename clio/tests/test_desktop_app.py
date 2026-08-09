# clio/tests/test_desktop_app.py
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import clio.desktop.app as app_mod
from clio.config import AppConfig
from clio.desktop.server_host import (
    fetch_run_status,
    request_run_cancel,
    start_server,
    stop_server,
)


def test_handle_closing_idle_allows_close(monkeypatch):
    monkeypatch.setattr(app_mod, "fetch_run_status", lambda host, port, token="": {"status": "idle", "running": False})
    confirmed = []
    monkeypatch.setattr(app_mod, "request_run_cancel", lambda host, port, token="": confirmed.append(port))
    assert app_mod._handle_closing("127.0.0.1", 1234) is True
    assert confirmed == []


def test_handle_closing_running_confirmed_cancels(monkeypatch):
    monkeypatch.setattr(
        app_mod, "fetch_run_status", lambda host, port, token="": {"status": "running", "running": True}
    )
    monkeypatch.setattr(app_mod, "_confirm_quit", lambda: True)
    cancelled = []
    monkeypatch.setattr(app_mod, "request_run_cancel", lambda host, port, token="": cancelled.append(port))
    assert app_mod._handle_closing("127.0.0.1", 1234) is True
    assert cancelled == [1234]


def test_handle_closing_running_declined_aborts_close(monkeypatch):
    monkeypatch.setattr(
        app_mod, "fetch_run_status", lambda host, port, token="": {"status": "running", "running": True}
    )
    monkeypatch.setattr(app_mod, "_confirm_quit", lambda: False)
    cancelled = []
    monkeypatch.setattr(app_mod, "request_run_cancel", lambda host, port, token="": cancelled.append(port))
    assert app_mod._handle_closing("127.0.0.1", 1234) is False
    assert cancelled == []


def test_fetch_run_status_and_cancel_roundtrip(loaded_config: AppConfig, monkeypatch):
    monkeypatch.setattr(
        "clio.desktop.server_host.auto_reindex_if_needed",
        lambda cfg: False,
    )
    handle = start_server(
        loaded_config,
        config_path=None,
        host="127.0.0.1",
        port=0,
        api_token=None,
    )
    try:
        status = fetch_run_status(handle.host, handle.port, handle.token)
        assert isinstance(status, dict)
        assert "status" in status and "running" in status
        request_run_cancel(handle.host, handle.port, handle.token)  # no exception while idle
    finally:
        stop_server(handle)


def test_fetch_run_status_unreachable_returns_empty():
    assert fetch_run_status("127.0.0.1", 1) == {}


def test_request_run_cancel_unreachable_silent():
    request_run_cancel("127.0.0.1", 1)  # must not raise


# ---------------------------------------------------------------------------
# single-instance + web-running wiring in main()
# ---------------------------------------------------------------------------


class _FakeHandle:
    host = "127.0.0.1"
    port = 9999
    token = "fake-token"


class _EventList(list):
    """List supporting `+= handler` like pywebview's Event objects."""

    def __iadd__(self, other):
        self.append(other)
        return self


class _FakeWindow:
    def __init__(self):
        self.events = types.SimpleNamespace(closing=_EventList(), closed=_EventList())
        self.shown = False
        self.restored = False

    def show(self):
        self.shown = True

    def restore(self):
        self.restored = True


def _install_fake_webview(monkeypatch):
    fake = types.ModuleType("webview")
    fake.create_window = MagicMock(return_value=_FakeWindow())
    fake.start = MagicMock()
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake


def _run_main(monkeypatch, tmp_path, config_text: str = "key: value\n"):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(config_text, encoding="utf-8")
    monkeypatch.setattr("clio.config.load_config", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("clio.log.setup_logging", MagicMock())
    monkeypatch.setattr("clio.desktop.server_host.start_server", MagicMock(return_value=_FakeHandle()))
    monkeypatch.setattr("clio.desktop.server_host.stop_server", MagicMock())
    fake_webview = _install_fake_webview(monkeypatch)
    return app_mod.main(argv=[], config_path=cfg_file), fake_webview


def test_main_exits_when_web_running_and_user_declines(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=True))
    monkeypatch.setattr(app_mod, "_confirm_web_continue", MagicMock(return_value=False))
    start_mock = MagicMock()
    monkeypatch.setattr("clio.desktop.server_host.start_server", start_mock)
    rv, _ = _run_main(monkeypatch, tmp_path)
    assert rv == 0
    start_mock.assert_not_called()


def test_main_continues_when_web_running_and_user_accepts(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=True))
    monkeypatch.setattr(app_mod, "_confirm_web_continue", MagicMock(return_value=True))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    rv, fake_webview = _run_main(monkeypatch, tmp_path)
    assert rv == 0
    fake_webview.create_window.assert_called_once()
    fake_webview.start.assert_called_once()
    app_mod.write_lock.assert_called_once()
    cfg_file = tmp_path / "config.yaml"
    assert app_mod.write_lock.call_args.args[0].resolve() == cfg_file.parent.resolve()
    assert app_mod.write_lock.call_args.args[1] == _FakeHandle.port
    assert app_mod.write_lock.call_args.args[2] == os.getpid()


def test_main_creates_window_with_text_selection_enabled(monkeypatch, tmp_path):
    """Desktop window must enable text selection (pywebview text_select default is False)."""
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "read_lock", MagicMock(return_value=None))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    rv, fake_webview = _run_main(monkeypatch, tmp_path)
    assert rv == 0
    kwargs = fake_webview.create_window.call_args.kwargs
    assert kwargs.get("text_select") is True


def test_main_injects_token_into_window_url(monkeypatch, tmp_path):
    """The webview URL must carry ?token= so the frontend auto-captures it into
    sessionStorage (real session boundary, R-04 desktop security)."""
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "read_lock", MagicMock(return_value=None))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    rv, fake_webview = _run_main(monkeypatch, tmp_path)
    assert rv == 0
    url = fake_webview.create_window.call_args.args[1]
    assert f"?token={_FakeHandle.token}" in url
    assert url.startswith(f"http://{_FakeHandle.host}:{_FakeHandle.port}/")


def test_main_focuses_existing_instance_and_exits(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "read_lock", MagicMock(return_value={"port": 4321, "pid": 1}))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=True))
    start_mock = MagicMock()
    monkeypatch.setattr("clio.desktop.server_host.start_server", start_mock)
    monkeypatch.setattr(app_mod, "remove_lock", MagicMock())
    rv, _ = _run_main(monkeypatch, tmp_path)
    assert rv == 0
    start_mock.assert_not_called()
    app_mod.remove_lock.assert_not_called()


def test_main_takes_over_stale_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "read_lock", MagicMock(return_value={"port": 4321, "pid": 1}))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    rv, fake_webview = _run_main(monkeypatch, tmp_path)
    assert rv == 0
    fake_webview.create_window.assert_called_once()
    app_mod.write_lock.assert_called_once()


def test_main_registers_focus_callback_that_shows_window(monkeypatch, tmp_path):
    monkeypatch.setattr("clio.config.load_config", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("clio.log.setup_logging", MagicMock())
    monkeypatch.setattr("clio.desktop.server_host.start_server", MagicMock(return_value=_FakeHandle()))
    monkeypatch.setattr("clio.desktop.server_host.stop_server", MagicMock())
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    callback_holder = {}
    monkeypatch.setattr(
        app_mod,
        "set_desktop_focus_callback",
        lambda cb: callback_holder.update(cb=cb),
    )
    fake = types.ModuleType("webview")
    window = _FakeWindow()
    fake.create_window = MagicMock(return_value=window)
    fake.start = MagicMock()
    monkeypatch.setitem(sys.modules, "webview", fake)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("key: value\n", encoding="utf-8")
    rv = app_mod.main(argv=[], config_path=cfg_file)
    assert rv == 0
    assert "cb" in callback_holder
    callback_holder["cb"]()
    assert window.shown is True
    assert window.restored is True


def test_main_removes_lock_on_close(monkeypatch, tmp_path):
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    monkeypatch.setattr(app_mod, "remove_lock", MagicMock())
    rv, fake_webview = _run_main(monkeypatch, tmp_path)
    assert rv == 0
    fake_webview.start.assert_called_once()
    app_mod.remove_lock.assert_called_once()


def test_main_closed_event_does_not_stop_server_double(monkeypatch, tmp_path):
    """_on_closed no longer stops the server (finally owns cleanup after
    webview.start() returns). stop_server must be called exactly once by the
    finally block, not again by the window closed event."""
    monkeypatch.setattr("clio.log.setup_logging", MagicMock())
    stop_mock = MagicMock()
    monkeypatch.setattr("clio.desktop.server_host.start_server", MagicMock(return_value=_FakeHandle()))
    monkeypatch.setattr("clio.desktop.server_host.stop_server", stop_mock)
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    monkeypatch.setattr(app_mod, "remove_lock", MagicMock())
    fake = _install_fake_webview(monkeypatch)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("key: value\n", encoding="utf-8")
    rv = app_mod.main(argv=[], config_path=cfg_file)
    assert rv == 0
    window = fake.create_window.return_value
    assert window.events.closed, "closed handler should be registered"
    for handler in window.events.closed:
        handler()
    stop_mock.assert_called_once()


def test_main_first_launch_passes_created_config_path(monkeypatch, tmp_path):
    """First launch in a dir without config.yaml: load_config auto-creates it
    (R-040 B-1), so start_server must receive the real path instead of None
    (None made every /api/config/* GET return 500)."""
    captured = {}

    def _fake_start(cfg, config_path=None, **kw):
        captured["config_path"] = config_path
        return _FakeHandle()

    monkeypatch.setattr("clio.log.setup_logging", MagicMock())
    monkeypatch.setattr("clio.desktop.server_host.start_server", _fake_start)
    monkeypatch.setattr("clio.desktop.server_host.stop_server", MagicMock())
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    monkeypatch.setattr(app_mod, "remove_lock", MagicMock())
    _install_fake_webview(monkeypatch)

    cfg_file = tmp_path / "config.yaml"
    assert not cfg_file.exists()
    rv = app_mod.main(argv=[], config_path=cfg_file)
    assert rv == 0
    assert cfg_file.is_file(), "load_config should auto-create config.yaml"
    assert captured["config_path"] is not None
    assert Path(captured["config_path"]).is_file()


def test_main_sets_up_logging_with_config_logs_dir(monkeypatch, tmp_path):
    logs_dir = tmp_path / "logs"
    cfg = MagicMock()
    cfg.paths.logs_dir = logs_dir
    monkeypatch.setattr("clio.config.load_config", MagicMock(return_value=cfg))
    monkeypatch.setattr("clio.desktop.server_host.start_server", MagicMock(return_value=_FakeHandle()))
    monkeypatch.setattr("clio.desktop.server_host.stop_server", MagicMock())
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    setup_mock = MagicMock()
    monkeypatch.setattr("clio.log.setup_logging", setup_mock)
    _install_fake_webview(monkeypatch)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("key: value\n", encoding="utf-8")
    rv = app_mod.main(argv=[], config_path=cfg_file)
    assert rv == 0
    setup_mock.assert_called_once_with(logs_dir)


def _fake_webview_with_failing_start(monkeypatch):
    fake = types.ModuleType("webview")
    fake.create_window = MagicMock(return_value=_FakeWindow())
    fake.start = MagicMock(side_effect=RuntimeError("WebView2 runtime not found"))
    monkeypatch.setitem(sys.modules, "webview", fake)
    return fake


def test_main_shows_webview2_error_when_start_raises(monkeypatch, tmp_path):
    """B-3: WebView2 missing during webview.start() → clear dialog + non-zero exit."""
    monkeypatch.setattr("clio.config.load_config", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("clio.log.setup_logging", MagicMock())
    monkeypatch.setattr("clio.desktop.server_host.start_server", MagicMock(return_value=_FakeHandle()))
    monkeypatch.setattr("clio.desktop.server_host.stop_server", MagicMock())
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "remove_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    error_shown = []
    monkeypatch.setattr(app_mod, "_show_window_start_error", lambda e: error_shown.append(e))
    _fake_webview_with_failing_start(monkeypatch)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("key: value\n", encoding="utf-8")
    rv = app_mod.main(argv=[], config_path=cfg_file)
    assert rv == 1
    assert len(error_shown) == 1 and isinstance(error_shown[0], RuntimeError)
    app_mod.remove_lock.assert_called_once()


def test_main_shows_webview2_error_when_create_window_raises(monkeypatch, tmp_path):
    """B-3: WebView2 missing during create_window() → clear dialog + non-zero exit."""
    monkeypatch.setattr("clio.config.load_config", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr("clio.log.setup_logging", MagicMock())
    monkeypatch.setattr("clio.desktop.server_host.start_server", MagicMock(return_value=_FakeHandle()))
    monkeypatch.setattr("clio.desktop.server_host.stop_server", MagicMock())
    monkeypatch.setattr(app_mod, "is_web_running", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "focus_first_instance", MagicMock(return_value=False))
    monkeypatch.setattr(app_mod, "write_lock", MagicMock())
    monkeypatch.setattr(app_mod, "remove_lock", MagicMock())
    monkeypatch.setattr(app_mod, "set_desktop_focus_callback", MagicMock())
    error_shown = []
    monkeypatch.setattr(app_mod, "_show_window_start_error", lambda e: error_shown.append(e))
    fake = types.ModuleType("webview")
    fake.create_window = MagicMock(side_effect=RuntimeError("no WebView2"))
    fake.start = MagicMock()
    monkeypatch.setitem(sys.modules, "webview", fake)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("key: value\n", encoding="utf-8")
    rv = app_mod.main(argv=[], config_path=cfg_file)
    assert rv == 1
    assert len(error_shown) == 1 and isinstance(error_shown[0], RuntimeError)
    app_mod.write_lock.assert_not_called()
    app_mod.remove_lock.assert_called_once()


# ---------------------------------------------------------------------------
# config path resolution (R-044: desktop must not scatter config into CWD)
# ---------------------------------------------------------------------------


def test_resolve_config_prefers_explicit_param():
    p = app_mod.resolve_desktop_config_path([], "/x/y.yaml")
    assert p == Path("/x/y.yaml")


def test_resolve_config_parses_short_c_arg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = app_mod.resolve_desktop_config_path(["-c", "proj/cfg.yaml"], None)
    assert p == Path("proj/cfg.yaml")


def test_resolve_config_parses_long_config_arg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = app_mod.resolve_desktop_config_path(["--config", "proj/cfg.yaml"], None)
    assert p == Path("proj/cfg.yaml")


def test_resolve_config_parses_equals_form(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = app_mod.resolve_desktop_config_path(["--config=proj/cfg.yaml"], None)
    assert p == Path("proj/cfg.yaml")


def test_resolve_config_keeps_cwd_when_present(monkeypatch, tmp_path):
    (tmp_path / "config.yaml").write_text("k: v\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    p = app_mod.resolve_desktop_config_path([], None)
    assert p.resolve() == (tmp_path / "config.yaml").resolve()


def test_resolve_config_falls_back_to_platform_dir_when_no_cwd(monkeypatch, tmp_path):
    """Double-click/Finder launch without -c and no CWD config.yaml: fall back
    to the platform-standard config dir instead of littering the launch dir."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_mod, "platform_config_dir", lambda: Path("/virtual/clio-config"))
    p = app_mod.resolve_desktop_config_path([], None)
    assert p == Path("/virtual/clio-config/config.yaml")


def test_platform_config_dir_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", "C:/Users/u/AppData/Roaming")
    assert app_mod.platform_config_dir() == Path("C:/Users/u/AppData/Roaming") / "Clio"


def test_platform_config_dir_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert app_mod.platform_config_dir() == Path.home() / "Library" / "Application Support" / "Clio"


def test_platform_config_dir_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert app_mod.platform_config_dir() == Path.home() / ".config" / "clio"

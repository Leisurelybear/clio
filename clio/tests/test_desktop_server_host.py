# clio/tests/test_desktop_server_host.py
from __future__ import annotations

import urllib.request

from clio.config import AppConfig
from clio.desktop.server_host import start_server, stop_server


def test_start_server_binds_loopback_and_serves(loaded_config: AppConfig, monkeypatch):
    # Keep startup light: skip index scan that can touch disk heavily.
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
        assert handle.host == "127.0.0.1"
        assert handle.port > 0
        assert handle.token and len(handle.token) >= 16
        url = f"http://{handle.host}:{handle.port}/"
        with urllib.request.urlopen(url, timeout=3) as resp:
            assert resp.status == 200
            body = resp.read()
            assert b"html" in body.lower() or b"<!doctype" in body.lower() or len(body) > 0
        with urllib.request.urlopen(f"{url}api/run/status?token={handle.token}", timeout=3) as resp:
            assert resp.status == 200
    finally:
        stop_server(handle)

"""Tests for the Handler HTTP access log integration."""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.server import make_handler


def _build_handler(handler_cls, path="/api/plan", method="GET"):
    with patch.object(handler_cls, "__init__", lambda self: None):
        inst = handler_cls()
    inst.wfile = io.BytesIO()
    inst.send_response = MagicMock()
    inst.send_header = MagicMock()
    inst.end_headers = MagicMock()
    inst.send_error = MagicMock()
    inst.close_connection = True
    inst.request_version = "HTTP/1.1"
    inst.command = method
    inst.requestline = f"{method} {path} HTTP/1.1"
    inst.path = path
    inst.headers = MagicMock()
    inst.headers.get.return_value = "0"
    inst.rfile = io.BytesIO(b"{}")
    inst._http_started = time.perf_counter()
    inst._http_req_id = "7f3a"
    inst._http_response_bytes = None
    inst._http_request_body_log = None
    inst._http_response_body_log = None
    inst._http_response_started = False
    inst._http_streaming = False
    inst._http_stream_status = None
    return inst


@pytest.fixture
def mock_config(tmp_path):
    cfg = MagicMock()
    out_dir = tmp_path / "output"
    in_dir = tmp_path / "videos"
    out_dir.mkdir()
    in_dir.mkdir()
    cfg.paths.output_dir = out_dir
    cfg.project_dir = in_dir
    return cfg


@pytest.fixture
def handler_cls(mock_config):
    with patch("clio.ui.server.ConfigCache"):
        return make_handler(mock_config, Path("/fake/config.yaml"))


def test_make_handler_applies_configured_log_settings(tmp_path):
    from clio.config.models import (
        AppConfig,
        GlobalConfig,
        GlobalPathsConfig,
        ProjectConfig,
        ProjectPathsConfig,
        ServerConfig,
    )

    out_dir = tmp_path / "output"
    out_dir.mkdir()
    config = AppConfig(
        global_cfg=GlobalConfig(
            paths=GlobalPathsConfig(logs_dir=tmp_path / "logs"),
            server=ServerConfig(
                access_log_mode="all",
                access_log_slow_ms=120,
                access_log_body_bytes=2048,
                log_retention_days=3,
            ),
        ),
        project_cfg=ProjectConfig(paths=ProjectPathsConfig(output_dir=out_dir)),
        project_dir=tmp_path,
    )

    with patch("clio.ui.server.ConfigCache"):
        cls = make_handler(config, None)

    assert cls._access_log_mode == "all"
    assert cls._access_log_slow_ms == 120
    assert cls._access_log_body_bytes == 2048


class TestAccessLine:
    def test_logs_method_path_status_latency_and_size(self, handler_cls, capsys):
        handler = _build_handler(handler_cls)
        handler._http_started = time.perf_counter() - 0.05
        handler._http_response_bytes = 2160

        handler.log_request(200, "-")

        out = capsys.readouterr().out
        assert "[http] req=7f3a GET /api/plan 200" in out
        assert "ms" in out
        assert "2.1 KB" in out

    def test_masks_token_query_value(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/plan?token=secret&day=day1")

        handler.log_request(200, "-")

        out = capsys.readouterr().out
        assert "secret" not in out
        assert "token=***" in out
        assert "day=day1" in out

    def test_suppresses_static_assets(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/static/src/main.js")

        handler.log_request(200, "-")

        assert capsys.readouterr().out == ""

    def test_suppresses_successful_polling(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/notifications?after=3")

        handler.log_request(200, "-")

        assert capsys.readouterr().out == ""

    def test_logs_slow_polling(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/notifications?after=3")
        handler._http_started = time.perf_counter() - 1.0

        handler.log_request(200, "-")

        out = capsys.readouterr().out
        assert "[http]" in out
        assert "SLOW" in out

    def test_logs_static_failures(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/static/missing.js")

        handler.log_request(404, "-")

        assert "[http]" in capsys.readouterr().out

    def test_logs_mutations(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/run/start", method="POST")

        handler.log_request(202, "-")

        assert "POST /api/run/start 202" in capsys.readouterr().out

    def test_body_lines_carry_request_id(self, handler_cls, capsys):
        handler = _build_handler(handler_cls)
        handler._http_request_body_log = '{"day":"day1"}'
        handler._http_response_body_log = '{"ok":true}'

        handler.log_request(200, "-")

        out = capsys.readouterr().out
        assert "[http-req] req=7f3a" in out
        assert "[http-resp] req=7f3a" in out
        assert '{"day":"day1"}' in out
        assert '{"ok":true}' in out

    def test_suppressed_request_does_not_emit_body_lines(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/static/src/main.js")
        handler._http_request_body_log = '{"day":"day1"}'

        handler.log_request(200, "-")

        assert capsys.readouterr().out == ""


class TestResponseCapture:
    def test_send_json_records_bytes_and_redacted_body(self, handler_cls):
        handler_cls._access_log_body_bytes = 2048
        handler = _build_handler(handler_cls)

        handler._send_json({"api_key": "sk-live-secret", "ok": True})

        assert handler._http_response_bytes is not None
        assert handler._http_response_bytes > 0
        assert handler._http_response_body_log is not None
        assert "sk-live-secret" not in handler._http_response_body_log
        assert '"***"' in handler._http_response_body_log

    def test_send_bytes_records_size_only(self, handler_cls):
        handler_cls._access_log_body_bytes = 2048
        handler = _build_handler(handler_cls)

        handler._send_bytes(b"0123456789")

        assert handler._http_response_bytes == 10
        assert handler._http_response_body_log is None


class TestRequestBodyCapture:
    def _body_handler(self, handler_cls, path, raw):
        handler = _build_handler(handler_cls, path=path, method="POST")
        handler.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
        handler.rfile = io.BytesIO(raw)
        return handler

    def test_read_json_body_records_redacted_body(self, handler_cls):
        handler_cls._access_log_body_bytes = 2048
        raw = json.dumps({"api_key": "sk-live-secret", "day": "day1"}).encode()
        handler = self._body_handler(handler_cls, "/api/plan", raw)

        obj, status = handler._read_json_body()

        assert status is None
        assert obj["day"] == "day1"
        assert handler._http_request_body_log is not None
        assert "sk-live-secret" not in handler._http_request_body_log
        assert '"***"' in handler._http_request_body_log

    def test_env_body_is_never_captured(self, handler_cls):
        handler_cls._access_log_body_bytes = 2048
        raw = json.dumps({"content": "SECRET=value"}).encode()
        handler = self._body_handler(handler_cls, "/api/env", raw)

        obj, status = handler._read_json_body()

        assert status is None
        assert obj["content"] == "SECRET=value"
        assert handler._http_request_body_log is None


class TestErrorLogging:
    def test_unhandled_exception_logs_context_and_returns_500(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/plan/readiness", method="POST")

        def boom(h, qs):
            raise ValueError("boom")

        with patch.object(handler, "_send_json") as mock_send:
            handler._invoke_handler(boom, {})

        err = capsys.readouterr().err
        assert "[http-error] req=7f3a" in err
        assert "POST /api/plan/readiness" in err
        assert "ValueError: boom" in err
        mock_send.assert_called_once()
        _payload, status = mock_send.call_args[0]
        assert status == 500

    def test_exception_after_response_started_does_not_send_again(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/video")
        handler._http_response_started = True

        def boom(h, qs):
            raise ValueError("late boom")

        with patch.object(handler, "_send_json") as mock_send:
            handler._invoke_handler(boom, {})

        assert "[http-error] req=7f3a" in capsys.readouterr().err
        mock_send.assert_not_called()


def test_log_message_redacts_sensitive_query(handler_cls, capsys):
    handler = _build_handler(handler_cls)
    handler.address_string = lambda: "127.0.0.1"

    handler.log_message("%s", "GET /api/plan?token=super-secret HTTP/1.1")

    out = capsys.readouterr().out
    assert "super-secret" not in out
    assert "token=***" in out


def test_client_disconnect_is_not_logged_as_internal_error(handler_cls, capsys):
    handler = _build_handler(handler_cls)

    def boom(*args, **kwargs):
        raise ConnectionResetError("peer reset")

    handler._invoke_handler(boom, {})

    assert "[http-error]" not in capsys.readouterr().err


class TestStreamLifecycle:
    def test_stream_connect_is_suppressed_until_close(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/tasks/stream")
        handler._http_streaming = True

        handler.log_request(200, "-")
        assert capsys.readouterr().out == ""

        handler._http_stream_status = 200
        handler._log_stream_close()

        out = capsys.readouterr().out
        assert "[http]" in out
        assert "GET /api/tasks/stream 200" in out
        assert "STREAM" in out

    def test_stream_error_is_logged_once_on_close(self, handler_cls, capsys):
        handler = _build_handler(handler_cls, path="/api/tasks/stream")
        handler._http_streaming = True

        handler.log_request(500, "-")
        assert capsys.readouterr().out == ""

        handler._log_stream_close()

        out = capsys.readouterr().out
        assert out.count("[http]") == 1
        assert "GET /api/tasks/stream 500" in out

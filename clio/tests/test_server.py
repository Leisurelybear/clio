"""Tests for clio/ui/server.py — HTTP server & Handler."""

from __future__ import annotations

import io
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from clio.task_center.manager import TaskManager
from clio.task_center.models import TaskKind
from clio.task_center.store import TaskStore
from clio.ui.server import (
    MAX_JSON_BODY_BYTES,
    STATIC_DIR,
    _parse_json_content_length,
    _ServerState,
    make_handler,
    register_builtin_task_handlers,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_builtin_task_handlers_are_registered_for_cold_start(tmp_path):
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)

    register_builtin_task_handlers(manager)
    register_builtin_task_handlers(manager)

    assert set(manager.registry.kinds()) == {
        TaskKind.PIPELINE,
        TaskKind.RERUN,
        TaskKind.CUT_EXPORT,
        TaskKind.EXPORT,
        TaskKind.WHISPER_INSTALL,
        TaskKind.WAVEFORM,
    }


def _build_handler(handler_cls, path="/", method="GET"):
    """Create a Handler instance with mocked socket/base-class dependencies."""
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
    return inst


@pytest.fixture
def mock_config(tmp_path):
    """Minimal AppConfig-like object for make_handler (real dirs, no project.json)."""
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
    """Return a Handler class (ConfigCache mocked out)."""
    with patch("clio.ui.server.ConfigCache"):
        hcls = make_handler(mock_config, Path("/fake/config.yaml"))
    yield hcls


# ===========================================================================
# make_handler basics
# ===========================================================================


class TestMakeHandler:
    def test_returns_handler_subclass(self, mock_config):
        with patch("clio.ui.server.ConfigCache"):
            hcls = make_handler(mock_config, None)
        assert issubclass(hcls, BaseHTTPRequestHandler)

    def test_sets_class_attributes(self, mock_config):
        with patch("clio.ui.server.ConfigCache"):
            hcls = make_handler(mock_config, Path("/fake/config.yaml"))
        assert isinstance(hcls._project_states, dict)
        assert hcls._project_states == {}
        assert hasattr(hcls, "_config_cache")
        assert hasattr(hcls, "DEFAULT_PROJECT")
        assert hasattr(hcls, "project_dir")
        assert hasattr(hcls, "output_dir")
        assert hasattr(hcls, "config_path")
        assert hcls.config_path == Path("/fake/config.yaml")

    def test_default_project_values(self, tmp_path, mock_config):
        in_dir = tmp_path / "my-trip"
        in_dir.mkdir()
        mock_config.project_dir = in_dir
        with patch("clio.ui.server.ConfigCache"):
            hcls = make_handler(mock_config, None)
        dp = hcls.DEFAULT_PROJECT
        assert dp["name"] == "my-trip"
        assert dp["source"] == "compressed"
        assert dp["currentDay"] == "day1"
        assert dp["lastEntity"] is None
        assert dp["lastVideo"] is None
        assert "output_dir" in dp

    def test_config_cache_on_load(self, mock_config):
        with patch("clio.ui.server.ConfigCache") as mock_cc:
            make_handler(mock_config, Path("/fake/config.yaml"))
        mock_cc.assert_called_once()
        args, kwargs = mock_cc.call_args
        assert args[0] == Path("/fake/config.yaml")
        assert callable(kwargs["on_load"])


# ===========================================================================
# Migration logic
# ===========================================================================


class TestMigration:
    def test_migrate_old_to_new(self, tmp_path: Path):
        output_dir = tmp_path / "output"
        input_dir = tmp_path / "videos"
        output_dir.mkdir()
        input_dir.mkdir()
        old_proj = output_dir / "project.json"
        old_proj.write_text(json.dumps({"name": "videos"}), encoding="utf-8")

        cfg = MagicMock()
        cfg.paths.output_dir = output_dir
        cfg.project_dir = input_dir
        with patch("clio.ui.server.ConfigCache"):
            make_handler(cfg, None)

        new_proj = input_dir / "project.json"
        assert new_proj.is_file()
        assert json.loads(new_proj.read_text(encoding="utf-8"))["name"] == "videos"

    def test_migration_no_copy_when_new_exists(self, tmp_path: Path):
        output_dir = tmp_path / "output"
        input_dir = tmp_path / "videos"
        output_dir.mkdir()
        input_dir.mkdir()
        (output_dir / "project.json").write_text(json.dumps({"name": "old"}), encoding="utf-8")
        new_proj = input_dir / "project.json"
        new_proj.write_text(json.dumps({"name": "existing"}), encoding="utf-8")

        cfg = MagicMock()
        cfg.paths.output_dir = output_dir
        cfg.project_dir = input_dir
        with patch("clio.ui.server.ConfigCache"):
            make_handler(cfg, None)

        assert json.loads(new_proj.read_text(encoding="utf-8"))["name"] == "existing"

    def test_migration_copy_error_ignored(self, tmp_path: Path):
        output_dir = tmp_path / "output"
        input_dir = tmp_path / "videos"
        input_dir.mkdir()
        # output_dir does NOT have project.json — no copy attempted
        cfg = MagicMock()
        cfg.paths.output_dir = output_dir
        cfg.project_dir = input_dir
        with patch("clio.ui.server.ConfigCache"):
            make_handler(cfg, None)

        assert not (input_dir / "project.json").is_file()

    def test_migration_fix_name(self, tmp_path: Path):
        """When old project.json name == output_dir name & input_dir name differs, fix it."""
        output_dir = tmp_path / "output"
        input_dir = tmp_path / "videos"
        output_dir.mkdir()
        input_dir.mkdir()
        # Old project.json has name matching output_dir name
        old_proj = output_dir / "project.json"
        old_proj.write_text(json.dumps({"name": "output"}), encoding="utf-8")

        cfg = MagicMock()
        cfg.paths.output_dir = output_dir
        cfg.project_dir = input_dir
        with patch("clio.ui.server.ConfigCache"):
            make_handler(cfg, None)

        new_proj = input_dir / "project.json"
        assert json.loads(new_proj.read_text(encoding="utf-8"))["name"] == "videos"

    def test_migration_skip_name_fix_when_same(self, tmp_path: Path):
        """When input_dir name == output_dir name, name should not be changed."""
        base = tmp_path / "same_name"
        base.mkdir()
        proj_file = base / "project.json"
        proj_file.write_text(json.dumps({"name": "same_name"}), encoding="utf-8")

        cfg = MagicMock()
        cfg.paths.output_dir = base
        cfg.project_dir = base
        with patch("clio.ui.server.ConfigCache"):
            make_handler(cfg, None)

        assert json.loads(proj_file.read_text(encoding="utf-8"))["name"] == "same_name"

    def test_migration_json_decode_error_ignored(self, tmp_path: Path):
        output_dir = tmp_path / "output"
        input_dir = tmp_path / "videos"
        output_dir.mkdir()
        input_dir.mkdir()
        (output_dir / "project.json").write_text("not valid json", encoding="utf-8")

        cfg = MagicMock()
        cfg.paths.output_dir = output_dir
        cfg.project_dir = input_dir
        with patch("clio.ui.server.ConfigCache"):
            make_handler(cfg, None)

        new_proj = input_dir / "project.json"
        assert new_proj.read_text(encoding="utf-8") == "not valid json"


# ===========================================================================
# _send_json / _send_bytes / _send_static
# ===========================================================================


class TestSendJson:
    def test_sends_json_with_headers(self, handler_cls):
        handler = _build_handler(handler_cls)
        obj = {"hello": "世界", "n": 42}
        handler._send_json(obj, 201)

        expected = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        handler.send_response.assert_called_once_with(201)
        assert handler.send_header.call_args_list == [
            call("Content-Type", "application/json; charset=utf-8"),
            call("Content-Length", str(len(expected))),
            call("Cache-Control", "no-store"),
        ]
        handler.end_headers.assert_called_once()
        assert handler.wfile.getvalue() == expected

    def test_sends_json_default_status(self, handler_cls):
        handler = _build_handler(handler_cls)
        handler._send_json({"ok": True})
        handler.send_response.assert_called_once_with(200)

    def test_sends_bytes(self, handler_cls):
        handler = _build_handler(handler_cls)
        data = b"some binary data"
        handler._send_bytes(data, "application/octet-stream")

        handler.send_response.assert_called_once_with(200)
        assert handler.send_header.call_args_list == [
            call("Content-Type", "application/octet-stream"),
            call("Content-Length", str(len(data))),
            call("Cache-Control", "no-store"),
        ]
        handler.end_headers.assert_called_once()
        assert handler.wfile.getvalue() == data


class TestSendStatic:
    def test_path_traversal_forbidden(self, handler_cls):
        handler = _build_handler(handler_cls)
        handler._send_static("../../etc/passwd")
        handler.send_error.assert_called_once_with(HTTPStatus.FORBIDDEN)

    def test_not_found(self, handler_cls):
        handler = _build_handler(handler_cls)
        handler._send_static("nonexistent_file.html")
        handler.send_error.assert_called_once_with(HTTPStatus.NOT_FOUND)

    def test_serves_html_with_correct_type(self, handler_cls):
        handler = _build_handler(handler_cls)
        target = STATIC_DIR / "index.html"
        if not target.is_file():
            pytest.skip("index.html not found")
        with patch.object(handler, "_send_bytes") as mock_send:
            handler._send_static("index.html")
        data = target.read_bytes()
        mock_send.assert_called_once_with(data, "text/html; charset=utf-8")

    def test_serves_js_with_correct_type(self, handler_cls):
        handler = _build_handler(handler_cls)
        target = STATIC_DIR / "app.js"
        if not target.is_file():
            pytest.skip("app.js not found")
        with patch.object(handler, "_send_bytes") as mock_send:
            handler._send_static("app.js")
        data = target.read_bytes()
        mock_send.assert_called_once_with(data, "application/javascript; charset=utf-8")

    def test_serves_css_with_correct_type(self, handler_cls):
        handler = _build_handler(handler_cls)
        target = STATIC_DIR / "style.css"
        if not target.is_file():
            pytest.skip("style.css not found")
        with patch.object(handler, "_send_bytes") as mock_send:
            handler._send_static("style.css")
        data = target.read_bytes()
        mock_send.assert_called_once_with(data, "text/css; charset=utf-8")


# ===========================================================================
# _get_state
# ===========================================================================


class TestGetState:
    def test_creates_new_state(self, handler_cls):
        handler = _build_handler(handler_cls)
        state = handler._get_state("project_x")
        assert isinstance(state, _ServerState)
        assert "project_x" in handler_cls._project_states

    def test_returns_same_instance(self, handler_cls):
        handler = _build_handler(handler_cls)
        state1 = handler._get_state("project_x")
        state2 = handler._get_state("project_x")
        assert state1 is state2


# ===========================================================================
# _get_config / _resolve_project_input / _get_project_output
# ===========================================================================


class TestDelegation:
    def test_get_config_delegates_to_cache(self, handler_cls):
        handler = _build_handler(handler_cls)
        handler._get_config()
        handler_cls._config_cache.get.assert_called_once_with(None)

    def test_get_config_with_project(self, handler_cls):
        handler = _build_handler(handler_cls)
        handler._get_config(Path("/some/proj"))
        handler_cls._config_cache.get.assert_called_once_with(Path("/some/proj"))

    @patch("clio.ui.server.resolve_project_dir")
    def test_resolve_project_dir(self, mock_rpi, handler_cls):
        mock_rpi.return_value = Path("/resolved")
        handler = _build_handler(handler_cls)
        result = handler._resolve_project_dir({"input_dir": ["/path"]})
        assert result == Path("/resolved")
        mock_rpi.assert_called_once()

    @patch("clio.ui.server.resolve_project_dir")
    @patch("clio.ui.server._project_output_dir")
    def test_get_project_output_from_dict(self, mock_pod, mock_rpi, handler_cls):
        mock_rpi.return_value = Path("/resolved/input")
        mock_pod.return_value = Path("/resolved/output")
        handler = _build_handler(handler_cls)
        result = handler._get_project_output({"project": ["test"]})
        assert result == Path("/resolved/output")
        mock_rpi.assert_called_once()
        mock_pod.assert_called_once_with(Path("/resolved/input"))

    @patch("clio.ui.server._project_output_dir")
    def test_get_project_output_from_path(self, mock_pod, handler_cls):
        mock_pod.return_value = Path("/resolved/output")
        handler = _build_handler(handler_cls)
        result = handler._get_project_output(Path("/my/proj"))
        assert result == Path("/resolved/output")
        mock_pod.assert_called_once_with(Path("/my/proj"))

    def test_invoke_handler_returns_result(self, handler_cls):
        handler = _build_handler(handler_cls)
        result = handler._invoke_handler(lambda h, qs: "ok", {"a": ["1"]})
        assert result == "ok"

    def test_invoke_handler_maps_resolution_error(self, handler_cls):
        from clio.ui.services.project_service import ProjectResolutionError

        handler = _build_handler(handler_cls)

        def boom(h, qs):
            raise ProjectResolutionError(404, "unknown project name: nope")

        with patch.object(handler, "_send_json") as mock_send:
            handler._invoke_handler(boom, {"a": ["1"]})
        mock_send.assert_called_once()
        payload, status = mock_send.call_args[0]
        assert status == 404
        assert payload == {"ok": False, "error": "unknown project name: nope"}


# ===========================================================================
# do_GET routing
# ===========================================================================


class TestDoGET:
    @patch("clio.ui.server.handle_index")
    def test_root(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler)

    @patch("clio.ui.server.handle_index")
    def test_index_html(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/index.html")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler)

    @patch("clio.ui.server.handle_favicon")
    def test_favicon(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/favicon.ico")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler)

    @patch("clio.ui.server.handle_static")
    def test_static(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/static/app.js")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, "app.js")

    @patch("clio.ui.server.handle_get_config")
    def test_api_config(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/config?input_dir=/x")
        handler.do_GET()
        mock_fn.assert_called_once()
        args = mock_fn.call_args
        assert args[0][0] is handler
        assert "input_dir" in args[0][1]

    @patch("clio.ui.server.handle_get_config_raw")
    def test_api_config_raw(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/config/raw")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_project")
    def test_api_project(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/project")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_projects")
    def test_api_projects(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/projects")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_videos")
    def test_api_videos(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/videos")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_video")
    def test_api_video(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/video")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_video")
    def test_api_video_head(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/video")
        handler.command = "HEAD"
        handler.do_HEAD()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_vmeta")
    def test_api_vmeta(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/vmeta/001_test")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, {}, stem="001_test")

    @patch("clio.ui.server.handle_get_texts")
    def test_api_texts(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/texts")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_voiceover")
    def test_api_voiceover(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/voiceover")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_plans")
    def test_api_plans(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/plans")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_tasks")
    def test_api_tasks(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/tasks")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, {})

    @patch("clio.ui.server.handle_get_tasks_stream")
    def test_api_tasks_stream(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/tasks/stream")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, {})

    @patch("clio.ui.server.handle_get_task")
    def test_api_task_detail(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/tasks/task-1")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, {}, task_id="task-1")

    @patch("clio.ui.server.handle_get_plan")
    def test_api_plan(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/plan")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_processing_state")
    def test_api_processing_state(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/processing-state")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_fs_dirs")
    def test_api_fs_dirs(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/fs/dirs")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_fs_entries")
    def test_api_fs_entries(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/fs/entries?kind=video")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_transcripts")
    def test_api_transcripts(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/transcripts")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_whisper_check")
    def test_api_whisper_check(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/whisper/check")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, {})

    @patch("clio.ui.server.handle_get_whisper_install_status")
    def test_api_whisper_install_status(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/whisper/install/status")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, {})

    @patch("clio.ui.server.handle_get_whisper_models")
    def test_api_whisper_models(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/whisper/models")
        handler.do_GET()
        mock_fn.assert_called_once_with(handler, {})

    @patch("clio.ui.server.handle_get_token_usage")
    def test_api_token_usage(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/token-usage")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_env")
    def test_api_env(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/env")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_prompts")
    def test_api_prompts(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/prompts")
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.read_session_log")
    def test_api_logs(self, mock_read, handler_cls):
        mock_read.return_value = {"logs": []}
        handler = _build_handler(handler_cls, path="/api/logs?offset=5")
        handler.do_GET()
        mock_read.assert_called_once_with(5)
        handler.send_response.assert_called_once_with(200)

    def test_not_found(self, handler_cls):
        handler = _build_handler(handler_cls, path="/api/unknown")
        handler.do_GET()
        handler.send_error.assert_called_once_with(HTTPStatus.NOT_FOUND)


# ===========================================================================
# do_PUT routing
# ===========================================================================


class TestDoPUT:
    def _put_handler(self, cls, body: dict, path="/api/unknown"):
        handler = _build_handler(cls, path=path, method="PUT")
        raw = json.dumps(body).encode("utf-8")
        handler.headers.get.return_value = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        return handler

    def test_invalid_json_returns_400(self, handler_cls):
        handler = _build_handler(handler_cls, path="/api/project", method="PUT")
        handler.headers.get.return_value = "6"
        handler.rfile = io.BytesIO(b"not{}ok")
        handler.do_PUT()
        handler.send_response.assert_called_once_with(400)
        data = json.loads(handler.wfile.getvalue())
        assert data["ok"] is False

    def test_invalid_non_dict_returns_400(self, handler_cls):
        handler = _build_handler(handler_cls, path="/api/project", method="PUT")
        raw = json.dumps([1, 2, 3]).encode("utf-8")
        handler.headers.get.return_value = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        handler.do_PUT()
        handler.send_response.assert_called_once_with(400)

    @patch("clio.ui.server.handle_put_config_raw")
    def test_put_config_raw(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {"key": "val"}, "/api/config/raw")
        handler.do_PUT()
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0] is handler

    @patch("clio.ui.server.handle_put_project")
    def test_put_project(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {"name": "test"}, "/api/project")
        handler.do_PUT()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_put_texts")
    def test_put_texts(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {}, "/api/texts")
        handler.do_PUT()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_put_voiceover")
    def test_put_voiceover(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {}, "/api/voiceover")
        handler.do_PUT()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_put_plan")
    def test_put_plan(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {}, "/api/plan")
        handler.do_PUT()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_put_transcripts")
    def test_put_transcripts(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {}, "/api/transcripts")
        handler.do_PUT()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_put_whisper_model")
    def test_put_whisper_model(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {}, "/api/whisper/model")
        handler.do_PUT()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_put_env")
    def test_put_env(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {}, "/api/env")
        handler.do_PUT()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_put_prompt")
    def test_put_prompt(self, mock_fn, handler_cls):
        handler = self._put_handler(handler_cls, {"content": "prompt"}, "/api/prompts/ANALYZE_PROMPT")
        handler.do_PUT()
        mock_fn.assert_called_once_with(handler, {}, {"content": "prompt"}, name="ANALYZE_PROMPT")

    def test_put_unknown(self, handler_cls):
        handler = self._put_handler(handler_cls, {}, "/api/nope")
        handler.do_PUT()
        handler.send_response.assert_called_once_with(404)
        data = json.loads(handler.wfile.getvalue())
        assert data["ok"] is False
        assert data["error"] == "unknown endpoint"

    def test_put_empty_body(self, handler_cls):
        """PUT with empty body (Content-Length: 0) returns 400 (not valid JSON dict)."""
        handler = _build_handler(handler_cls, path="/api/project", method="PUT")
        handler.headers.get.return_value = "0"
        handler.rfile = io.BytesIO(b"")
        handler.do_PUT()
        handler.send_response.assert_called_once_with(400)


# ===========================================================================
# do_DELETE routing
# ===========================================================================


class TestDoDELETE:
    @patch("clio.ui.server.handle_delete_prompt")
    def test_delete_prompt(self, mock_fn, handler_cls):
        handler = _build_handler(handler_cls, path="/api/prompts/ANALYZE_PROMPT", method="DELETE")
        handler.do_DELETE()
        mock_fn.assert_called_once_with(handler, {}, name="ANALYZE_PROMPT")

    def test_delete_unknown(self, handler_cls):
        handler = _build_handler(handler_cls, path="/api/nope", method="DELETE")
        handler.do_DELETE()
        handler.send_response.assert_called_once_with(404)
        data = json.loads(handler.wfile.getvalue())
        assert data["ok"] is False
        assert data["error"] == "unknown endpoint"


# ===========================================================================
# desktop focus callback registry
# ===========================================================================


class TestDesktopFocusCallback:
    def test_no_callback_returns_503(self, handler_cls, monkeypatch):
        from clio.ui.server import set_desktop_focus_callback

        monkeypatch.setattr("clio.ui.server._desktop_focus_callback", None)
        set_desktop_focus_callback(None)
        handler = _build_handler(handler_cls, path="/api/desktop/focus", method="POST")
        from clio.ui.server import handle_post_desktop_focus

        handle_post_desktop_focus(handler, {}, {})
        handler.send_response.assert_called_once_with(503)
        data = json.loads(handler.wfile.getvalue())
        assert data["ok"] is False

    def test_callback_called_and_ok(self, handler_cls, monkeypatch):
        from clio.ui.server import set_desktop_focus_callback

        calls = []
        set_desktop_focus_callback(lambda: calls.append("focused"))
        try:
            handler = _build_handler(handler_cls, path="/api/desktop/focus", method="POST")
            from clio.ui.server import handle_post_desktop_focus

            handle_post_desktop_focus(handler, {}, {})
            assert calls == ["focused"]
            assert handler.send_response.call_args.args[0] == 200
            data = json.loads(handler.wfile.getvalue())
            assert data["ok"] is True
        finally:
            set_desktop_focus_callback(None)

    def test_callback_exception_returns_500(self, handler_cls, monkeypatch):
        from clio.ui.server import set_desktop_focus_callback

        def _boom():
            raise RuntimeError("window not ready")

        set_desktop_focus_callback(_boom)
        try:
            handler = _build_handler(handler_cls, path="/api/desktop/focus", method="POST")
            from clio.ui.server import handle_post_desktop_focus

            handle_post_desktop_focus(handler, {}, {})
            assert handler.send_response.call_args.args[0] == 500
            data = json.loads(handler.wfile.getvalue())
            assert data["ok"] is False
        finally:
            set_desktop_focus_callback(None)


# ===========================================================================
# do_POST routing
# ===========================================================================


class TestDoPOST:
    def _post_handler(self, cls, body: dict, path="/api/unknown"):
        handler = _build_handler(cls, path=path, method="POST")
        raw = json.dumps(body).encode("utf-8")
        handler.headers.get.return_value = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        return handler

    def test_invalid_json_returns_400(self, handler_cls):
        handler = _build_handler(handler_cls, path="/api/run/start", method="POST")
        handler.headers.get.return_value = "5"
        handler.rfile = io.BytesIO(b"bad!!")
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)

    @patch("clio.ui.server.handle_post_run_start")
    def test_post_run_start(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/run/start")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_run_start")
    def test_post_webhook_trigger(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/webhook/trigger")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_run_preview")
    def test_post_run_preview(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/run/preview")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_ai_test")
    def test_post_ai_test(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {"provider": "deepseek"}, "/api/ai/test")
        handler.do_POST()
        mock_fn.assert_called_once()
        assert mock_fn.call_args[0][0] is handler
        assert mock_fn.call_args[1]["qs"] == {}
        assert mock_fn.call_args[1]["obj"] == {"provider": "deepseek"}

    @patch("clio.ui.server.handle_post_task_cancel")
    def test_post_task_cancel(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/tasks/task-1/cancel")
        handler.do_POST()
        mock_fn.assert_called_once_with(handler, qs={}, obj={}, task_id="task-1")

    @patch("clio.ui.server.handle_post_task_retry")
    def test_post_task_retry(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/tasks/task-1/retry")
        handler.do_POST()
        mock_fn.assert_called_once_with(handler, qs={}, obj={}, task_id="task-1")

    @patch("clio.ui.server.handle_post_config_init")
    def test_post_config_init(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/config/init")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_cut")
    def test_post_cut(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/cut")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_refine")
    def test_post_refine(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/refine")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_export")
    def test_post_export(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/export")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_project_create")
    def test_post_project_create(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/project/create")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_project_add")
    def test_post_project_add(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/project/add")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_project_remove")
    def test_post_project_remove(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/project/remove")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_rerun")
    def test_post_rerun(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/rerun")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_transcripts")
    def test_post_transcripts(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/transcripts")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_post_whisper_install")
    def test_post_whisper_install(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/whisper/install")
        handler.do_POST()
        mock_fn.assert_called_once_with(handler, qs={})

    @patch("clio.ui.server.handle_post_whisper_install_cancel")
    def test_post_whisper_install_cancel(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/whisper/install/cancel")
        handler.do_POST()
        mock_fn.assert_called_once_with(handler, qs={})

    @patch("clio.ui.server.handle_post_whisper_model_delete")
    def test_post_whisper_model_delete(self, mock_fn, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/whisper/models/delete")
        handler.do_POST()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.clear_session_log")
    def test_post_logs_clear(self, mock_clear, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/logs/clear")
        handler.do_POST()
        mock_clear.assert_called_once()
        handler.send_response.assert_called_once_with(200)

    def test_post_unknown(self, handler_cls):
        handler = self._post_handler(handler_cls, {}, "/api/unknown/route")
        handler.do_POST()
        handler.send_response.assert_called_once_with(404)

    def test_post_empty_body_non_dict(self, handler_cls):
        """POST with a valid JSON body that is not a dict returns 400."""
        handler = _build_handler(handler_cls, path="/api/run/start", method="POST")
        raw = json.dumps("string_body").encode("utf-8")
        handler.headers.get.return_value = str(len(raw))
        handler.rfile = io.BytesIO(raw)
        handler.do_POST()
        handler.send_response.assert_called_once_with(400)


# ===========================================================================
# Auth tests
# ===========================================================================


class TestAuth:
    """Token-based auth for sensitive routes.

    Default handler_cls has _api_token="" (no auth).
    Tests that need auth use auth_cls fixture with _api_token="test-token-789".
    """

    @pytest.fixture
    def auth_cls(self, handler_cls):
        handler_cls._api_token = "test-token-789"
        yield handler_cls
        handler_cls._api_token = ""

    # --- GET route auth ---

    def test_sensitive_get_returns_401_without_token(self, auth_cls):
        """GET /api/env without token returns 401."""
        handler = _build_handler(auth_cls, path="/api/env")
        handler.do_GET()
        handler.send_response.assert_called_once_with(401)

    @patch("clio.ui.server.handle_get_env")
    def test_sensitive_get_works_with_bearer_token(self, mock_fn, auth_cls):
        """GET /api/env with valid Bearer token works."""
        handler = _build_handler(auth_cls, path="/api/env")
        handler.headers.get.side_effect = lambda k, d=None: "Bearer test-token-789" if k == "Authorization" else "0"
        handler.do_GET()
        mock_fn.assert_called_once()

    @patch("clio.ui.server.handle_get_env")
    def test_sensitive_get_works_with_query_token(self, mock_fn, auth_cls):
        """GET /api/env with ?token= in URL works."""
        handler = _build_handler(auth_cls, path="/api/env?token=test-token-789")
        handler.do_GET()
        mock_fn.assert_called_once()

    def test_sensitive_get_config_raw_returns_401_without_token(self, auth_cls):
        """GET /api/config/raw without token returns 401."""
        handler = _build_handler(auth_cls, path="/api/config/raw")
        handler.do_GET()
        handler.send_response.assert_called_once_with(401)

    @patch("clio.ui.server.handle_get_fs_dirs")
    def test_sensitive_get_fs_dirs_returns_401_without_token(self, mock_fn, auth_cls):
        """GET /api/fs/dirs without token returns 401."""
        handler = _build_handler(auth_cls, path="/api/fs/dirs")
        handler.do_GET()
        handler.send_response.assert_called_once_with(401)

    @patch("clio.ui.server.handle_get_fs_entries")
    def test_sensitive_get_fs_entries_returns_401_without_token(self, mock_fn, auth_cls):
        handler = _build_handler(auth_cls, path="/api/fs/entries")
        handler.do_GET()
        handler.send_response.assert_called_once_with(401)

    @patch("clio.ui.server.handle_get_video")
    def test_sensitive_get_video_returns_401_without_token(self, mock_fn, auth_cls):
        """GET /api/video without token returns 401."""
        handler = _build_handler(auth_cls, path="/api/video?file=test.mp4")
        handler.do_GET()
        handler.send_response.assert_called_once_with(401)

    @patch("clio.ui.server.handle_get_video")
    def test_video_works_with_query_token(self, mock_fn, auth_cls):
        """GET /api/video with ?token= works."""
        handler = _build_handler(auth_cls, path="/api/video?file=test.mp4&token=test-token-789")
        handler.do_GET()
        mock_fn.assert_called_once()

    def test_api_config_returns_401_without_token(self, auth_cls):
        """GET /api/config returns 401 in token mode."""
        handler = _build_handler(auth_cls, path="/api/config")
        with patch("clio.ui.server.handle_get_config") as mock_fn:
            handler.do_GET()
            handler.send_response.assert_called_once_with(401)
            mock_fn.assert_not_called()

    @patch("clio.ui.server.handle_get_config")
    def test_api_config_works_with_bearer_token(self, mock_fn, auth_cls):
        """GET /api/config with valid Bearer token works."""
        handler = _build_handler(auth_cls, path="/api/config")
        handler.headers.get.side_effect = lambda k, d=None: "Bearer test-token-789" if k == "Authorization" else "0"
        handler.do_GET()
        mock_fn.assert_called_once()

    # --- PUT route auth ---

    def test_put_returns_401_without_token(self, auth_cls):
        """PUT without token returns 401."""
        handler = _build_handler(auth_cls, path="/api/project", method="PUT")
        handler.headers.get.return_value = "2"
        handler.rfile = io.BytesIO(b"{}")
        handler.do_PUT()
        handler.send_response.assert_called_once_with(401)

    @patch("clio.ui.server.handle_put_project")
    def test_put_works_with_bearer_token(self, mock_fn, auth_cls):
        """PUT with valid Bearer token works."""
        handler = _build_handler(auth_cls, path="/api/project", method="PUT")
        handler.headers = MagicMock()
        handler.headers.get.side_effect = lambda k, d=None: {
            "Authorization": "Bearer test-token-789",
            "Content-Length": "2",
        }.get(k, d)
        handler.rfile = io.BytesIO(b"{}")
        handler.do_PUT()
        mock_fn.assert_called_once()

    # --- POST route auth ---

    def test_post_returns_401_without_token(self, auth_cls):
        """POST without token returns 401."""
        handler = _build_handler(auth_cls, path="/api/run/start", method="POST")
        handler.headers.get.return_value = "2"
        handler.rfile = io.BytesIO(b"{}")
        handler.do_POST()
        handler.send_response.assert_called_once_with(401)

    # --- No auth needed for default (empty token) ---

    def test_unprotected_default_no_auth(self, handler_cls):
        """Default handler (empty token) works without auth."""
        with patch("clio.ui.server.handle_get_env") as mock_fn:
            handler = _build_handler(handler_cls, path="/api/env")
            handler.do_GET()
            mock_fn.assert_called_once()

    # --- POST /api/desktop/focus (single-instance, must NOT require auth) ---

    def test_focus_unauthenticated_without_token(self, handler_cls):
        """POST /api/desktop/focus works with no token configured."""
        with patch("clio.ui.server.handle_post_desktop_focus") as mock_fn:
            handler = _build_handler(handler_cls, path="/api/desktop/focus", method="POST")
            handler.headers.get.return_value = "2"
            handler.rfile = io.BytesIO(b"{}")
            handler.do_POST()
            mock_fn.assert_called_once()

    def test_focus_unauthenticated_even_with_token(self, auth_cls):
        """POST /api/desktop/focus is reachable even when a token is configured."""
        with patch("clio.ui.server.handle_post_desktop_focus") as mock_fn:
            handler = _build_handler(auth_cls, path="/api/desktop/focus", method="POST")
            handler.headers.get.return_value = "2"
            handler.rfile = io.BytesIO(b"{}")
            handler.do_POST()
            mock_fn.assert_called_once()


class TestLocalSession:
    """Desktop loopback session gate (Host/Origin) + JSON content-type enforcement.

    The gate is off by default in ``make_handler`` (legacy ``serve`` tests) and is
    enabled by the desktop ``start_server`` and by ``run()`` on loopback binds.
    """

    @pytest.fixture
    def enforce_cls(self, handler_cls):
        handler_cls._enforce_local_session = True
        handler_cls._enforce_json_ct = True
        handler_cls._allowed_hosts = ("127.0.0.1", "localhost", "::1")
        yield handler_cls
        handler_cls._enforce_local_session = False
        handler_cls._enforce_json_ct = False
        handler_cls._allowed_hosts = ()

    def test_loopback_host_accepts_api(self, enforce_cls):
        """Host: 127.0.0.1:<port> is accepted for API GET."""
        with patch("clio.ui.server.handle_get_env") as mock_fn:
            handler = _build_handler(enforce_cls, path="/api/env")
            handler.headers.get.side_effect = lambda k, d=None: "127.0.0.1:8765" if k == "Host" else "0"
            handler.do_GET()
            mock_fn.assert_called_once()

    def test_localhost_host_accepted(self, enforce_cls):
        with patch("clio.ui.server.handle_get_env") as mock_fn:
            handler = _build_handler(enforce_cls, path="/api/env")
            handler.headers.get.side_effect = lambda k, d=None: "localhost:8765" if k == "Host" else "0"
            handler.do_GET()
            mock_fn.assert_called_once()

    def test_foreign_host_rejected_403(self, enforce_cls):
        """Host: evil.example.com is rejected even with a valid token."""
        handler = _build_handler(enforce_cls, path="/api/env?token=test-token-789")
        handler.headers.get.side_effect = lambda k, d=None: "evil.example.com" if k == "Host" else "0"
        handler.do_GET()
        handler.send_response.assert_called_once_with(403)

    def test_foreign_origin_rejected_403(self, enforce_cls):
        """Origin: https://evil.example.com is rejected."""
        handler = _build_handler(enforce_cls, path="/api/env?token=test-token-789")
        handler.headers.get.side_effect = lambda k, d=None: (
            "127.0.0.1:8765" if k == "Host" else "https://evil.example.com" if k == "Origin" else "0"
        )
        handler.do_GET()
        handler.send_response.assert_called_once_with(403)

    def test_loopback_origin_accepted(self, enforce_cls):
        with patch("clio.ui.server.handle_get_env") as mock_fn:
            handler = _build_handler(enforce_cls, path="/api/env")
            handler.headers.get.side_effect = lambda k, d=None: (
                "127.0.0.1:8765" if k == "Host" else "http://127.0.0.1:8765" if k == "Origin" else "0"
            )
            handler.do_GET()
            mock_fn.assert_called_once()

    def test_empty_host_accepted(self, enforce_cls):
        """An absent Host header (rare in tests/HTTP1.0) must not be fatal."""
        with patch("clio.ui.server.handle_get_env") as mock_fn:
            handler = _build_handler(enforce_cls, path="/api/env")
            handler.headers.get.side_effect = lambda k, d=None: "" if k == "Host" else "0"
            handler.do_GET()
            mock_fn.assert_called_once()

    def test_wrong_content_type_rejected_415(self, enforce_cls):
        """PUT/POST with a non-JSON Content-Type gets 415."""
        handler = _build_handler(enforce_cls, path="/api/plan", method="PUT")
        handler.headers.get.side_effect = lambda k, d=None: (
            "127.0.0.1:8765" if k == "Host" else "text/plain" if k == "Content-Type" else "2"
        )
        handler.do_PUT()
        handler.send_response.assert_called_once_with(415)

    def test_json_content_type_accepted(self, enforce_cls):
        body = b'{"video": "x"}'
        with patch("clio.ui.server.handle_put_plan") as mock_fn:
            handler = _build_handler(enforce_cls, path="/api/plan", method="PUT")
            handler.headers.get.side_effect = lambda k, d=None: (
                "127.0.0.1:8765"
                if k == "Host"
                else "application/json; charset=utf-8"
                if k == "Content-Type"
                else str(len(body))
            )
            handler.rfile = io.BytesIO(body)
            handler.do_PUT()
            mock_fn.assert_called_once()

    def test_gate_off_by_default_allows_any_host(self, handler_cls):
        """Default handlers (no desktop session) accept any Host the token passes."""
        with patch("clio.ui.server.handle_get_env") as mock_fn:
            handler = _build_handler(handler_cls, path="/api/env")
            handler.headers.get.side_effect = lambda k, d=None: "whatever.example.com" if k == "Host" else "0"
            handler.do_GET()
            mock_fn.assert_called_once()


class TestParseJsonContentLength:
    def test_missing_is_zero(self):
        assert _parse_json_content_length(None) == (0, None)
        assert _parse_json_content_length("") == (0, None)

    def test_valid(self):
        assert _parse_json_content_length("12") == (12, None)

    def test_invalid(self):
        assert _parse_json_content_length("abc") == (None, 400)
        assert _parse_json_content_length("-1") == (None, 400)

    def test_too_large(self):
        assert _parse_json_content_length(str(MAX_JSON_BODY_BYTES + 1)) == (None, 413)
        assert _parse_json_content_length(str(MAX_JSON_BODY_BYTES)) == (MAX_JSON_BODY_BYTES, None)

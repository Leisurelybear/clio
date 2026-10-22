"""Tests for clio/ui/routes/refine.py — POST /api/refine handler."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.routes.refine import _is_file_busy, _mark_busy, handle_post_refine


@pytest.fixture(autouse=True)
def _clear_busy_set():
    """Ensure _refining set is clean before and after each test."""
    yield
    from clio.ui.routes.refine import _refining

    _refining.clear()


def _mock_path(resolved: str = "/resolved/path.json", content: str | None = None):
    """Create a MagicMock that looks like a Path for the refine route handler."""
    p = MagicMock(spec=Path)
    p.resolve.return_value = Path(resolved)
    p.read_text.return_value = content or '{"key": "value"}'
    return p


def _make_handler(p=None, with_config=True):
    """Build a minimal magicmock handler for refine tests."""
    handler = MagicMock()
    handler._resolve_project_dir.return_value = Path("/proj")
    handler._get_project_output.return_value = Path("/proj_out")
    handler._resolve_texts.return_value = p
    handler._resolve_in.return_value = p
    if with_config:
        handler._get_config.return_value = MagicMock()
    return handler


# ===========================================================================
# _is_file_busy / _mark_busy
# ===========================================================================


class TestFileBusy:
    def test_not_busy_initially(self):
        assert _is_file_busy("/some/path.json") is False

    def test_mark_busy_true(self):
        _mark_busy("/path/to/file.json", True)
        assert _is_file_busy("/path/to/file.json") is True

    def test_mark_busy_false(self):
        _mark_busy("/path/to/file.json", True)
        _mark_busy("/path/to/file.json", False)
        assert _is_file_busy("/path/to/file.json") is False

    def test_independent_paths(self):
        _mark_busy("/path/a.json", True)
        assert _is_file_busy("/path/b.json") is False
        assert _is_file_busy("/path/a.json") is True

    def test_discard_not_raise(self):
        _mark_busy("/not/marked.json", False)
        assert _is_file_busy("/not/marked.json") is False


# ===========================================================================
# handle_post_refine — validation
# ===========================================================================


class TestHandlePostRefineValidation:
    def test_missing_file(self):
        handler = MagicMock()
        handle_post_refine(handler, {}, {"type": "texts"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing or invalid file/type"}, 400)

    def test_empty_file(self):
        handler = MagicMock()
        handle_post_refine(handler, {}, {"file": "", "type": "texts"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing or invalid file/type"}, 400)

    def test_missing_type(self):
        handler = MagicMock()
        handle_post_refine(handler, {}, {"file": "test.json"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing or invalid file/type"}, 400)

    def test_invalid_type(self):
        handler = MagicMock()
        handle_post_refine(handler, {}, {"file": "test.json", "type": "videos"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing or invalid file/type"}, 400)

    def test_empty_body(self):
        handler = MagicMock()
        handle_post_refine(handler, {}, {})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing or invalid file/type"}, 400)

    def test_non_string_file_returns_400(self):
        handler = MagicMock()
        handle_post_refine(handler, {}, {"file": 123, "type": "texts"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing or invalid file/type"}, 400)

    def test_non_string_context_returns_400(self):
        handler = MagicMock()
        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts", "context": 1})
        handler._send_json.assert_called_once_with({"ok": False, "error": "context must be a string"}, 400)


# ===========================================================================
# handle_post_refine — file not found / forbidden
# ===========================================================================


class TestHandlePostRefineFileNotFound:
    def test_texts_file_not_found(self):
        handler = _make_handler(p=None, with_config=False)
        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "forbidden or not found"}, 404)

    def test_scripts_file_not_found(self):
        handler = _make_handler(p=None, with_config=False)
        handler._resolve_in.return_value = None
        handle_post_refine(handler, {}, {"file": "test.json", "type": "scripts"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "forbidden or not found"}, 404)


# ===========================================================================
# handle_post_refine — busy file
# ===========================================================================


class TestHandlePostRefineBusy:
    def test_busy_texts_file_returns_409(self, tmp_path):
        fpath = tmp_path / "busy_file.json"
        abs_path = str(fpath.resolve())
        p = MagicMock(spec=Path)
        p.resolve.return_value = fpath.resolve()
        p.read_text.return_value = '{"key": "value"}'
        _mark_busy(abs_path, True)

        try:
            handler = MagicMock()
            handler._resolve_project_dir.return_value = Path("/proj")
            handler._get_project_output.return_value = Path("/proj_out")
            handler._resolve_texts.return_value = p
            handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})
            handler._send_json.assert_called_once_with({"ok": False, "error": "该文件正在 AI 审阅中，请等待完成"}, 409)
        finally:
            _mark_busy(abs_path, False)

    def test_busy_scripts_file_returns_409(self, tmp_path):
        fpath = tmp_path / "voiceover.json"
        abs_path = str(fpath.resolve())
        p = MagicMock(spec=Path)
        p.resolve.return_value = fpath.resolve()
        p.read_text.return_value = '{"key": "value"}'
        _mark_busy(abs_path, True)

        try:
            handler = MagicMock()
            handler._resolve_project_dir.return_value = Path("/proj")
            handler._get_project_output.return_value = Path("/proj_out")
            handler._resolve_in.return_value = p
            handle_post_refine(handler, {}, {"file": "test.json", "type": "scripts"})
            handler._send_json.assert_called_once_with({"ok": False, "error": "该文件正在 AI 审阅中，请等待完成"}, 409)
        finally:
            _mark_busy(abs_path, False)


# ===========================================================================
# handle_post_refine — file read error
# ===========================================================================


class TestHandlePostRefineReadError:
    def test_json_decode_error_returns_500(self):
        p = _mock_path(content="invalid json")
        handler = _make_handler(p, with_config=False)
        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 500
        assert "failed to read file" in args[0][0]["error"]

    def test_oserror_returns_500(self):
        p = MagicMock(spec=Path)
        p.resolve.return_value = Path("/some/path.json")
        p.read_text.side_effect = OSError("permission denied")
        handler = _make_handler(p, with_config=False)
        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 500
        assert "failed to read file" in args[0][0]["error"]


# ===========================================================================
# handle_post_refine — success path: texts
# ===========================================================================


class TestHandlePostRefineTextsSuccess:
    @patch("clio.ui.routes.refine.refine_text")
    @patch("clio.ui.routes.refine._save_atomic")
    def test_refine_texts_success(self, mock_save, mock_refine):
        refined_data = {"index": 1, "title": "fixed", "_changelog": ["fixed title"]}
        mock_refine.return_value = refined_data

        p = _mock_path(content='{"index": 1, "title": "original"}')
        handler = _make_handler(p)

        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})

        mock_refine.assert_called_once()
        args, kwargs = mock_refine.call_args
        assert args[0] == {"index": 1, "title": "original"}
        assert kwargs.get("context_override") is None

        mock_save.assert_called_once()
        save_path, save_data = mock_save.call_args[0]
        assert save_path is p
        assert json.loads(save_data) == refined_data
        handler._send_json.assert_called_once_with({"ok": True, "data": refined_data})

    @patch("clio.ui.routes.refine.refine_text")
    @patch("clio.ui.routes.refine._save_atomic")
    def test_refine_texts_with_context(self, mock_save, mock_refine):
        mock_refine.return_value = {"index": 1, "_changelog": []}

        p = _mock_path(content='{"index": 1}')
        handler = _make_handler(p)
        handle_post_refine(
            handler,
            {},
            {"file": "test.json", "type": "texts", "context": "请改为更正式的语气"},
        )

        assert mock_refine.call_args.kwargs["context_override"] == "请改为更正式的语气"

    @patch("clio.ui.routes.refine.refine_text")
    @patch("clio.ui.routes.refine._save_atomic")
    def test_busy_cleared_after_success(self, mock_save, mock_refine):
        mock_refine.return_value = {"index": 1, "_changelog": []}

        resolved = "/some/path.json"
        p = _mock_path(resolved, content='{"index": 1}')
        abs_path = str(Path(resolved).resolve())
        assert _is_file_busy(abs_path) is False

        handler = _make_handler(p)
        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})

        assert _is_file_busy(abs_path) is False


# ===========================================================================
# handle_post_refine — success path: scripts
# ===========================================================================


class TestHandlePostRefineScriptsSuccess:
    @patch("clio.ui.routes.refine.refine_script")
    @patch("clio.ui.routes.refine._load_analysis_for_script")
    @patch("clio.ui.routes.refine._save_atomic")
    def test_refine_scripts_success(self, mock_save, mock_load_analysis, mock_refine):
        analysis = {"title": "test", "index": 1}
        mock_load_analysis.return_value = analysis
        refined = {"voiceover": "fixed script", "_changelog": []}
        mock_refine.return_value = refined

        p = _mock_path("/some/scripts/test_voiceover.json", content='{"voiceover": "original"}')
        handler = _make_handler(p)

        handle_post_refine(handler, {}, {"file": "test.json", "type": "scripts"})

        mock_load_analysis.assert_called_once_with(p, handler._get_config.return_value.texts_dir)
        mock_refine.assert_called_once()
        args = mock_refine.call_args
        assert args[0][0] == {"voiceover": "original"}
        assert args[0][1] == analysis

        mock_save.assert_called_once()
        handler._send_json.assert_called_once_with({"ok": True, "data": refined})


# ===========================================================================
# handle_post_refine — refine raises exception
# ===========================================================================


class TestHandlePostRefineError:
    @patch("clio.ui.routes.refine.refine_text")
    def test_refine_raises_exception(self, mock_refine):
        mock_refine.side_effect = RuntimeError("AI API timeout")

        p = _mock_path(content='{"index": 1}')
        handler = _make_handler(p)
        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})

        handler._send_json.assert_called_once_with({"ok": False, "error": "refine failed: AI API timeout"}, 500)

    @patch("clio.ui.routes.refine.refine_text")
    def test_busy_cleared_on_error(self, mock_refine):
        mock_refine.side_effect = ValueError("bad data")

        resolved = "/some/path.json"
        p = _mock_path(resolved, content='{"index": 1}')
        abs_path = str(Path(resolved).resolve())
        handler = _make_handler(p)

        handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})

        assert _is_file_busy(abs_path) is False

    @patch("clio.ui.routes.refine.refine_text")
    def test_file_save_skipped_on_error(self, mock_refine):
        mock_refine.side_effect = RuntimeError("fail")

        p = _mock_path(content='{"index": 1}')
        handler = _make_handler(p)

        with patch("clio.ui.routes.refine._save_atomic") as mock_save:
            handle_post_refine(handler, {}, {"file": "test.json", "type": "texts"})

        mock_save.assert_not_called()

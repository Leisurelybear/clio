from __future__ import annotations

import json
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def handler():
    h = MagicMock()
    h._get_project_output.return_value = "/output"
    return h


class TestHandleGetTexts:
    def test_not_found(self, handler):
        handler._resolve_texts.return_value = None
        from clio.ui.routes.texts import handle_get_texts

        handle_get_texts(handler, {"file": ["001.json"]})
        handler.send_error.assert_called_once_with(HTTPStatus.NOT_FOUND)

    def test_sends_file(self, handler):
        handler._resolve_texts.return_value = MagicMock()
        handler._resolve_texts.return_value.read_bytes.return_value = b'{"key": "val"}'
        from clio.ui.routes.texts import handle_get_texts

        handle_get_texts(handler, {"file": ["001.json"]})
        handler._send_bytes.assert_called_once()
        data = handler._send_bytes.call_args.args[0]
        assert json.loads(data) == {"key": "val"}

    def test_passes_query_string(self, handler):
        handler._resolve_texts.return_value = MagicMock()
        handler._resolve_texts.return_value.read_bytes.return_value = b"{}"
        from clio.ui.routes.texts import handle_get_texts

        handle_get_texts(handler, {"file": ["001.json"], "project": ["proj1"]})
        handler._resolve_texts.assert_called_once_with("001.json", "/output")


class TestHandleGetVoiceover:
    def test_not_found(self, handler):
        handler._resolve_in.return_value = None
        from clio.ui.routes.texts import handle_get_voiceover

        handle_get_voiceover(handler, {"file": ["001.json"]})
        handler.send_error.assert_called_once_with(HTTPStatus.NOT_FOUND)

    def test_sends_file(self, handler):
        handler._resolve_in.return_value = MagicMock()
        handler._resolve_in.return_value.read_bytes.return_value = b'{"voiceover": "hello"}'
        from clio.ui.routes.texts import handle_get_voiceover

        handle_get_voiceover(handler, {"file": ["001.json"]})
        handler._send_bytes.assert_called_once()
        data = handler._send_bytes.call_args.args[0]
        assert json.loads(data)["voiceover"] == "hello"

    def test_resolves_in_scripts_dir(self, handler):
        handler._resolve_in.return_value = MagicMock()
        handler._resolve_in.return_value.read_bytes.return_value = b"{}"
        from clio.ui.routes.texts import handle_get_voiceover

        handle_get_voiceover(handler, {"file": ["001.json"]})
        handler._resolve_in.assert_called_once_with("scripts", "001.json", "/output")


class TestHandlePutTexts:
    def test_forbidden(self, handler):
        handler._resolve_texts.return_value = None
        from clio.ui.routes.texts import handle_put_texts

        handle_put_texts(handler, {"file": ["001.json"]}, {"key": "val"})
        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args.args[0]
        assert resp["ok"] is False

    @patch("clio.ui.routes.texts._save_atomic")
    def test_saves_file(self, mock_save, handler):
        handler._resolve_texts.return_value = MagicMock()
        from clio.ui.routes.texts import handle_put_texts

        handle_put_texts(handler, {"file": ["001.json"]}, {"key": "val"})
        mock_save.assert_called_once()
        handler._send_json.assert_called_once_with({"ok": True, "path": str(handler._resolve_texts.return_value)})


class TestHandlePutVoiceover:
    def test_forbidden(self, handler):
        handler._resolve_in.return_value = None
        from clio.ui.routes.texts import handle_put_voiceover

        handle_put_voiceover(handler, {"file": ["001.json"]}, {"voiceover": "test"})
        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args.args[0]
        assert resp["ok"] is False

    @patch("clio.ui.routes.texts._save_atomic")
    def test_saves_file(self, mock_save, handler):
        handler._resolve_in.return_value = MagicMock()
        from clio.ui.routes.texts import handle_put_voiceover

        handle_put_voiceover(handler, {"file": ["001.json"]}, {"voiceover": "test"})
        mock_save.assert_called_once()
        handler._send_json.assert_called_once_with({"ok": True, "path": str(handler._resolve_in.return_value)})


class TestHandleGetCover:
    def test_rejects_non_image_extension(self, handler):
        from clio.ui.routes.texts import handle_get_cover

        handle_get_cover(handler, {"file": ["evil.html"]})
        handler._send_json.assert_called_once()
        payload, status = handler._send_json.call_args[0][0], handler._send_json.call_args[0][1]
        assert status == 403
        assert payload["ok"] is False
        handler._resolve_in.assert_not_called()

    def test_rejects_mismatched_magic_bytes(self, handler):
        from clio.ui.routes.texts import handle_get_cover

        path = MagicMock()
        path.name = "001.jpg"
        path.suffix = ".jpg"
        path.read_bytes.return_value = b"<script>alert(1)</script>"
        handler._resolve_in.return_value = path
        handle_get_cover(handler, {"file": ["001.jpg"]})
        payload, status = handler._send_json.call_args[0][0], handler._send_json.call_args[0][1]
        assert status == 403
        assert payload["ok"] is False

    def test_allows_chinese_cover_filename(self, handler):
        from clio.ui.routes.texts import handle_get_cover

        path = MagicMock()
        path.name = "028_巴黎蒙马特街头漫步.jpg"
        path.suffix = ".jpg"
        path.read_bytes.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        handler._resolve_in.return_value = path
        handle_get_cover(handler, {"file": ["028_巴黎蒙马特街头漫步.jpg"]})
        handler._resolve_in.assert_called_once()
        handler._send_bytes.assert_called_once()

    def test_rejects_name_with_dangerous_char(self, handler):
        from clio.ui.routes.texts import handle_get_cover

        handle_get_cover(handler, {"file": ["evil<>.jpg"]})
        status = handler._send_json.call_args[0][1]
        assert status == 403
        handler._resolve_in.assert_not_called()

    def test_sends_jpeg_with_security_headers(self, handler):
        from clio.ui.routes.texts import handle_get_cover

        path = MagicMock()
        path.name = "001.jpg"
        path.suffix = ".jpg"
        path.read_bytes.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        handler._resolve_in.return_value = path
        handle_get_cover(handler, {"file": ["001.jpg"]})
        args, kwargs = handler._send_bytes.call_args
        assert args[1].startswith("image/jpeg")
        headers = kwargs.get("extra_headers") or (args[2] if len(args) > 2 else {})
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" in headers
        assert "Content-Disposition" in headers

    def test_content_disposition_is_ascii_safe_for_chinese(self, handler):
        from clio.ui.routes.texts import handle_get_cover

        path = MagicMock()
        path.name = "026_巴黎蒙马特街头漫步.jpg"
        path.suffix = ".jpg"
        path.read_bytes.return_value = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        handler._resolve_in.return_value = path
        handle_get_cover(handler, {"file": ["026_巴黎蒙马特街头漫步.jpg"]})
        args, kwargs = handler._send_bytes.call_args
        headers = kwargs.get("extra_headers") or (args[2] if len(args) > 2 else {})
        cd = headers.get("Content-Disposition", "")
        assert cd
        cd.encode("latin-1")
        assert "filename*=UTF-8''" in cd or cd.isascii()

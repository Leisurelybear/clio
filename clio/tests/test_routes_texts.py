from __future__ import annotations

import json
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.services.file_service import ArtifactConflictError, version_headers


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

    @patch("clio.ui.routes.texts.read_with_version")
    def test_sends_file_with_version_header(self, mock_read, handler):
        version = "0123456789abcdef"
        handler._resolve_texts.return_value = MagicMock()
        mock_read.return_value = (b'{"key": "val"}', version)
        from clio.ui.routes.texts import handle_get_texts

        handle_get_texts(handler, {"file": ["001.json"]})

        handler._send_bytes.assert_called_once_with(
            b'{"key": "val"}',
            "application/json; charset=utf-8",
            extra_headers=version_headers(version),
        )

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

    @patch("clio.ui.routes.texts.read_with_version")
    def test_sends_file_with_version_header(self, mock_read, handler):
        version = "fedcba9876543210"
        handler._resolve_in.return_value = MagicMock()
        mock_read.return_value = (b'{"voiceover": "hello"}', version)
        from clio.ui.routes.texts import handle_get_voiceover

        handle_get_voiceover(handler, {"file": ["001.json"]})

        handler._send_bytes.assert_called_once_with(
            b'{"voiceover": "hello"}',
            "application/json; charset=utf-8",
            extra_headers=version_headers(version),
        )

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

    def test_saves_file_with_if_match(self, handler):
        path = handler._resolve_texts.return_value
        new_version = "1111111111111111"
        from clio.ui.routes.texts import handle_put_texts

        with (
            patch("clio.ui.routes.texts.request_version", return_value="0123456789abcdef") as mock_request,
            patch("clio.ui.routes.texts.save_with_version", return_value=new_version) as mock_save,
        ):
            handle_put_texts(handler, {"file": ["001.json"]}, {"key": "val"})

        mock_request.assert_called_once_with(handler)
        saved_path, saved_data, expected = mock_save.call_args.args
        assert saved_path is path
        assert json.loads(saved_data) == {"key": "val"}
        assert expected == "0123456789abcdef"
        handler._send_json.assert_called_once_with(
            {"ok": True, "path": str(path), "version": new_version},
            extra_headers=version_headers(new_version),
        )

    def test_stale_version_returns_conflict(self, handler):
        current = "aaaaaaaaaaaaaaaa"
        from clio.ui.routes.texts import handle_put_texts

        with patch(
            "clio.ui.routes.texts.save_with_version",
            side_effect=ArtifactConflictError(current),
        ):
            handle_put_texts(handler, {"file": ["001.json"]}, {"key": "val"})

        payload, status = handler._send_json.call_args.args
        assert status == 409
        assert payload["code"] == "artifact_conflict"
        assert payload["current_version"] == current
        assert handler._send_json.call_args.kwargs["extra_headers"] == version_headers(current)


class TestHandlePutVoiceover:
    def test_forbidden(self, handler):
        handler._resolve_in.return_value = None
        from clio.ui.routes.texts import handle_put_voiceover

        handle_put_voiceover(handler, {"file": ["001.json"]}, {"voiceover": "test"})
        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args.args[0]
        assert resp["ok"] is False

    def test_saves_file_with_if_match(self, handler):
        path = handler._resolve_in.return_value
        new_version = "2222222222222222"
        from clio.ui.routes.texts import handle_put_voiceover

        with (
            patch("clio.ui.routes.texts.request_version", return_value="0123456789abcdef") as mock_request,
            patch("clio.ui.routes.texts.save_with_version", return_value=new_version) as mock_save,
        ):
            handle_put_voiceover(handler, {"file": ["001.json"]}, {"voiceover": "test"})

        mock_request.assert_called_once_with(handler)
        saved_path, saved_data, expected = mock_save.call_args.args
        assert saved_path is path
        assert json.loads(saved_data) == {"voiceover": "test"}
        assert expected == "0123456789abcdef"
        handler._send_json.assert_called_once_with(
            {"ok": True, "path": str(path), "version": new_version},
            extra_headers=version_headers(new_version),
        )

    def test_stale_version_returns_conflict(self, handler):
        current = "bbbbbbbbbbbbbbbb"
        from clio.ui.routes.texts import handle_put_voiceover

        with patch(
            "clio.ui.routes.texts.save_with_version",
            side_effect=ArtifactConflictError(current),
        ):
            handle_put_voiceover(handler, {"file": ["001.json"]}, {"voiceover": "test"})

        payload, status = handler._send_json.call_args.args
        assert status == 409
        assert payload["code"] == "artifact_conflict"
        assert payload["current_version"] == current
        assert handler._send_json.call_args.kwargs["extra_headers"] == version_headers(current)


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

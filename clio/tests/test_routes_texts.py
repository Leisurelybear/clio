from __future__ import annotations

import json
from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.services.file_service import version_headers


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
    PAYLOAD = {
        "title": "A",
        "summary": "s",
        "timeline": [{"start": "00:00", "end": "00:05", "description": "d"}],
    }

    @staticmethod
    def _write_analysis(path: Path, **overrides) -> dict:
        data = {
            "_lineage": "server-lineage",
            "_lineage_components": {"prompt": "server-prompt"},
            "_schema_version": 2,
            "compressed_file": "001.mp4",
            "cover_file": "covers/001.jpg",
            "index": 7,
            "media_identity": {"source_stem": "source"},
            "source_file": "source.mp4",
            "title": "Old title",
            "summary": "old summary",
            "timeline": [],
        }
        data.update(overrides)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def test_forbidden(self, handler):
        handler._resolve_texts.return_value = None
        from clio.ui.routes.texts import handle_put_texts

        handle_put_texts(handler, {"file": ["001.json"]}, {"key": "val"})
        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args.args[0]
        assert resp["ok"] is False

    def test_saves_file_with_if_match_and_preserves_server_fields(self, handler, tmp_path: Path):
        path = tmp_path / "001.json"
        existing = self._write_analysis(path)
        sidecar = path.with_suffix(".txt")
        sidecar.write_text("old sidecar", encoding="utf-8")
        handler._resolve_texts.return_value = path
        from clio.ui.routes.texts import handle_put_texts

        payload = {
            "title": "New title",
            "summary": "new summary",
            "timeline": [{"start": "00:00", "end": "00:05", "description": "new"}],
            "index": 999,
            "_lineage": "tampered",
            "_lineage_components": {"prompt": "tampered"},
            "media_identity": {"source_stem": "tampered"},
            "source_file": "tampered.mp4",
        }
        from clio.ui.services.file_service import content_version

        expected = content_version(path.read_bytes())
        with patch("clio.ui.routes.texts.request_version", return_value=expected) as mock_request:
            handle_put_texts(handler, {"file": ["001.json"]}, payload)

        mock_request.assert_called_once_with(handler)
        saved = json.loads(path.read_text(encoding="utf-8"))
        for key in ("_lineage", "_lineage_components", "media_identity", "source_file"):
            assert saved[key] == existing[key]
        assert saved["index"] == existing["index"]
        assert saved["title"] == "New title"
        assert saved["summary"] == "new summary"
        assert saved["_user_edited"] is True
        assert saved["_user_edited_at"]
        assert "New title" in sidecar.read_text(encoding="utf-8")
        body = handler._send_json.call_args.args[0]
        assert body["ok"] is True
        assert body["version"] == content_version(path.read_bytes())

    def test_stale_version_returns_conflict_without_rewriting_sidecar(self, handler, tmp_path: Path):
        path = tmp_path / "001.json"
        self._write_analysis(path)
        sidecar = path.with_suffix(".txt")
        sidecar.write_text("old sidecar", encoding="utf-8")
        handler._resolve_texts.return_value = path
        from clio.ui.routes.texts import handle_put_texts

        with patch("clio.ui.routes.texts.request_version", return_value="aaaaaaaaaaaaaaaa"):
            handle_put_texts(handler, {"file": ["001.json"]}, dict(self.PAYLOAD))

        payload, status = handler._send_json.call_args.args
        assert status == 409
        assert payload["code"] == "artifact_conflict"
        assert sidecar.read_text(encoding="utf-8") == "old sidecar"
        assert json.loads(path.read_text(encoding="utf-8"))["title"] == "Old title"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"title": ""},
            {"title": "   "},
            {"title": 5},
            {"title": "A", "summary": 5},
            {"title": "A", "location": ["x"]},
            {"title": "A"},
            {"title": "A", "timeline": "00:00-00:05"},
            {"title": "A", "timeline": [{"start": "00:00"}]},
            {"title": "A", "timeline": [{"start": True, "end": "00:05"}]},
            {"title": "A", "timeline": [{"start": None, "end": "00:05"}]},
            {"title": "A", "timeline": [{"start": "", "end": "00:05"}]},
            {"title": "A", "timeline": [{"start": "00:00", "end": "00:05", "description": 1}]},
            {"title": "A", "timeline": ["not-a-dict"]},
            {"title": "A", "timeline": [], "highlights": "x"},
            {"title": "A", "timeline": [], "highlights": [1]},
        ],
    )
    def test_rejects_malformed_analysis(self, handler, payload):
        from clio.ui.routes.texts import handle_put_texts

        with (
            patch("clio.ui.routes.texts.request_version") as mock_request,
            patch("clio.ui.routes.texts.save_with_version") as mock_save,
        ):
            handle_put_texts(handler, {"file": ["001.json"]}, payload)

        mock_request.assert_not_called()
        mock_save.assert_not_called()
        body, status = handler._send_json.call_args.args
        assert status == 400
        assert body["ok"] is False
        assert body["code"] == "invalid_analysis"
        assert body["error"]

    def test_accepts_minimal_valid_payload(self, handler, tmp_path: Path):
        path = tmp_path / "001.json"
        self._write_analysis(path)
        handler._resolve_texts.return_value = path
        from clio.ui.routes.texts import handle_put_texts

        handle_put_texts(
            handler,
            {"file": ["001.json"]},
            {"title": "A", "summary": "s", "timeline": []},
        )

        body = handler._send_json.call_args.args[0]
        assert body["ok"] is True

    def test_accepts_numeric_timeline_bounds_and_null_optionals(self, handler, tmp_path: Path):
        path = tmp_path / "001.json"
        self._write_analysis(path)
        handler._resolve_texts.return_value = path
        from clio.ui.routes.texts import handle_put_texts

        handle_put_texts(
            handler,
            {"file": ["001.json"]},
            {
                "title": "A",
                "summary": None,
                "location": None,
                "mood": None,
                "suggested_use": None,
                "cover_timestamp": None,
                "timeline": [{"start": 0, "end": 5.5}],
                "highlights": ["x", {"text": "y"}],
            },
        )

        body = handler._send_json.call_args.args[0]
        assert body["ok"] is True


class TestHandlePutVoiceover:
    @staticmethod
    def _write_script(path: Path, **overrides) -> dict:
        data = {
            "_lineage": "server-lineage",
            "_schema_version": 2,
            "title": "Old title",
            "voiceover": "old voiceover",
            "edit_tip": "old tip",
            "duration_hint_sec": 20,
        }
        data.update(overrides)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def test_forbidden(self, handler):
        handler._resolve_in.return_value = None
        from clio.ui.routes.texts import handle_put_voiceover

        handle_put_voiceover(handler, {"file": ["001.json"]}, {"voiceover": "test"})
        handler._send_json.assert_called_once()
        resp = handler._send_json.call_args.args[0]
        assert resp["ok"] is False

    def test_saves_file_with_if_match_and_rewrites_markdown(self, handler, tmp_path: Path):
        path = tmp_path / "001_voiceover.json"
        existing = self._write_script(path)
        sidecar = path.with_suffix(".md")
        sidecar.write_text("old markdown", encoding="utf-8")
        handler._resolve_in.return_value = path
        from clio.ui.routes.texts import handle_put_voiceover
        from clio.ui.services.file_service import content_version

        expected = content_version(path.read_bytes())
        with patch("clio.ui.routes.texts.request_version", return_value=expected) as mock_request:
            handle_put_voiceover(
                handler,
                {"file": ["001_voiceover.json"]},
                {
                    "title": "New title",
                    "voiceover": "new voiceover",
                    "edit_tip": "new tip",
                    "duration_hint_sec": 30,
                    "_lineage": "tampered",
                    "_schema_version": 99,
                },
            )

        mock_request.assert_called_once_with(handler)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["_lineage"] == existing["_lineage"]
        assert saved["_schema_version"] == existing["_schema_version"]
        assert saved["voiceover"] == "new voiceover"
        assert saved["_user_edited"] is True
        assert saved["_user_edited_at"]
        markdown = sidecar.read_text(encoding="utf-8")
        assert "New title" in markdown
        assert "new voiceover" in markdown
        assert "old voiceover" not in markdown
        body = handler._send_json.call_args.args[0]
        assert body["ok"] is True
        assert body["version"] == content_version(path.read_bytes())

    def test_stale_version_returns_conflict_without_rewriting_markdown(self, handler, tmp_path: Path):
        path = tmp_path / "001_voiceover.json"
        self._write_script(path)
        sidecar = path.with_suffix(".md")
        sidecar.write_text("old markdown", encoding="utf-8")
        handler._resolve_in.return_value = path
        from clio.ui.routes.texts import handle_put_voiceover

        with patch("clio.ui.routes.texts.request_version", return_value="bbbbbbbbbbbbbbbb"):
            handle_put_voiceover(handler, {"file": ["001_voiceover.json"]}, {"voiceover": "test"})

        payload, status = handler._send_json.call_args.args
        assert status == 409
        assert payload["code"] == "artifact_conflict"
        assert sidecar.read_text(encoding="utf-8") == "old markdown"
        assert json.loads(path.read_text(encoding="utf-8"))["voiceover"] == "old voiceover"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"voiceover": ""},
            {"voiceover": "   "},
            {"voiceover": 1},
            {"voiceover": "text", "title": 1},
            {"voiceover": "text", "edit_tip": 1},
            {"voiceover": "text", "duration_hint_sec": True},
            {"voiceover": "text", "duration_hint_sec": -1},
            {"voiceover": "text", "duration_hint_sec": "20"},
        ],
    )
    def test_rejects_malformed_voiceover(self, handler, payload):
        from clio.ui.routes.texts import handle_put_voiceover

        with (
            patch("clio.ui.routes.texts.request_version") as mock_request,
            patch("clio.ui.routes.texts.save_with_version") as mock_save,
        ):
            handle_put_voiceover(handler, {"file": ["001_voiceover.json"]}, payload)

        mock_request.assert_not_called()
        mock_save.assert_not_called()
        body, status = handler._send_json.call_args.args
        assert status == 400
        assert body["ok"] is False
        assert body["code"] == "invalid_voiceover"
        assert body["error"]


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

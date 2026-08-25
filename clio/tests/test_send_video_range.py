"""Tests for send_video_range — P2-P40 Range / 416 / HEAD."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clio.ui.services.file_service import send_video_range


def _handler(method: str = "GET", range_hdr: str | None = None) -> MagicMock:
    h = MagicMock()
    h.command = method
    h.headers = {}
    if range_hdr is not None:
        h.headers["Range"] = range_hdr
    h.wfile = MagicMock()
    return h


@pytest.fixture
def video(tmp_path: Path) -> Path:
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"0123456789ABCDEF")  # 16 bytes
    return p


class TestSendVideoRange:
    def test_rejects_trailing_garbage(self, video: Path):
        h = _handler(range_hdr="bytes=0-7;extra")
        send_video_range(h, video)
        # Must be 416 with Content-Range */size, not a partial 206
        assert h.send_response.call_args[0][0] == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
        headers = {c.args[0]: c.args[1] for c in h.send_header.call_args_list}
        assert headers.get("Content-Range") == f"bytes */{video.stat().st_size}"
        h.wfile.write.assert_not_called()

    def test_out_of_range_includes_content_range(self, video: Path):
        h = _handler(range_hdr="bytes=100-200")
        send_video_range(h, video)
        assert h.send_response.call_args[0][0] == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
        headers = {c.args[0]: c.args[1] for c in h.send_header.call_args_list}
        assert headers.get("Content-Range") == f"bytes */{video.stat().st_size}"

    def test_valid_range_returns_206(self, video: Path):
        h = _handler(range_hdr="bytes=0-7")
        send_video_range(h, video)
        assert h.send_response.call_args[0][0] == 206
        written = b"".join(c.args[0] for c in h.wfile.write.call_args_list)
        assert written == b"01234567"

    def test_response_is_browser_cacheable(self, video: Path):
        h = _handler(range_hdr="bytes=0-7")
        send_video_range(h, video)
        headers = {c.args[0]: c.args[1] for c in h.send_header.call_args_list}
        assert headers.get("Cache-Control") == "private, max-age=3600"

    def test_head_sends_headers_without_body(self, video: Path):
        h = _handler(method="HEAD", range_hdr="bytes=0-7")
        send_video_range(h, video)
        assert h.send_response.call_args[0][0] == 206
        h.end_headers.assert_called_once()
        h.wfile.write.assert_not_called()

    def test_head_full_file(self, video: Path):
        h = _handler(method="HEAD")
        send_video_range(h, video)
        assert h.send_response.call_args[0][0] == 200
        headers = {c.args[0]: c.args[1] for c in h.send_header.call_args_list}
        assert headers.get("Content-Length") == str(video.stat().st_size)
        h.wfile.write.assert_not_called()

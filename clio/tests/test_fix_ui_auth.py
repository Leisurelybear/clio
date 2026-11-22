"""Regression tests for the local web UI request gate and session cookie."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from clio.ui.handler_factory import _RequestHandler
from clio.ui.handler_security import validate_fetch_site

LOOPBACK = ("127.0.0.1", "::1", "localhost")


class _GateHandler(_RequestHandler):
    _enforce_local_session = True
    _enforce_request_origin = True
    _allowed_hosts = LOOPBACK
    _api_token = "session-token"


def _make(path: str, headers: dict[str, str]) -> _GateHandler:
    handler = _GateHandler.__new__(_GateHandler)
    handler.path = path
    handler.headers = headers  # type: ignore[assignment]
    handler._send_json = MagicMock()  # type: ignore[method-assign]
    return handler


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"Sec-Fetch-Site": "same-origin"}, None),
        ({"Sec-Fetch-Site": "none"}, None),
        ({"Sec-Fetch-Site": "cross-site"}, "api.invalid_origin"),
        ({"Sec-Fetch-Site": "same-site"}, "api.invalid_origin"),
    ],
)
def test_validate_fetch_site(headers, expected):
    assert validate_fetch_site(headers) == expected


def test_cross_site_get_without_origin_or_referer_is_rejected():
    # <img src="http://127.0.0.1:8765/api/fs/entries?path=..." referrerpolicy="no-referrer">
    handler = _make(
        "/api/fs/entries?path=%5C%5Cattacker%5Cs",
        {"Host": "127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    )
    assert handler._require_local_session() is False
    assert handler._send_json.call_args.args[1] == 403


def test_same_origin_api_get_passes_gate():
    handler = _make("/api/videos", {"Host": "127.0.0.1:8765", "Sec-Fetch-Site": "same-origin"})
    assert handler._require_local_session() is True


def test_static_assets_are_not_fetch_site_gated():
    handler = _make("/static/app.js", {"Host": "127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"})
    assert handler._require_local_session() is True

"""Regression tests for the local web UI request gate and session cookie."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from clio.ui.handler_factory import _RequestHandler
from clio.ui.handler_security import (
    SESSION_COOKIE_NAME,
    api_token_source,
    session_cookie_header,
    validate_fetch_site,
)

LOOPBACK = ("127.0.0.1", "::1", "localhost")


class _GateHandler(_RequestHandler):
    _enforce_local_session = True
    _enforce_request_origin = True
    _allowed_hosts = LOOPBACK
    _api_token = "session-token"
    _security_headers = ()


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


# ---------------------------------------------------------------------------
# D-02: always-on session token handed over as an HttpOnly cookie
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "path", "expected"),
    [
        ({}, "/api/env", None),
        ({"Cookie": f"{SESSION_COOKIE_NAME}=session-token"}, "/api/env", "cookie"),
        ({"Cookie": f"other=1; {SESSION_COOKIE_NAME}=session-token"}, "/api/env", "cookie"),
        ({"Cookie": f"{SESSION_COOKIE_NAME}=wrong"}, "/api/env", None),
        ({"Authorization": "Bearer session-token"}, "/api/env", "bearer"),
        ({}, "/api/env?token=session-token", "query"),
        ({}, "/api/env?token=%E4%BD%A0%E5%A5%BD%E4%BD%A0%E5%A5%BD", None),
    ],
)
def test_api_token_source(headers, path, expected):
    assert api_token_source(headers, path, "session-token") == expected


def test_session_cookie_attributes():
    assert session_cookie_header("abc") == "clio_session=abc; HttpOnly; SameSite=Strict; Path=/"


def test_no_origin_api_request_without_token_is_unauthorized():
    handler = _make("/api/credentials/gemini/reveal", {"Host": "127.0.0.1:8765"})
    assert handler._require_local_session() is True  # nothing to judge the origin by
    assert handler._require_auth() is False
    assert handler._send_json.call_args.args[1] == 401


def test_cookie_authenticates_without_reissuing():
    handler = _make("/api/env", {"Cookie": f"{SESSION_COOKIE_NAME}=session-token"})
    assert handler._require_auth() is True
    assert handler._pending_session_cookie is None


def test_bearer_or_query_auth_is_exchanged_for_cookie():
    handler = _make("/api/env?token=session-token", {})
    assert handler._require_auth() is True
    assert handler._pending_session_cookie == session_cookie_header("session-token")


def test_index_sets_cookie_on_loopback_bind():
    handler = _make("/", {"Host": "127.0.0.1:8765"})
    handler._offer_session_cookie()
    assert handler._pending_session_cookie == session_cookie_header("session-token")


def test_index_on_lan_bind_requires_token_before_issuing_cookie():
    class _LanHandler(_GateHandler):
        _enforce_local_session = False

    plain = _LanHandler.__new__(_LanHandler)
    plain.path = "/"
    plain.headers = {"Host": "192.168.1.5:8765"}  # type: ignore[assignment]
    plain._offer_session_cookie()
    assert plain._pending_session_cookie is None

    with_token = _LanHandler.__new__(_LanHandler)
    with_token.path = "/?token=session-token"
    with_token.headers = {"Host": "192.168.1.5:8765"}  # type: ignore[assignment]
    with_token._offer_session_cookie()
    assert with_token._pending_session_cookie == session_cookie_header("session-token")


def test_pending_cookie_is_emitted_once_by_end_headers():
    handler = _GateHandler.__new__(_GateHandler)
    handler._sent_header_names = set()
    sent: list[tuple[str, str]] = []
    handler.send_header = lambda key, value: sent.append((key, value))  # type: ignore[method-assign]
    handler._pending_session_cookie = session_cookie_header("session-token")
    handler.wfile = MagicMock()
    handler._headers_buffer = []
    handler.request_version = "HTTP/1.1"
    handler.end_headers()
    assert sent == [("Set-Cookie", session_cookie_header("session-token"))]
    assert handler._pending_session_cookie is None

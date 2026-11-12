from __future__ import annotations

import pytest

from clio.ui.handler_security import has_valid_api_token, validate_local_session

ALLOWED_HOSTS = ("127.0.0.1", "localhost", "::1")


@pytest.mark.parametrize(
    ("headers", "path", "token", "expected"),
    [
        ({}, "/api/env", "", True),
        ({}, "/api/env", None, True),
        ({"Authorization": "Bearer secret"}, "/api/env", "secret", True),
        ({}, "/api/env?token=secret", "secret", True),
        ({"Authorization": "Bearer wrong"}, "/api/env", "secret", False),
        ({}, "/api/env?token=wrong", "secret", False),
        ({"Authorization": "Basic secret"}, "/api/env", "secret", False),
    ],
)
def test_has_valid_api_token(headers, path, token, expected):
    assert has_valid_api_token(headers, path, token) is expected


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, None),
        ({"Host": "127.0.0.1:8765"}, None),
        ({"Host": "[::1]:8765"}, None),
        ({"Host": "evil.example.com"}, "api.invalid_host"),
        ({"Host": "127.0.0.1", "Origin": "https://evil.example.com"}, "api.invalid_origin"),
        ({"Host": "127.0.0.1", "Origin": "null"}, "api.invalid_origin"),
        ({"Host": "127.0.0.1", "Referer": "https://evil.example.com/page"}, "api.invalid_origin"),
        ({"Host": "127.0.0.1", "Origin": "http://127.0.0.1:8765"}, None),
        ({"Host": "127.0.0.1", "Origin": "http://[::1]:8765"}, None),
    ],
)
def test_validate_local_session(headers, expected):
    assert validate_local_session(headers, ALLOWED_HOSTS) == expected

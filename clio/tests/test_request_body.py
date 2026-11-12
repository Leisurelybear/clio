from __future__ import annotations

import io

import pytest

from clio.ui.request_body import MAX_JSON_BODY_BYTES, parse_json_content_length, read_json_body


def _headers(values: dict[str, str]) -> dict[str, str]:
    return values


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, (0, None)),
        ("", (0, None)),
        ("12", (12, None)),
        ("abc", (None, 400)),
        ("-1", (None, 400)),
        (str(MAX_JSON_BODY_BYTES + 1), (None, 413)),
    ],
)
def test_parse_json_content_length(raw, expected):
    assert parse_json_content_length(raw) == expected


def test_read_json_body_accepts_object_with_json_content_type():
    result = read_json_body(
        _headers({"Content-Type": "application/json; charset=utf-8", "Content-Length": "7"}),
        io.BytesIO(b'{"a":1}'),
        require_json_content_type=True,
    )

    assert result.obj == {"a": 1}
    assert result.raw == b'{"a":1}'
    assert result.status is None
    assert result.error is None


def test_read_json_body_rejects_wrong_content_type_without_reading():
    result = read_json_body(
        _headers({"Content-Type": "text/plain", "Content-Length": "2"}),
        io.BytesIO(b"{}"),
        require_json_content_type=True,
    )

    assert result.status == 415
    assert result.error == "unsupported_content_type"


def test_read_json_body_allows_other_content_types_when_not_enforced():
    result = read_json_body(
        _headers({"Content-Type": "text/plain", "Content-Length": "2"}),
        io.BytesIO(b"{}"),
        require_json_content_type=False,
    )

    assert result.obj == {}


def test_read_json_body_reports_invalid_content_length():
    result = read_json_body(
        _headers({"Content-Length": "invalid"}),
        io.BytesIO(b""),
        require_json_content_type=False,
    )

    assert result.status == 400
    assert result.error == "invalid_content_length"


def test_read_json_body_rejects_oversized_body_before_reading():
    class Unreadable(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            raise AssertionError("oversized body must not be read")

    result = read_json_body(
        _headers({"Content-Length": str(MAX_JSON_BODY_BYTES + 1)}),
        Unreadable(),
        require_json_content_type=False,
    )

    assert result.status == 413
    assert result.error == "too_large"


def test_read_json_body_reports_invalid_json_and_preserves_raw_body():
    result = read_json_body(
        _headers({"Content-Length": "6"}),
        io.BytesIO(b"not{}!"),
        require_json_content_type=False,
    )

    assert result.status == 400
    assert result.error == "invalid_json"
    assert result.detail
    assert result.raw == b"not{}!"


def test_read_json_body_rejects_non_object_json():
    result = read_json_body(
        _headers({"Content-Length": "2"}),
        io.BytesIO(b"[]"),
        require_json_content_type=False,
    )

    assert result.status == 400
    assert result.error == "invalid_json"
    assert result.detail == "expected a JSON object"

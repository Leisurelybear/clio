"""Regression tests: bodyless POST is {} while PUT and content-type rules stay strict."""

from __future__ import annotations

import io

from clio.ui.request_body import read_json_body


def test_empty_body_is_empty_object_when_allowed():
    result = read_json_body(
        {"Content-Type": "application/json", "Content-Length": "0"},
        io.BytesIO(b""),
        require_json_content_type=True,
        allow_empty=True,
    )
    assert result.obj == {}
    assert result.status is None


def test_missing_content_length_is_empty_object_when_allowed():
    result = read_json_body({}, io.BytesIO(b""), require_json_content_type=False, allow_empty=True)
    assert result.obj == {}


def test_empty_body_still_invalid_by_default():
    result = read_json_body({"Content-Length": "0"}, io.BytesIO(b""), require_json_content_type=False)
    assert result.status == 400
    assert result.error == "invalid_json"


def test_allow_empty_does_not_bypass_content_type_check():
    result = read_json_body(
        {"Content-Type": "text/plain", "Content-Length": "0"},
        io.BytesIO(b""),
        require_json_content_type=True,
        allow_empty=True,
    )
    assert result.status == 415

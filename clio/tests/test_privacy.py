"""Tests for clio/privacy.py — GAP-P2-05 log redaction."""

from __future__ import annotations

from clio.privacy import redact_sensitive


def test_redacts_bearer_token():
    assert "secret-token" not in redact_sensitive("Authorization: Bearer secret-token")
    assert "***" in redact_sensitive("Authorization: Bearer secret-token")


def test_redacts_api_key_assignment():
    out = redact_sensitive("api_key=sk-live-abcdefghijklmnop")
    assert "abcdefghijklmnop" not in out
    assert "api_key=" in out


def test_redacts_url_userinfo():
    out = redact_sensitive("proxy http://user:hunter2@example.com:8080")
    assert "hunter2" not in out
    assert "user" not in out or "***" in out
    assert "example.com" in out


def test_truncates_long_lines():
    huge = "x" * 5000
    out = redact_sensitive(huge, max_len=100)
    assert len(out) < 200
    assert "已截断" in out


def test_plain_text_unchanged():
    assert redact_sensitive("hello world") == "hello world"

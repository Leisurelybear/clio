"""Tests for clio/http_access_log.py — access line formatting and body redaction."""

from __future__ import annotations

from clio.http_access_log import (
    ACCESS_LOG_MODES,
    MAX_ACCESS_LOG_BODY_BYTES,
    MAX_ACCESS_LOG_PATH,
    format_access_line,
    prepare_body,
    sanitize_target,
    should_log,
)


class TestSanitizeTarget:
    def test_masks_token_query_value(self):
        assert sanitize_target("/api/plan?token=secret&project=abc") == "/api/plan?token=***&project=abc"

    def test_masks_sensitive_keys_case_insensitively(self):
        out = sanitize_target("/api/x?TOKEN=abc&Api_Key=def&secret=ghi&keep=ok")
        assert out == "/api/x?TOKEN=***&Api_Key=***&secret=***&keep=ok"

    def test_masks_url_encoded_sensitive_key(self):
        out = sanitize_target("/api/x?api%5Fkey=def&keep=ok")
        assert out == "/api/x?api%5Fkey=***&keep=ok"

    def test_keeps_path_without_query(self):
        assert sanitize_target("/api/plan") == "/api/plan"

    def test_truncates_long_target(self):
        out = sanitize_target("/api/plan?project=" + "x" * (MAX_ACCESS_LOG_PATH * 2))
        assert len(out) <= MAX_ACCESS_LOG_PATH + len("...[truncated]")
        assert out.endswith("...[truncated]")

    def test_replaces_control_characters(self):
        assert sanitize_target("/api/plan\r\nX") == "/api/plan  X"


class TestFormatAccessLine:
    def test_formats_method_status_latency_and_size(self):
        line = format_access_line(
            req_id="7f3a",
            method="GET",
            path="/api/plan",
            status=200,
            duration_s=0.0123,
            response_bytes=2160,
            slow_ms=500,
        )
        assert line == "[http] req=7f3a GET /api/plan 200 12.3ms 2.1 KB"

    def test_marks_slow_requests(self):
        line = format_access_line(
            req_id="7f3a",
            method="GET",
            path="/api/plan",
            status=200,
            duration_s=0.75,
            response_bytes=10,
            slow_ms=500,
        )
        assert line.endswith("SLOW")

    def test_unknown_size_is_dash(self):
        line = format_access_line(
            req_id="7f3a",
            method="HEAD",
            path="/api/video",
            status=206,
            duration_s=0.1,
            response_bytes=None,
            slow_ms=500,
        )
        assert "206 100.0ms -" in line

    def test_masks_token_in_path(self):
        line = format_access_line(
            req_id="7f3a",
            method="GET",
            path="/?token=secret",
            status=200,
            duration_s=0.1,
            response_bytes=1,
            slow_ms=500,
        )
        assert "secret" not in line
        assert "token=***" in line


class TestShouldLog:
    def test_modes_cover_off_errors_api_all(self):
        assert ACCESS_LOG_MODES == ("off", "errors", "api", "all")

    def test_off_never_logs(self):
        assert not should_log("off", "GET", "/api/plan", 200, 0.0, 500)
        assert not should_log("off", "GET", "/api/plan", 500, 0.0, 500)

    def test_errors_only_logs_failures(self):
        assert not should_log("errors", "GET", "/api/plan", 200, 0.0, 500)
        assert should_log("errors", "GET", "/api/plan", 500, 0.0, 500)

    def test_api_mode_skips_static_and_successful_polling(self):
        assert not should_log("api", "GET", "/static/src/main.js", 200, 0.0, 500)
        assert not should_log("api", "GET", "/api/notifications", 200, 0.0, 500)
        assert not should_log("api", "GET", "/api/project", 200, 0.0, 500)

    def test_api_mode_logs_non_polling_get_and_mutations(self):
        assert should_log("api", "GET", "/api/plan", 200, 0.0, 500)
        assert should_log("api", "POST", "/api/run/start", 200, 0.0, 500)
        assert should_log("api", "PUT", "/api/plan", 200, 0.0, 500)

    def test_api_mode_logs_slow_polling_and_binary(self):
        assert should_log("api", "GET", "/api/notifications", 200, 0.75, 500)
        assert should_log("api", "GET", "/api/video", 206, 0.75, 500)
        assert not should_log("api", "GET", "/api/video", 206, 0.1, 500)

    def test_api_mode_logs_failures_for_suppressed_paths(self):
        assert should_log("api", "GET", "/static/src/main.js", 404, 0.0, 500)
        assert should_log("api", "GET", "/api/project", 500, 0.0, 500)

    def test_streams_are_logged_under_api_and_all(self):
        assert should_log("api", "GET", "/api/tasks/stream", 200, 0.0, 500)
        assert should_log("all", "GET", "/api/tasks/stream", 200, 0.0, 500)
        assert not should_log("errors", "GET", "/api/tasks/stream", 200, 0.0, 500)

    def test_all_mode_logs_static_success(self):
        assert should_log("all", "GET", "/static/src/main.js", 200, 0.0, 500)


class TestPrepareBody:
    def test_disabled_when_limit_is_zero(self):
        assert prepare_body(b'{"a":1}', path="/api/plan", limit=0) is None

    def test_denied_paths_never_return_body(self):
        assert prepare_body(b'{"key":"value"}', path="/api/env", limit=2048) is None
        assert prepare_body(b'{"key":"value"}', path="/api/config/raw", limit=2048) is None
        assert prepare_body(b'{"key":"value"}', path="/api/providers", limit=2048) is None

    def test_redacts_sensitive_json_keys_recursively(self):
        raw = b'{"api_key":"sk-live-secret","nested":{"token":"abc","ok":true},"password":"pw"}'
        out = prepare_body(raw, path="/api/plan", limit=2048)
        assert out is not None
        assert "sk-live-secret" not in out
        assert '"abc"' not in out
        assert '"pw"' not in out
        assert out.count('"***"') == 3

    def test_redacts_plain_text_secrets(self):
        out = prepare_body("api_key=sk-live-secret", path="/api/plan", limit=2048)
        assert out is not None
        assert "sk-live-secret" not in out

    def test_truncates_to_limit(self):
        out = prepare_body(b'{"a":"' + b"x" * 500 + b'"}', path="/api/plan", limit=50)
        assert out is not None
        assert "...[truncated " in out
        assert len(out) < 100

    def test_keeps_body_single_line(self):
        out = prepare_body(b'{"a":"line1\\nline2"}', path="/api/plan", limit=2048)
        assert out is not None
        assert "\n" not in out
        assert "\\n" in out

    def test_cap_is_finite(self):
        assert MAX_ACCESS_LOG_BODY_BYTES >= 1024

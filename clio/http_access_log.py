"""HTTP access log formatting, noise policy, and safe body capture."""

from __future__ import annotations

import json
import re
from urllib.parse import unquote_plus

from clio.log import format_size
from clio.privacy import redact_sensitive

ACCESS_LOG_MODES: tuple[str, ...] = ("off", "errors", "api", "all")
DEFAULT_ACCESS_LOG_MODE = "api"
DEFAULT_ACCESS_LOG_SLOW_MS = 500
DEFAULT_ACCESS_LOG_BODY_BYTES = 0
MAX_ACCESS_LOG_BODY_BYTES = 4096
MAX_ACCESS_LOG_PATH = 512

_SENSITIVE_QUERY_KEYS = {
    "token",
    "api_key",
    "api-key",
    "apikey",
    "key",
    "secret",
    "password",
    "passwd",
    "authorization",
    "access_key",
}
_SENSITIVE_JSON_KEY = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|credential|access[_-]?key)"
)
_CONTROL_CHARS = re.compile(r"[\r\n\t]")

_STATIC_EXACT = {"/", "/index.html", "/favicon.ico"}
_STATIC_PREFIXES = ("/static/",)
_POLLING_GET = {"/api/notifications", "/api/project", "/api/plan/readiness", "/api/tasks"}
_BINARY_GET = {"/api/video", "/api/cover", "/api/waveform"}
_STREAM_PATHS = frozenset({"/api/notifications/stream", "/api/tasks/stream"})
_BODY_DENIED_EXACT = {"/api/env"}
_BODY_DENIED_PREFIXES = ("/api/config", "/api/providers", "/api/logs")


def path_only(target: str) -> str:
    return target.split("?", 1)[0]


def normalize_mode(mode: str | None) -> str:
    return mode if mode in ACCESS_LOG_MODES else DEFAULT_ACCESS_LOG_MODE


def is_static(path: str) -> bool:
    path = path_only(path)
    return path in _STATIC_EXACT or any(path.startswith(prefix) for prefix in _STATIC_PREFIXES)


def is_polling(path: str) -> bool:
    return path_only(path) in _POLLING_GET


def is_binary(path: str) -> bool:
    return path_only(path) in _BINARY_GET


def is_stream(path: str) -> bool:
    return path_only(path) in _STREAM_PATHS


def is_body_denied(path: str) -> bool:
    path = path_only(path)
    return path in _BODY_DENIED_EXACT or any(path.startswith(prefix) for prefix in _BODY_DENIED_PREFIXES)


def should_log(
    mode: str | None,
    method: str,
    path: str,
    status: int,
    duration_s: float,
    slow_ms: int,
) -> bool:
    mode = normalize_mode(mode)
    if mode == "off":
        return False
    if status >= 400:
        return True
    if mode == "errors":
        return False
    if is_stream(path):
        return mode in ("api", "all")
    if mode == "all":
        return True
    if method.upper() not in ("GET", "HEAD"):
        return True
    if is_static(path):
        return False
    if is_polling(path) or is_binary(path):
        return duration_s * 1000 >= slow_ms
    return True


def sanitize_target(target: str, *, max_chars: int = MAX_ACCESS_LOG_PATH) -> str:
    path, separator, query = target.partition("?")
    if separator and query:
        masked: list[str] = []
        for part in query.split("&"):
            if not part:
                continue
            name, has_value, _value = part.partition("=")
            decoded_name = unquote_plus(name).lower()
            value = "***" if decoded_name in _SENSITIVE_QUERY_KEYS else _value
            masked.append(f"{name}={value}" if has_value else name)
        target = f"{path}?{'&'.join(masked)}"
    else:
        target = path
    target = _CONTROL_CHARS.sub(" ", target)
    if len(target) > max_chars:
        target = target[:max_chars] + "...[truncated]"
    return target


def format_access_line(
    *,
    req_id: str,
    method: str,
    path: str,
    status: int,
    duration_s: float,
    response_bytes: int | None,
    slow_ms: int,
) -> str:
    duration_ms = max(0.0, duration_s * 1000)
    size = "-" if response_bytes is None else format_size(response_bytes)
    line = f"[http] req={req_id} {method.upper()} {sanitize_target(path)} {status} {duration_ms:.1f}ms {size}"
    if duration_ms >= slow_ms:
        line += " SLOW"
    return line


def _redact_json(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_JSON_KEY.search(key):
                out[key] = "***"
            else:
                out[key] = _redact_json(item)
        return out
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive(value)
    return value


def _truncate_bytes(text: str, limit: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    removed = len(encoded) - limit
    return encoded[:limit].decode("utf-8", "ignore") + f"...[truncated {removed} bytes]"


def prepare_body(raw: bytes | str, *, path: str, limit: int) -> str | None:
    if limit <= 0 or is_body_denied(path):
        return None
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        normalized = redact_sensitive(text)
    else:
        normalized = json.dumps(_redact_json(parsed), ensure_ascii=False, separators=(",", ":"))
    normalized = normalized.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return _truncate_bytes(normalized, limit)

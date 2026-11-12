"""Bounded JSON request-body parsing for the web UI handler."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol, overload

MAX_JSON_BODY_BYTES = 8 * 1024 * 1024

JsonBodyError = Literal[
    "unsupported_content_type",
    "invalid_content_length",
    "too_large",
    "invalid_json",
]


class HeaderLookup(Protocol):
    @overload
    def get(self, key: str, default: None = None) -> str | None: ...

    @overload
    def get(self, key: str, default: str) -> str: ...

    def get(self, key: str, default: str | None = None) -> str | None: ...


class BodyReader(Protocol):
    def read(self, size: int | None = -1) -> bytes: ...


@dataclass(frozen=True)
class JsonBodyResult:
    obj: dict[str, Any] | None = None
    raw: bytes = b""
    status: int | None = None
    error: JsonBodyError | None = None
    detail: str | None = None


def parse_json_content_length(
    raw_len: str | None, max_bytes: int = MAX_JSON_BODY_BYTES
) -> tuple[int | None, int | None]:
    """Return ``(length, error_status)`` with 400/413 for invalid lengths."""
    if raw_len is None or raw_len == "":
        return 0, None
    try:
        length = int(raw_len)
    except (TypeError, ValueError):
        return None, 400
    if length < 0:
        return None, 400
    if length > max_bytes:
        return None, 413
    return length, None


def read_json_body(
    headers: HeaderLookup,
    reader: BodyReader,
    *,
    require_json_content_type: bool,
    max_bytes: int = MAX_JSON_BODY_BYTES,
) -> JsonBodyResult:
    """Read a bounded JSON object and report protocol or payload errors."""
    if require_json_content_type:
        content_type = (headers.get("Content-Type", "") or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return JsonBodyResult(
                status=415,
                error="unsupported_content_type",
            )
    length, error_status = parse_json_content_length(headers.get("Content-Length", "0"), max_bytes)
    if error_status is not None:
        error: JsonBodyError = "invalid_content_length" if error_status == 400 else "too_large"
        return JsonBodyResult(status=error_status, error=error)
    assert length is not None
    raw = reader.read(length) if length else b""
    try:
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("expected a JSON object")
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        return JsonBodyResult(
            raw=raw,
            status=400,
            error="invalid_json",
            detail=str(exc),
        )
    return JsonBodyResult(obj=obj, raw=raw)

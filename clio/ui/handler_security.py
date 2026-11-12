"""Authentication and local-session validation for the web UI handler."""

from __future__ import annotations

import hmac
from typing import Literal, Protocol, overload
from urllib.parse import parse_qs, urlparse

LocalSessionError = Literal["api.invalid_host", "api.invalid_origin"]


class HeaderLookup(Protocol):
    @overload
    def get(self, key: str, default: None = None) -> str | None: ...

    @overload
    def get(self, key: str, default: str) -> str: ...

    def get(self, key: str, default: str | None = None) -> str | None: ...


def has_valid_api_token(headers: HeaderLookup, path: str, token: str | None) -> bool:
    """Return whether a request carries the configured API token."""
    if not token:
        return True
    auth = headers.get("Authorization", "")
    if isinstance(auth, str) and auth.startswith("Bearer "):
        provided = auth[7:]
        if isinstance(provided, str) and len(provided) == len(token) and hmac.compare_digest(provided, token):
            return True
    query_token = parse_qs(urlparse(path).query).get("token", [None])[0]
    return isinstance(query_token, str) and len(query_token) == len(token) and hmac.compare_digest(query_token, token)


def validate_local_session(headers: HeaderLookup, allowed_hosts: tuple[str, ...]) -> LocalSessionError | None:
    """Reject cross-origin and DNS-rebinding requests to a loopback server."""
    hostname = _hostname_no_port(headers.get("Host", "") or "")
    if hostname and hostname not in allowed_hosts and hostname != "localhost":
        return "api.invalid_host"
    origin_header = headers.get("Origin")
    if isinstance(origin_header, str) and origin_header.strip().lower() == "null":
        return "api.invalid_origin"
    origin = origin_header or headers.get("Referer", "") or ""
    if not origin:
        return None
    try:
        parsed = urlparse(origin)
    except ValueError:
        parsed = urlparse("")
    origin_host = (parsed.hostname or "").lower()
    if origin_host and origin_host not in allowed_hosts and origin_host != "localhost":
        return "api.invalid_origin"
    return None


def _hostname_no_port(value: str) -> str:
    value = value.strip().lower()
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    if value.count(":") == 1:
        return value.split(":", 1)[0]
    return value

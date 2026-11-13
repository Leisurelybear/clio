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


def validate_request_origin(
    headers: HeaderLookup,
    allowed_hosts: tuple[str, ...] | None = None,
) -> LocalSessionError | None:
    """Reject opaque or cross-origin requests by comparing Origin/Referer to Host."""
    origin_header = headers.get("Origin")
    if isinstance(origin_header, str) and origin_header.strip().lower() == "null":
        return "api.invalid_origin"
    origin = origin_header or headers.get("Referer", "") or ""
    if not origin:
        return None
    try:
        parsed = urlparse(origin)
        origin_host = (parsed.hostname or "").lower()
        origin_port = parsed.port
    except ValueError:
        return "api.invalid_origin"
    if parsed.scheme not in ("http", "https") or not origin_host:
        return "api.invalid_origin"
    if allowed_hosts is not None and origin_host not in allowed_hosts and origin_host != "localhost":
        return "api.invalid_origin"
    default_port = 443 if parsed.scheme == "https" else 80
    origin_port = origin_port or default_port
    hostname, host_port = _host_and_port(headers.get("Host", "") or "")
    if not hostname or hostname != origin_host or (host_port or default_port) != origin_port:
        return "api.invalid_origin"
    return None


def validate_local_session(headers: HeaderLookup, allowed_hosts: tuple[str, ...]) -> LocalSessionError | None:
    """Reject non-loopback hosts and cross-origin requests to a loopback server."""
    hostname, _ = _host_and_port(headers.get("Host", "") or "")
    if hostname and hostname not in allowed_hosts and hostname != "localhost":
        return "api.invalid_host"
    return validate_request_origin(headers, allowed_hosts)


def _host_and_port(value: str) -> tuple[str, int | None]:
    """Normalize a Host header into a lowercase hostname and optional port."""
    value = value.strip()
    if not value:
        return "", None
    if value.startswith("["):
        end = value.find("]")
        if end == -1:
            return value.lower(), None
        hostname = value[1:end].lower()
        remainder = value[end + 1 :]
        if not remainder.startswith(":"):
            return hostname, None
        port_text = remainder[1:]
    elif value.count(":") == 1:
        hostname, port_text = value.split(":", 1)
        hostname = hostname.lower()
    else:
        return value.lower(), None
    try:
        port = int(port_text)
    except ValueError:
        return hostname, None
    if not 0 < port <= 65535:
        return hostname, None
    return hostname, port

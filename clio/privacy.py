"""Privacy helpers for log / session output (GAP-P2-05)."""

from __future__ import annotations

import re

# Bearer / raw API-looking tokens (sk-..., long hex/base64-ish secrets).
_RE_BEARER = re.compile(r"(?i)(authorization:\s*bearer\s+)(\S+)")
_RE_API_KEY_ASSIGN = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|access_key)\b(\s*[=:]\s*)([^\s,;\"']+)"
)
_RE_SK_TOKEN = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,})\b")
_RE_URL_CREDS = re.compile(r"(://)([^:@/\s]+):([^@/\s]+)(@)")
_MAX_LINE = 4096


def redact_sensitive(text: str, *, max_len: int = _MAX_LINE) -> str:
    """Mask credentials and truncate oversized lines before they hit durable logs."""
    if not text:
        return text
    out = text
    out = _RE_URL_CREDS.sub(r"\1***:***\4", out)
    out = _RE_BEARER.sub(r"\1***", out)
    out = _RE_API_KEY_ASSIGN.sub(r"\1\2***", out)
    out = _RE_SK_TOKEN.sub("sk-***", out)
    if len(out) > max_len:
        out = out[:max_len] + f"…[已截断 {len(text) - max_len} 字符]"
    return out

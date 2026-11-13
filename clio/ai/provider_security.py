from __future__ import annotations

import hmac
import ipaddress
import os
import re
from urllib.parse import urlsplit

PROVIDER_KEY_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]*_API_KEY$")
_MASKED_SECRET_VALUES = frozenset({"***", "********", "••••••••••"})
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")
_FNV_OFFSET_BASIS = 0x811C9DC5
_FNV_PRIME = 0x01000193

_DEFAULT_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com",
    "openai": "https://api.openai.com/v1",
    "openai_compat": "https://api.openai.com/v1",
    "ollama": "http://localhost:11434/v1",
}


def _fnv1a_code_points(value: str) -> str:
    result = _FNV_OFFSET_BASIS
    for char in value:
        result ^= ord(char)
        result = (result * _FNV_PRIME) & 0xFFFFFFFF
    return f"{result:08x}"


def provider_env_name(name: str) -> str:
    """Return the canonical API-key environment variable for a provider name."""
    trimmed = str(name or "").strip()
    slug = re.sub(r"[^A-Z0-9]+", "_", trimmed.upper()).strip("_")
    base = slug or "CUSTOM_PROVIDER"
    if base[0].isdigit():
        base = f"PROVIDER_{base}"
    if _NON_ASCII_RE.search(trimmed):
        return f"{base}_{_fnv1a_code_points(trimmed)}_API_KEY"
    return f"{base}_API_KEY"


def normalize_provider_key_env(value: object) -> str:
    """Normalize a configured API-key environment variable name."""
    return value.strip() if isinstance(value, str) else ""


def is_provider_key_env_name(value: str) -> bool:
    """Return whether value follows the provider API-key env naming contract."""
    return bool(PROVIDER_KEY_ENV_RE.fullmatch(str(value or "").strip()))


def provider_key_env_error(provider_name: str, value: object) -> str | None:
    """Validate that a provider can only read its own canonical credential slot."""
    if value is not None and not isinstance(value, str):
        return "api_key_env must be an uppercase *_API_KEY environment variable name"
    env_name = normalize_provider_key_env(value)
    if not env_name:
        return None
    if not is_provider_key_env_name(env_name):
        return "api_key_env must be an uppercase *_API_KEY environment variable name"
    expected = provider_env_name(provider_name)
    if env_name != expected:
        return f"api_key_env for provider '{provider_name}' must be {expected}"
    return None


def effective_provider_base_url(provider_type: str, base_url: str) -> str:
    """Resolve the URL the provider will actually contact."""
    normalized_type = str(provider_type or "gemini").strip().lower()
    raw_url = str(base_url or "").strip()
    if raw_url:
        return raw_url.rstrip("/")
    return _DEFAULT_BASE_URLS.get(normalized_type, _DEFAULT_BASE_URLS["openai"])


def _is_loopback_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback


def provider_base_url_error(provider_type: str, base_url: str) -> str | None:
    """Return why a provider connection-test URL is unsafe, if it is."""
    effective = effective_provider_base_url(provider_type, base_url)
    if not effective:
        return "provider URL is required"
    if any(char.isspace() or ord(char) < 0x20 for char in effective) or "\\" in effective:
        return "provider URL is invalid"
    try:
        parsed = urlsplit(effective)
        _port = parsed.port
    except ValueError:
        return "provider URL is invalid"
    if parsed.username is not None or parsed.password is not None:
        return "provider URL must not contain embedded credentials"
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host or (parsed.path and not parsed.path.startswith("/")) or parsed.query or parsed.fragment:
        return "provider URL is invalid"
    if parsed.scheme == "https":
        return None
    if parsed.scheme == "http" and _is_loopback_host(host):
        return None
    return "provider connection tests require HTTPS or a loopback HTTP URL"


def provider_binding_signature(provider: dict) -> tuple[str, ...]:
    """Return every value that changes the credential target of a provider."""
    provider_type = str(provider.get("type", "gemini") or "gemini").strip().lower()
    effective = effective_provider_base_url(provider_type, str(provider.get("base_url", "") or ""))
    env_name = normalize_provider_key_env(provider.get("api_key_env", ""))
    try:
        parsed = urlsplit(effective)
        port = parsed.port
        if parsed.scheme.lower() == "https" and port == 443 or parsed.scheme.lower() == "http" and port == 80:
            port = None
        if parsed.scheme and parsed.hostname:
            return (
                provider_type,
                parsed.scheme.lower(),
                (parsed.hostname or "").rstrip(".").lower(),
                str(port or ""),
                parsed.path or "/",
                parsed.query,
                parsed.fragment,
                env_name,
            )
    except ValueError:
        pass
    return (provider_type, "invalid", effective, "", "", "", "", env_name)


def _resolved_provider_secret(provider: dict) -> str:
    env_name = normalize_provider_key_env(provider.get("api_key_env", ""))
    if env_name:
        env_value = os.environ.get(env_name, "")
        if env_value:
            return env_value
    value = str(provider.get("api_key", "") or "")
    return "" if value in _MASKED_SECRET_VALUES else value


def _supplied_secret(provider: dict) -> str:
    value = provider.get("api_key", "")
    if not isinstance(value, str):
        return ""
    return "" if value in _MASKED_SECRET_VALUES else value


def provider_rebind_error(provider_name: str, previous: dict, candidate: dict) -> str | None:
    """Require proof of possession when a credential target is introduced or changed.

    Existing credentials must be proven against the old binding. When the old
    binding had no secret, candidate credentials must be proven against the new
    binding so a provider cannot silently reuse an existing shell/.env secret.
    """
    if provider_binding_signature(previous) == provider_binding_signature(candidate):
        return None

    old_secret = _resolved_provider_secret(previous) if previous else ""
    candidate_secret = _resolved_provider_secret(candidate)
    supplied = _supplied_secret(candidate)
    expected_secret = old_secret or candidate_secret
    if expected_secret and supplied and hmac.compare_digest(supplied, expected_secret):
        return None
    if not expected_secret:
        return None
    return (
        f"provider '{provider_name}' changed its URL or API-key environment; "
        "re-enter the current API key to confirm the new credential target"
    )

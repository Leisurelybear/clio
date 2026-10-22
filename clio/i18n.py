"""Shared Python/JSON message catalog for user-facing server and CLI copy."""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from clio.resources import find_resource

SUPPORTED_LOCALES: tuple[str, ...] = ("zh-CN", "en")
DEFAULT_LOCALE = "zh-CN"
_FALLBACK_LOCALES: tuple[str, ...] = ("en", "zh-CN")
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")

logger = logging.getLogger("clio.i18n")


def normalize_locale(value: str | None, default: str = DEFAULT_LOCALE) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    if raw == "zh" or raw == "zh-cn" or raw.startswith("zh-"):
        return "zh-CN"
    if raw == "en" or raw.startswith("en-"):
        return "en"
    return default


_CATALOG_RESOURCES = (
    "ui/static/locales/{locale}.json",
    "locales/{locale}.json",
)


def _read_catalog(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("failed to load i18n catalog: %s", path, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(key, str) and isinstance(value, str)}


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def _load_catalog(locale: str) -> dict[str, str]:
    """Merge shared UI keys first, then the backend catalog (backend wins)."""
    merged: dict[str, str] = {}
    found = False
    for template in _CATALOG_RESOURCES:
        path = find_resource(template.format(locale=locale))
        if path is None:
            continue
        found = True
        merged.update(_read_catalog(path))
    if not found:
        logger.debug("i18n catalog missing: %s", locale)
    return merged


def _interpolate(template: str, params: dict[str, Any] | None) -> str:
    if not params:
        return template

    def replace(match: re.Match[str]) -> str:
        value = params.get(match.group(1))
        return match.group(0) if value is None else str(value)

    return _PLACEHOLDER.sub(replace, template)


_ACTIVE_LOCALE: str | None = None


def set_active_locale(value: str | None) -> str | None:
    """Set the process-wide locale used when t() receives no explicit locale.

    Callers pass config.ui.language. An empty value clears the override so
    CLIO_LANG and the default locale take over again.
    """
    global _ACTIVE_LOCALE
    raw = str(value or "").strip()
    _ACTIVE_LOCALE = normalize_locale(raw) if raw else None
    return _ACTIVE_LOCALE


def get_active_locale() -> str | None:
    return _ACTIVE_LOCALE


def _resolve_locale(locale: str | None) -> str:
    """Explicit argument, then CLIO_LANG, then the active locale, then default."""
    if locale:
        return normalize_locale(locale)
    env = os.environ.get("CLIO_LANG")
    if env and env.strip():
        return normalize_locale(env)
    if _ACTIVE_LOCALE:
        return _ACTIVE_LOCALE
    return DEFAULT_LOCALE


def t(
    key: str,
    params: dict[str, Any] | None = None,
    *,
    locale: str | None = None,
    fallback: str | None = None,
) -> str:
    """Translate key; return fallback (else the key) when nothing matches."""
    requested = _resolve_locale(locale)
    seen: set[str] = set()
    for candidate in (requested, *_FALLBACK_LOCALES):
        if candidate in seen:
            continue
        seen.add(candidate)
        value = _load_catalog(candidate).get(str(key))
        if value is not None:
            return _interpolate(value, params)
    return str(key) if fallback is None else fallback


def catalog_path(locale: str) -> Path | None:
    """Return the backend catalog path, falling back to the shared UI catalog."""
    normalized = normalize_locale(locale)
    return find_resource(f"locales/{normalized}.json") or find_resource(f"ui/static/locales/{normalized}.json")


def _reset_cache_for_tests() -> None:
    global _ACTIVE_LOCALE
    _load_catalog.cache_clear()
    _ACTIVE_LOCALE = None

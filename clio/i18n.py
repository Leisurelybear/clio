"""Shared Python/JSON message catalog for user-facing server and CLI copy."""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
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


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def _load_catalog(locale: str) -> dict[str, str]:
    path = find_resource(f"ui/static/locales/{locale}.json")
    if path is None:
        logger.debug("i18n catalog missing: %s", locale)
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("failed to load i18n catalog: %s", path, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if isinstance(key, str) and isinstance(value, str)}


def _interpolate(template: str, params: dict[str, Any] | None) -> str:
    if not params:
        return template

    def replace(match: re.Match[str]) -> str:
        value = params.get(match.group(1))
        return match.group(0) if value is None else str(value)

    return _PLACEHOLDER.sub(replace, template)


def t(key: str, params: dict[str, Any] | None = None, *, locale: str | None = None) -> str:
    requested = normalize_locale(locale or os.environ.get("CLIO_LANG"))
    seen: set[str] = set()
    for candidate in (requested, *_FALLBACK_LOCALES):
        if candidate in seen:
            continue
        seen.add(candidate)
        value = _load_catalog(candidate).get(str(key))
        if value is not None:
            return _interpolate(value, params)
    return str(key)


def catalog_path(locale: str):
    return find_resource(f"ui/static/locales/{normalize_locale(locale)}.json")


def _reset_cache_for_tests() -> None:
    _load_catalog.cache_clear()

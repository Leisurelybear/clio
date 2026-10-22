"""Tests for the shared Python message catalog."""

from __future__ import annotations

import clio.i18n as i18n
from clio.resources import find_resource


def test_catalog_resources_are_bundled():
    assert find_resource("ui/static/locales/zh-CN.json") is not None
    assert find_resource("ui/static/locales/en.json") is not None


def test_normalize_locale_accepts_short_and_regional_tags():
    assert i18n.normalize_locale("zh") == "zh-CN"
    assert i18n.normalize_locale("zh-Hans") == "zh-CN"
    assert i18n.normalize_locale("en-US") == "en"
    assert i18n.normalize_locale("fr") == "zh-CN"
    assert i18n.normalize_locale(None) == "zh-CN"


def test_real_catalogs_translate_interpolate_and_fall_back():
    i18n._reset_cache_for_tests()
    assert i18n.t("app.title", locale="zh-CN") == "Vlog 剪辑 UI"
    assert i18n.t("app.title", locale="en") == "Vlog Editing UI"
    assert i18n.t("notifications.unread_count", {"count": 4}, locale="en") == "Notifications, 4 unread"
    assert i18n.t("notifications.unread_count", {"count": 4}, locale="zh-CN") == "通知中心，有 4 条未读"


def test_missing_key_returns_key_and_unknown_locale_uses_default(monkeypatch):
    monkeypatch.setattr(i18n, "_load_catalog", lambda locale: {})
    assert i18n.t("missing.key", locale="en") == "missing.key"
    assert i18n.t("missing.key", locale="fr") == "missing.key"


def test_fallback_prefers_english_then_chinese(monkeypatch):
    catalogs = {
        "en": {"shared": "Shared"},
        "zh-CN": {"shared": "共享", "only.zh": "仅中文"},
    }
    monkeypatch.setattr(i18n, "_load_catalog", lambda locale: catalogs.get(locale, {}))
    assert i18n.t("shared", locale="en") == "Shared"
    assert i18n.t("only.zh", locale="en") == "仅中文"
    assert i18n.t("only.zh", locale="zh-CN") == "仅中文"


def test_clio_lang_env_sets_default_locale(monkeypatch):
    monkeypatch.setenv("CLIO_LANG", "en")
    i18n._reset_cache_for_tests()
    assert i18n.t("app.title") == "Vlog Editing UI"

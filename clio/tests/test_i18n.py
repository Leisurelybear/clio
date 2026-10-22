"""Tests for the shared Python message catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import clio.i18n as i18n
from clio.resources import find_resource

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    i18n._reset_cache_for_tests()
    yield
    i18n._reset_cache_for_tests()


def test_catalog_resources_are_bundled():
    assert find_resource("ui/static/locales/zh-CN.json") is not None
    assert find_resource("ui/static/locales/en.json") is not None
    assert find_resource("locales/zh-CN.json") is not None
    assert find_resource("locales/en.json") is not None


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
    assert i18n.t("app.title") == "Vlog Editing UI"


def test_backend_catalog_keys_have_locale_parity():
    catalogs = {
        locale: json.loads((REPO_ROOT / "clio" / "locales" / f"{locale}.json").read_text(encoding="utf-8"))
        for locale in i18n.SUPPORTED_LOCALES
    }

    assert set(catalogs["zh-CN"]) == set(catalogs["en"])


def test_backend_catalog_overrides_ui_catalog_and_keeps_shared_keys(tmp_path, monkeypatch):
    ui_catalog = tmp_path / "ui.json"
    backend_catalog = tmp_path / "backend.json"
    ui_catalog.write_text('{"app.title": "UI title", "ui.only": "UI only"}', encoding="utf-8")
    backend_catalog.write_text('{"app.title": "Backend title", "backend.only": "Backend only"}', encoding="utf-8")
    resources = {
        "ui/static/locales/en.json": ui_catalog,
        "locales/en.json": backend_catalog,
    }

    monkeypatch.setattr(i18n, "find_resource", lambda relative: resources.get(relative))

    assert i18n.t("app.title", locale="en") == "Backend title"
    assert i18n.t("ui.only", locale="en") == "UI only"
    assert i18n.t("backend.only", locale="en") == "Backend only"


def test_missing_parameters_leave_placeholders_visible():
    assert i18n.t("task.rerun_title", {"task": "analyze"}, locale="en") == "Rerun analyze: {video}"


def test_unknown_locale_behaves_like_the_default_locale():
    assert i18n.t("cli.description", locale="fr") == i18n.t("cli.description", locale="zh-CN")


def test_unknown_clio_lang_uses_default_locale(monkeypatch):
    monkeypatch.setenv("CLIO_LANG", "fr")

    assert i18n.t("cli.description") == i18n.t("cli.description", locale="zh-CN")


def test_catalog_path_prefers_the_backend_catalog():
    assert i18n.catalog_path("en") == find_resource("locales/en.json")

"""Regression test: ASR provider names follow the active locale."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from clio import i18n
from clio.asr.aliyun import AliyunASRProvider
from clio.asr.factory import list_providers
from clio.asr.readiness import check_asr_ready


def test_provider_names_are_translated_at_read_time(monkeypatch):
    monkeypatch.delenv("CLIO_LANG", raising=False)
    previous = i18n.get_active_locale()
    try:
        i18n.set_active_locale("en")
        names = {caps.id: caps.display_name for caps in list_providers()}
        assert names["aliyun"] == i18n.t("asr.aliyun.display_name", locale="en")
        assert names["local"] == i18n.t("asr.local.display_name", locale="en")

        i18n.set_active_locale("zh-CN")
        names = {caps.id: caps.display_name for caps in list_providers()}
        assert names["aliyun"] == i18n.t("asr.aliyun.display_name", locale="zh-CN")
    finally:
        i18n.set_active_locale(previous)


def test_readiness_uses_active_locale_name(monkeypatch):
    monkeypatch.delenv("CLIO_LANG", raising=False)
    previous = i18n.get_active_locale()
    try:
        i18n.set_active_locale("en")
        with patch.object(AliyunASRProvider, "readiness_error", return_value=None):
            readiness = check_asr_ready(MagicMock(), engine="aliyun", environ={})
        assert readiness.display_name == i18n.t("asr.aliyun.display_name", locale="en")
    finally:
        i18n.set_active_locale(previous)

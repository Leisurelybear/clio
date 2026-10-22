"""Web UI API payloads resolve through the shared message catalog."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clio import i18n

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_i18n():
    i18n._reset_cache_for_tests()
    yield
    i18n._reset_cache_for_tests()


def test_api_copy_is_translated_per_locale():
    assert i18n.t("api.plan_missing", {"path": "/p/plan.json"}, locale="en") == "Plan file does not exist: /p/plan.json"
    assert i18n.t("api.unauthorized", locale="en") == "Unauthorized: a valid API token is required"
    assert i18n.t("api.plan_missing", {"path": "/p/plan.json"}, locale="zh-CN") == "规划文件不存在: /p/plan.json"
    assert i18n.t("api.task_already_running", locale="zh-CN") == "另一个任务正在运行"


def test_api_copy_shares_one_key_set_and_never_reuses_chinese():
    zh = json.loads((REPO_ROOT / "clio" / "locales" / "zh-CN.json").read_text(encoding="utf-8"))
    en = json.loads((REPO_ROOT / "clio" / "locales" / "en.json").read_text(encoding="utf-8"))
    api_keys = {key for key in zh if key.startswith("api.")}
    assert api_keys, "expected api.* keys in the backend catalog"
    assert api_keys <= set(en)
    assert not {key for key in api_keys if zh[key] == en[key]}


def test_api_copy_borrows_shared_ui_keys():
    assert i18n.t("notifications.default_title", locale="en") == "Notification"
    assert i18n.t("transcription.engine_local", locale="en") == "Local faster-whisper"
    assert i18n.t("run.step.compress.label", locale="zh-CN") == "压缩原视频"


def test_run_preview_step_labels_follow_the_active_locale(monkeypatch):
    from clio.ui.services import run_preview

    monkeypatch.setenv("CLIO_LANG", "en")
    assert run_preview._step_label("compress") == "Compress footage"
    assert run_preview._step_label("label") == "Burn index labels"
    assert run_preview._step_label("unknown") == "unknown"

    monkeypatch.setenv("CLIO_LANG", "zh-CN")
    assert run_preview._step_label("compress") == "压缩原视频"

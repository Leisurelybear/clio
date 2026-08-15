from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from clio.prompts import (
    ANALYZE_PROMPT,
    PLAN_PROMPT,
    REFINE_SCRIPT_PROMPT,
    REFINE_TEXT_PROMPT,
    SCRIPT_PROMPT,
    load_prompt,
    render_prompt_template,
)


def test_load_prompt_uses_project_override(tmp_path):
    prompt_dir = tmp_path / "templates" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "analyze_prompt.md").write_text("project override", encoding="utf-8")

    assert load_prompt("ANALYZE_PROMPT", "default", tmp_path) == "project override"


def test_load_prompt_supports_txt_override(tmp_path):
    prompt_dir = tmp_path / "templates" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "ANALYZE_PROMPT.txt").write_text("txt override", encoding="utf-8")

    assert load_prompt("ANALYZE_PROMPT", "default", tmp_path) == "txt override"


def test_load_prompt_ignores_empty_override(tmp_path):
    prompt_dir = tmp_path / "templates" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "ANALYZE_PROMPT.md").write_text("  \n", encoding="utf-8")

    assert load_prompt("ANALYZE_PROMPT", "default", tmp_path) == "default"


def test_load_prompt_caches_override_until_file_changes(tmp_path, monkeypatch):
    prompt_dir = tmp_path / "templates" / "prompts"
    prompt_dir.mkdir(parents=True)
    prompt_file = prompt_dir / "ANALYZE_PROMPT.md"
    prompt_file.write_text("cached prompt", encoding="utf-8")
    read_count = 0
    original_read_text = Path.read_text

    def counted_read_text(self, *args, **kwargs):
        nonlocal read_count
        if self == prompt_file:
            read_count += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)

    assert load_prompt("ANALYZE_PROMPT", "default", tmp_path) == "cached prompt"
    assert load_prompt("ANALYZE_PROMPT", "default", tmp_path) == "cached prompt"
    assert read_count == 1

    prompt_file.write_text("updated prompt", encoding="utf-8")
    changed_at = time.time() + 2
    os.utime(prompt_file, (changed_at, changed_at))

    assert load_prompt("ANALYZE_PROMPT", "default", tmp_path) == "updated prompt"
    assert read_count == 2


def test_render_prompt_template_allows_json_braces():
    template = '返回 JSON: {"index": "{index}", "items": []}'

    result = render_prompt_template("SCRIPT_PROMPT", template, index="001")

    assert result == '返回 JSON: {"index": "001", "items": []}'


def test_render_prompt_template_rejects_unknown_placeholder():
    with pytest.raises(ValueError, match="unknown"):
        render_prompt_template("SCRIPT_PROMPT", "hello {missing}", index="001")


def test_analyze_prompt_requires_high_detail_timeline():
    assert "镜头运动" in ANALYZE_PROMPT
    assert "实际能看到" in ANALYZE_PROMPT
    assert "禁止编造" in ANALYZE_PROMPT


def test_analyze_prompt_requires_specific_summary_and_mood():
    assert "独有的具体内容" in ANALYZE_PROMPT
    assert "空泛" in ANALYZE_PROMPT


def test_analyze_prompt_highlights_support_object():
    assert '"start"' in ANALYZE_PROMPT
    assert '"reason"' in ANALYZE_PROMPT
    assert "纯字符串" in ANALYZE_PROMPT


def test_analyze_prompt_cover_timestamp_rules():
    assert "主体清晰" in ANALYZE_PROMPT
    assert "真实存在" in ANALYZE_PROMPT


def test_plan_prompt_requires_precise_timeline_and_budget():
    assert "use_timeline" in PLAN_PROMPT
    assert "越界" in PLAN_PROMPT
    assert "target_duration_sec" in PLAN_PROMPT
    assert "预算" in PLAN_PROMPT


def test_plan_prompt_reason_needs_evidence():
    assert "叙事作用" in PLAN_PROMPT
    assert "画面依据" in PLAN_PROMPT


def test_plan_prompt_voiceover_hint_specific():
    assert "空泛" in PLAN_PROMPT
    assert "voiceover_hint" in PLAN_PROMPT


def test_script_prompt_binds_to_timeline_details():
    assert "timeline_text" in SCRIPT_PROMPT
    assert "具体地点" in SCRIPT_PROMPT
    assert "禁止" in SCRIPT_PROMPT


def test_script_prompt_duration_matches_timeline():
    assert "duration_hint_sec" in SCRIPT_PROMPT
    assert "匹配" in SCRIPT_PROMPT


def test_script_prompt_has_clear_structure():
    assert "开头" in SCRIPT_PROMPT
    assert "过程" in SCRIPT_PROMPT
    assert "感受" in SCRIPT_PROMPT
    assert "过渡" in SCRIPT_PROMPT


def test_refine_text_prompt_is_conservative():
    assert "只改" in REFINE_TEXT_PROMPT
    assert "确凿" in REFINE_TEXT_PROMPT
    assert "不是重写" in REFINE_TEXT_PROMPT


def test_refine_text_prompt_checks_cross_field_consistency():
    assert "一致性" in REFINE_TEXT_PROMPT
    assert "location" in REFINE_TEXT_PROMPT
    assert "cover_timestamp" in REFINE_TEXT_PROMPT


def test_refine_script_prompt_is_conservative():
    assert "只改" in REFINE_SCRIPT_PROMPT
    assert "确凿" in REFINE_SCRIPT_PROMPT


def test_refine_script_prompt_keeps_reference():
    assert "素材分析" in REFINE_SCRIPT_PROMPT

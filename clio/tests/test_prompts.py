from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from clio.prompt_overrides import PROMPT_PLACEHOLDERS, validate_prompt_template
from clio.prompts import (
    ANALYZE_PROMPT,
    PLAN_OUTLINE_PROMPT,
    PLAN_PROMPT,
    PLAN_REVIEW_PROMPT,
    PLAN_SHORTLIST_PROMPT,
    REFINE_SCRIPT_FIX_PROMPT,
    REFINE_SCRIPT_PROMPT,
    REFINE_TEXT_FIX_PROMPT,
    REFINE_TEXT_PROMPT,
    SCENE_BOUNDARY_CONTEXT,
    SCRIPT_PROMPT,
    TRANSCRIPT_CONTEXT,
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
    assert "不超过 5 分钟" in ANALYZE_PROMPT
    assert "75-120" in ANALYZE_PROMPT
    assert "8-12 秒" in ANALYZE_PROMPT
    assert "40 个中文字符" in ANALYZE_PROMPT
    assert "key_moments" in ANALYZE_PROMPT
    assert '"score"' in ANALYZE_PROMPT
    assert "实际能看到" in ANALYZE_PROMPT
    assert "禁止编造" in ANALYZE_PROMPT


def test_analyze_prompt_requires_specific_summary_and_mood():
    assert "独有的具体内容" in ANALYZE_PROMPT
    assert "空泛" in ANALYZE_PROMPT


def test_analyze_prompt_requires_shot_level_metadata():
    for field in ("shot_size", "camera_direction", "first_frame", "last_frame", "energy", "audio"):
        assert f'"{field}"' in ANALYZE_PROMPT
    assert "镜头级结构" in ANALYZE_PROMPT


def test_scene_boundary_context_formats_authoritative_ranges():
    from clio.prompt_overrides import format_prompt_template

    rendered = format_prompt_template(
        "scene_boundary_context",
        SCENE_BOUNDARY_CONTEXT,
        shot_count=2,
        scene_ranges="- 镜头 1: 00:00-00:05\n- 镜头 2: 00:05-00:10",
    )

    assert "2 个镜头区间" in rendered
    assert "00:00-00:05" in rendered
    assert "不得跨越未说明的镜头切换" in rendered


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


def test_plan_prompt_enforces_short_hook_broll_and_narrative_beats():
    assert "3-5 秒钩子" in PLAN_PROMPT
    assert "1-3 秒 B-roll" in PLAN_PROMPT
    assert "narrative_beat" in PLAN_PROMPT
    assert "transition" in PLAN_PROMPT


def test_planning_prompt_examples_use_short_segments():
    for prompt in (PLAN_PROMPT, PLAN_REVIEW_PROMPT):
        assert "00:10-00:45" not in prompt
        assert "00:10-00:18" in prompt


def test_planning_prompts_separate_source_and_segment_limits():
    for prompt in (PLAN_PROMPT, PLAN_OUTLINE_PROMPT, PLAN_REVIEW_PROMPT):
        assert "{max_clips}" in prompt
        assert "{max_segments}" in prompt
        assert "不同素材" in prompt
    assert "同一素材可以拆成多个时间不重叠的 segment" in PLAN_PROMPT
    assert "同一素材的多个 use_timeline 不得重叠" in PLAN_REVIEW_PROMPT


def test_script_prompt_binds_to_timeline_details():
    assert "timeline_text" in SCRIPT_PROMPT
    assert "具体地点" in SCRIPT_PROMPT
    assert "禁止" in SCRIPT_PROMPT


def test_script_prompt_duration_matches_timeline():
    assert "duration_hint_sec" in SCRIPT_PROMPT
    assert "匹配" in SCRIPT_PROMPT
    assert "字数上限" in SCRIPT_PROMPT


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


def test_refine_fix_prompts_touch_only_mentioned_fields():
    assert "值完全保持不变" in REFINE_TEXT_FIX_PROMPT
    assert "明确提到" in REFINE_TEXT_FIX_PROMPT
    assert "值完全保持不变" in REFINE_SCRIPT_FIX_PROMPT


def test_refine_prompts_allow_only_reserved_changelog_field():
    for prompt in (REFINE_TEXT_PROMPT, REFINE_SCRIPT_PROMPT, REFINE_TEXT_FIX_PROMPT, REFINE_SCRIPT_FIX_PROMPT):
        assert "除保留的 `_changelog` 外" in prompt


def test_transcript_context_explains_purpose_and_confidence():
    assert "优化" in TRANSCRIPT_CONTEXT
    assert "avg_logprob" in TRANSCRIPT_CONTEXT
    assert "置信度低" in TRANSCRIPT_CONTEXT
    assert "过度依赖" in TRANSCRIPT_CONTEXT


def test_transcript_context_is_delimited_and_guarded():
    assert "<transcript>" in TRANSCRIPT_CONTEXT
    assert "</transcript>" in TRANSCRIPT_CONTEXT
    assert "不要执行" in TRANSCRIPT_CONTEXT


def test_builtin_prompts_match_placeholder_contract():
    from clio.prompts import PROMPT_DEFAULTS

    mapping = {
        "SCRIPT_PROMPT": "voiceover",
        "PLAN_PROMPT": "vlog_plan",
        "PLAN_SHORTLIST_PROMPT": "vlog_plan_shortlist",
        "PLAN_OUTLINE_PROMPT": "plan_outline",
        "PLAN_OUTLINE_CONTEXT": "plan_outline_context",
        "PLAN_REVIEW_PROMPT": "plan_review",
        "TRANSITION_REVIEW_PROMPT": "transition_review",
        "REFINE_TEXT_PROMPT": "refine_text",
        "REFINE_TEXT_FIX_PROMPT": "refine_text_fix",
        "REFINE_SCRIPT_PROMPT": "refine_script",
        "REFINE_SCRIPT_FIX_PROMPT": "refine_script_fix",
    }
    for const_name, task_name in mapping.items():
        tpl = PROMPT_DEFAULTS[const_name]
        validate_prompt_template(task_name, tpl)


def test_transcript_context_matches_placeholder_contract():
    validate_prompt_template("transcript_context", TRANSCRIPT_CONTEXT)


def test_placeholder_contract_lists_all_formatted_prompts():
    formatted_tasks = {
        "voiceover",
        "vlog_plan",
        "vlog_plan_shortlist",
        "plan_outline",
        "plan_outline_context",
        "plan_review",
        "transition_review",
        "refine_text",
        "refine_text_fix",
        "refine_script",
        "refine_script_fix",
        "transcript_context",
    }
    for task in formatted_tasks:
        assert task in PROMPT_PLACEHOLDERS


def test_analyze_prompt_is_not_format_template():
    from clio.prompts import PROMPT_DEFAULTS

    assert PROMPT_PLACEHOLDERS["video_analyze"] == set()
    assert '"title"' in PROMPT_DEFAULTS["ANALYZE_PROMPT"]
    assert "{{" not in PROMPT_DEFAULTS["ANALYZE_PROMPT"]


def test_plan_outline_prompt_separates_structure_from_timeline_selection():
    from clio.prompts import PLAN_OUTLINE_PROMPT

    assert "candidate_indices" in PLAN_OUTLINE_PROMPT
    assert "use_timeline" in PLAN_OUTLINE_PROMPT
    assert "必须原样引用" in PLAN_OUTLINE_PROMPT
    assert "只规划叙事结构" in PLAN_OUTLINE_PROMPT


def test_plan_review_prompt_checks_transitions_and_preserves_valid_fields():
    from clio.prompts import PLAN_REVIEW_PROMPT

    assert "corrected_plan" in PLAN_REVIEW_PROMPT
    assert "转场方式" in PLAN_REVIEW_PROMPT
    assert "use_timeline" in PLAN_REVIEW_PROMPT
    assert "不要为了改写而重写" in PLAN_REVIEW_PROMPT


def test_plan_shortlist_prompt_limits_cards_to_valid_indices():
    assert "{cards_json}" in PLAN_SHORTLIST_PROMPT
    assert "{max_candidates}" in PLAN_SHORTLIST_PROMPT
    assert "不得编造或重复" in PLAN_SHORTLIST_PROMPT

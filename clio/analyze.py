from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clio.ai.base import AIResponse, TaskName
from clio.ai.factory import get_task_provider, get_video_provider
from clio.config import AppConfig
from clio.log import format_size, timed
from clio.prompt_overrides import format_prompt_template, resolve_prompt_template
from clio.prompts import (
    ANALYZE_PROMPT,
    PLAN_PROMPT,
    REFINE_SCRIPT_FIX_PROMPT,
    REFINE_SCRIPT_PROMPT,
    REFINE_TEXT_FIX_PROMPT,
    REFINE_TEXT_PROMPT,
    SCRIPT_PROMPT,
    TRANSCRIPT_CONTEXT,
)
from clio.utils import extract_json

_trip_context_cache: dict[str, str] = {}


def _read_trip_context(project_dir: str) -> str:
    """读取 trip_context.md，按项目目录 + 文件 mtime 缓存。

    查找优先级：
    1. <project_dir>/templates/trip_context.md（项目级）
    2. <default_package>/templates/trip_context.md（包默认）
    """
    project_path = Path(project_dir) / "templates" / "trip_context.md"
    if project_path.is_file():
        mtime = project_path.stat().st_mtime
        key = f"{project_dir}@{mtime}"
        if key in _trip_context_cache:
            return _trip_context_cache[key]
        text = project_path.read_text(encoding="utf-8").strip()
        if text:
            _trip_context_cache[key] = text
            return text
    default_path = Path(__file__).parent.parent / "templates" / "trip_context.md"
    if default_path.is_file():
        mtime = default_path.stat().st_mtime
        key = f"{project_dir}@default@{mtime}"
        if key in _trip_context_cache:
            return _trip_context_cache[key]
        text = default_path.read_text(encoding="utf-8").strip()
        if text:
            _trip_context_cache[key] = text
            return text
    _trip_context_cache[f"{project_dir}@empty"] = ""
    return ""


def _coerce_str(value: Any, default: str) -> tuple[str, bool]:
    if isinstance(value, str):
        return value, True
    return default, False


def _coerce_number(value: Any, default: float) -> tuple[float, bool]:
    if isinstance(value, bool):
        return default, False
    try:
        return float(value), True
    except (TypeError, ValueError):
        return default, False


def _coerce_list(value: Any, default: float) -> tuple[list, bool]:
    if isinstance(value, list):
        return value, True
    return [], False


def _validate_analysis(data: dict, source: str) -> dict:
    """校验 AI 分析结果，缺失字段补默认值并对错误类型告警归一。"""
    data = copy.deepcopy(data)
    required = {"title", "summary", "timeline"}
    missing = required - data.keys()
    if missing:
        print(f"  [警告] {source}: AI 返回缺少字段 {missing}，使用默认值")
    data.setdefault("title", Path(source).stem)
    data.setdefault("summary", "")
    data.setdefault("timeline", [])
    data.setdefault("highlights", [])
    data.setdefault("location", "未知")
    data.setdefault("mood", "")
    data.setdefault("suggested_use", "")
    data.setdefault("cover_timestamp", "")
    data.setdefault("_confidence", 0.0)

    for key, default in (
        ("title", Path(source).stem),
        ("summary", ""),
        ("location", "未知"),
        ("mood", ""),
        ("suggested_use", ""),
        ("cover_timestamp", ""),
    ):
        value, ok = _coerce_str(data[key], default)
        if not ok:
            print(f"  [警告] {source}: 字段 {key} 类型非法（{type(data[key]).__name__}），重置为默认值")
        data[key] = value
    for key, default in (("timeline", []), ("highlights", [])):
        value, ok = _coerce_list(data[key], default)
        if not ok:
            print(f"  [警告] {source}: 字段 {key} 类型非法（{type(data[key]).__name__}），重置为空列表")
        data[key] = value
    value, ok = _coerce_number(data["_confidence"], 0.0)
    if not ok:
        print(f"  [警告] {source}: 字段 _confidence 类型非法，重置为 0.0")
    data["_confidence"] = value
    return data


def _validate_voiceover(data: dict, source: str) -> dict:
    """校验 AI 口播文案结果。"""
    data = copy.deepcopy(data)
    required = {"voiceover", "title"}
    missing = required - data.keys()
    if missing:
        print(f"  [警告] {source}: AI 返回缺少字段 {missing}，使用默认值")
    data.setdefault("title", Path(source).stem)
    data.setdefault("voiceover", "")
    data.setdefault("duration_hint_sec", 20)
    data.setdefault("edit_tip", "")
    data.setdefault("_confidence", 0.0)

    for key, default in (("title", Path(source).stem), ("voiceover", ""), ("edit_tip", "")):
        value, ok = _coerce_str(data[key], default)
        if not ok:
            print(f"  [警告] {source}: 字段 {key} 类型非法，重置为默认值")
        data[key] = value
    value, ok = _coerce_number(data["duration_hint_sec"], 20)
    if not ok:
        print(f"  [警告] {source}: 字段 duration_hint_sec 类型非法，重置为 20")
    data["duration_hint_sec"] = value
    value, ok = _coerce_number(data["_confidence"], 0.0)
    if not ok:
        print(f"  [警告] {source}: 字段 _confidence 类型非法，重置为 0.0")
    data["_confidence"] = value
    return data


def _validate_plan(data: dict, source: str) -> dict:
    """校验 AI vlog 规划结果。"""
    data = copy.deepcopy(data)
    required = {"day_title", "sequence"}
    missing = required - data.keys()
    if missing:
        print(f"  [警告] {source}: AI 返回缺少字段 {missing}，使用默认值")
    data.setdefault("day_title", source)
    data.setdefault("theme", "")
    data.setdefault("total_estimated_sec", 180)
    data.setdefault("sequence", [])
    data.setdefault("opening_tip", "")
    data.setdefault("ending_tip", "")
    data.setdefault("_confidence", 0.0)

    for key, default in (("day_title", source), ("theme", ""), ("opening_tip", ""), ("ending_tip", "")):
        value, ok = _coerce_str(data[key], default)
        if not ok:
            print(f"  [警告] {source}: 字段 {key} 类型非法，重置为默认值")
        data[key] = value
    sequence, ok = _coerce_list(data["sequence"], [])
    if not ok:
        print(f"  [警告] {source}: 字段 sequence 类型非法，重置为空列表")
    data["sequence"] = [s for s in sequence if isinstance(s, dict)]
    value, ok = _coerce_number(data["total_estimated_sec"], 180)
    if not ok:
        print(f"  [警告] {source}: 字段 total_estimated_sec 类型非法，重置为 180")
    data["total_estimated_sec"] = value
    value, ok = _coerce_number(data["_confidence"], 0.0)
    if not ok:
        print(f"  [警告] {source}: 字段 _confidence 类型非法，重置为 0.0")
    data["_confidence"] = value
    return data


def _wrap_with_context(prompt: str, config: AppConfig, context_override: str | None = None) -> str:
    """将背景/规范附加在 prompt 前面。

    层级（从上到下叠加）：
    1. templates/trip_context.md（项目级优先 → 包默认）
    2. config.ai.context（用户在设置页填写的项目特定内容）
    3. context_override（临时覆写，如 refine 时的额外说明）
    """
    parts = []
    # 1. 默认模板
    text = _read_trip_context(str(config.project_dir) if config.project_dir else "")
    if text:
        parts.append(text)
    # 2. 用户配置的 context
    if config.ai.context:
        parts.append(config.ai.context)
    # 3. 临时覆写
    if context_override:
        parts.append(context_override)
    if not parts:
        return prompt
    return f"## 背景与规范（请严格遵守）\n\n{chr(10).join(parts)}\n\n---\n\n{prompt}"


def _call_ai(
    label: str,
    provider_id: str,
    model: str,
    prompt: str,
    fn: Callable[[], AIResponse],
    *,
    debug_print: bool = False,
    token_store=None,
    task_name: str = "",
    cancel_event: threading.Event | None = None,
) -> str:
    # Check cancel_event before starting AI call
    if cancel_event and cancel_event.is_set():
        raise RuntimeError(f"{label} 被用户取消")

    if debug_print:
        print("=" * 60)
        print(f"[DEBUG PROMPT] {label} ({provider_id}/{model})")
        print("-" * 60)
        print(prompt)
        print("=" * 60)
    prompt_bytes = len(prompt.encode("utf-8"))
    print(f"  AI: {provider_id}/{model}（prompt {format_size(prompt_bytes)}）")
    with timed(f"{label} {provider_id}/{model}"):
        resp = fn()
    print(f"  响应: {format_size(len(resp.text.encode('utf-8')))}")
    if getattr(resp, "finish_reason", None) in ("length", "max_tokens"):
        print(
            f"  [警告] 模型输出被截断 (finish_reason={resp.finish_reason})，"
            "JSON 可能不完整；可在 config.yaml 提高该 provider 的 max_tokens 后重试"
        )
    if token_store and resp.token_usage:
        token_store.record(task_name or label, model, resp.token_usage)
    return resp.text


def _parse_timestamp_sec(ts: str) -> float:
    """将 "MM:SS" 或 "HH:MM:SS" 转为秒数。"""
    parts = ts.strip().split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return 0.0


def analyze_video(
    video_path: str,
    config: AppConfig,
    progress_callback: Callable[[str], None] | None = None,
    token_store=None,
    cancel_event: threading.Event | None = None,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict:
    provider, model = get_video_provider(config, TaskName.VIDEO_ANALYZE)
    base = resolve_prompt_template("video_analyze", ANALYZE_PROMPT, config, task_prompts=task_prompts)
    prompt = _wrap_with_context(base, config, context_override=context_override)
    text = _call_ai(
        "AI 视频分析",
        provider.provider_id,
        model,
        prompt,
        lambda: provider.analyze_video(
            video_path,
            prompt,
            model,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        ),
        debug_print=config.ai.debug_print_prompt,
        token_store=token_store,
        task_name=TaskName.VIDEO_ANALYZE,
        cancel_event=cancel_event,
    )
    # Check cancel_event after AI call but before validation
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("分析被用户取消")

    return _validate_analysis(extract_json(text), video_path)


def generate_voiceover(
    clip_data: dict,
    template: str,
    config: AppConfig,
    token_store=None,
    cancel_event: threading.Event | None = None,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict:
    if cancel_event and cancel_event.is_set():
        raise RuntimeError("voiceover 被用户取消")

    provider, model = get_task_provider(config, TaskName.VOICEOVER)

    timeline_text = "\n".join(
        f"- {t.get('start', '?')}-{t.get('end', '?')}: {t.get('description', '')}"
        for t in clip_data.get("timeline", [])
    )
    template_text = resolve_prompt_template("voiceover", SCRIPT_PROMPT, config, task_prompts=task_prompts)
    base = format_prompt_template(
        "voiceover",
        template_text,
        index=clip_data.get("index", ""),
        title=clip_data.get("title", ""),
        summary=clip_data.get("summary", ""),
        location=clip_data.get("location", ""),
        timeline_text=timeline_text or "（无）",
        template=template,
        target_words=config.script.target_words,
    )
    prompt = _wrap_with_context(base, config, context_override=context_override)
    text = _call_ai(
        "AI 口播",
        provider.provider_id,
        model,
        prompt,
        lambda: provider.generate_text(prompt, model),
        debug_print=config.ai.debug_print_prompt,
        token_store=token_store,
        task_name=TaskName.VOICEOVER,
    )
    return _validate_voiceover(extract_json(text), clip_data.get("title", ""))


def plan_daily_vlog(
    clips: list[dict],
    config: AppConfig,
    day_label: str = "day1",
    transcripts_map: dict[str, dict] | None = None,
    use_transcripts: bool = True,
    token_store=None,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict:
    provider, model = get_task_provider(config, TaskName.VLOG_PLAN)

    # 取第一个 clip 的 index 作为 prompt 示例，确保格式一致
    first_idx = clips[0].get("index", "001") if clips else "001"

    plan_template = resolve_prompt_template("vlog_plan", PLAN_PROMPT, config, task_prompts=task_prompts)
    base = format_prompt_template(
        "vlog_plan",
        plan_template,
        clips_json=json.dumps(clips, ensure_ascii=False, indent=None),
        max_clips=config.plan.max_clips_per_day,
        target_duration_sec=config.plan.target_duration_sec,
        example_index=first_idx,
    )
    if transcripts_map and use_transcripts and config.whisper.enabled:
        transcript_info = []
        for clip in clips:
            clip_stem = clip.get("match_stem") or clip.get("source_stem", "")
            trans = transcripts_map.get(clip_stem.lower()) if clip_stem else None
            if trans is None:
                continue
            offset = clip.get("segment_offset_sec", 0.0) or 0.0
            matched = []
            for tl in clip.get("timeline", []):
                tl_start = _parse_timestamp_sec(tl.get("start", "00:00")) + offset
                tl_end = _parse_timestamp_sec(tl.get("end", "00:00")) + offset
                for seg in trans.get("segments", []):
                    if seg["start"] >= tl_start and seg["end"] <= tl_end:
                        matched.append(seg)
            if matched:
                matched.sort(key=lambda s: -s.get("avg_logprob", 0))
                matched = matched[: config.whisper.max_segments_per_clip]
                transcript_info.append(
                    {
                        "clip_index": clip.get("index"),
                        "clip_title": clip.get("title"),
                        "transcript_segments": matched,
                    }
                )
        if transcript_info:
            transcript_json = json.dumps(transcript_info, ensure_ascii=False, indent=None)
            transcript_template = resolve_prompt_template(
                "transcript_context", TRANSCRIPT_CONTEXT, config, task_prompts=task_prompts
            )
            base += format_prompt_template("transcript_context", transcript_template, transcripts_json=transcript_json)
    prompt = _wrap_with_context(f"日 vlog 标签: {day_label}\n\n{base}", config, context_override=context_override)
    text = _call_ai(
        "AI vlog 剪辑规划",
        provider.provider_id,
        model,
        prompt,
        lambda: provider.generate_text(prompt, model),
        debug_print=config.ai.debug_print_prompt,
        token_store=token_store,
        task_name=TaskName.VLOG_PLAN,
    )
    result = _validate_plan(extract_json(text), day_label)

    # 后处理：过滤掉 segment 中引用不存在的 index 的项
    # 用整数比较（去零填充），兼容 "001"、"1"、1 等不同格式
    valid_ints: set[int | str] = set()
    for c in clips:
        idx = c.get("index")
        try:
            valid_ints.add(int(str(idx).strip()))
        except (ValueError, TypeError):
            valid_ints.add(str(idx))
    if "sequence" in result:
        original_count = len(result["sequence"])
        filtered = []
        for s in result["sequence"]:
            sidx = s.get("index")
            try:
                match = int(str(sidx).strip()) in valid_ints
            except (ValueError, TypeError):
                match = str(sidx) in valid_ints
            if match:
                filtered.append(s)
        result["sequence"] = filtered
        if len(result["sequence"]) < original_count:
            dropped = original_count - len(result["sequence"])
            print(f"[规划] 已过滤 {dropped} 个引用无效 index 的 segment")

    return result


def refine_text(
    analysis: dict,
    config: AppConfig,
    fix: str | None = None,
    context_override: str | None = None,
    token_store=None,
    task_prompts: dict[str, str] | None = None,
) -> dict:
    """审阅并修正现有的素材分析。

    fix 非空时切换为「按用户意见定向修正」模式（仅改用户提到的字段，
    changelog 第一条固定写"按用户意见修改了 XXX"）。
    """
    provider, model = get_task_provider(config, TaskName.REFINE_TEXT)
    if fix:
        refine_template = resolve_prompt_template(
            "refine_text_fix", REFINE_TEXT_FIX_PROMPT, config, task_prompts=task_prompts
        )
        base = format_prompt_template(
            "refine_text_fix",
            refine_template,
            fix_instruction=fix.strip(),
            existing_json=json.dumps(analysis, ensure_ascii=False, indent=None),
        )
        label = "AI refine (定向)"
    else:
        refine_template = resolve_prompt_template("refine_text", REFINE_TEXT_PROMPT, config, task_prompts=task_prompts)
        base = format_prompt_template(
            "refine_text",
            refine_template,
            existing_json=json.dumps(analysis, ensure_ascii=False, indent=None),
        )
        label = "AI refine 素材"
    prompt = _wrap_with_context(base, config, context_override=context_override)
    text = _call_ai(
        label,
        provider.provider_id,
        model,
        prompt,
        lambda: provider.generate_text(prompt, model),
        debug_print=config.ai.debug_print_prompt,
        token_store=token_store,
        task_name=TaskName.REFINE_TEXT,
    )
    result = extract_json(text)
    if not isinstance(result, dict) or "index" not in result:
        print("  [警告] refine_text: AI 返回结构异常，使用原始数据")
        return analysis
    return result


def refine_script(
    script: dict,
    analysis: dict | None,
    config: AppConfig,
    fix: str | None = None,
    context_override: str | None = None,
    token_store=None,
    task_prompts: dict[str, str] | None = None,
) -> dict:
    """审阅并修正现有的口播文案。

    复用 refine_text 任务的 provider/model 配置 —— texts 和 scripts 审阅
    都是纯文本输入输出，没必要拆两个任务。
    fix 非空时切换为定向修正模式（同 refine_text）。
    """
    provider, model = get_task_provider(config, TaskName.REFINE_TEXT)
    analysis_json = json.dumps(analysis, ensure_ascii=False, indent=None) if analysis else "（无）"
    existing_json = json.dumps(script, ensure_ascii=False, indent=None)
    if fix:
        refine_template = resolve_prompt_template(
            "refine_script_fix", REFINE_SCRIPT_FIX_PROMPT, config, task_prompts=task_prompts
        )
        base = format_prompt_template(
            "refine_script_fix",
            refine_template,
            fix_instruction=fix.strip(),
            analysis_json=analysis_json,
            existing_json=existing_json,
        )
        label = "AI refine 脚本 (定向)"
    else:
        refine_template = resolve_prompt_template(
            "refine_script", REFINE_SCRIPT_PROMPT, config, task_prompts=task_prompts
        )
        base = format_prompt_template(
            "refine_script",
            refine_template,
            analysis_json=analysis_json,
            existing_json=existing_json,
        )
        label = "AI refine 脚本"
    prompt = _wrap_with_context(base, config, context_override=context_override)
    text = _call_ai(
        label,
        provider.provider_id,
        model,
        prompt,
        lambda: provider.generate_text(prompt, model),
        debug_print=config.ai.debug_print_prompt,
        token_store=token_store,
        task_name=TaskName.REFINE_TEXT,
    )
    result = extract_json(text)
    if not isinstance(result, dict) or "voiceover" not in result:
        print("  [警告] refine_script: AI 返回结构异常，使用原始数据")
        return script
    return result

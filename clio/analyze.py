from __future__ import annotations

import copy
import json
import math
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clio.ai.base import AIResponse, TaskName
from clio.ai.factory import get_task_provider, get_video_provider
from clio.config import AppConfig
from clio.cut import parse_time_range
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
_REFINEMENT_IMMUTABLE_FIELDS = {
    "_lineage",
    "_schema_version",
    "compressed_file",
    "id",
    "index",
    "media_identity",
    "source_file",
}


def _refinement_value_compatible(original: Any, candidate: Any) -> bool:
    if original is None:
        return True
    if isinstance(original, bool):
        return isinstance(candidate, bool)
    if isinstance(original, (int, float)):
        return isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
    return isinstance(candidate, type(original))


def _read_trip_context(project_dir: str) -> str:
    """读取 trip_context.md，按项目目录 + 文件 mtime 缓存。

    查找优先级：
    1. <project_dir>/templates/trip_context.md（项目级）
    2. <default_package>/templates/trip_context.md（包默认）
    """
    from clio.utils import validate_within_root

    project_root = Path(project_dir).resolve()
    project_path = project_root / "templates" / "trip_context.md"
    if project_path.is_file():
        try:
            validate_within_root(project_path, project_root)
        except ValueError:
            return ""
        mtime = project_path.stat().st_mtime
        key = f"{project_dir}@{mtime}"
        if key in _trip_context_cache:
            return _trip_context_cache[key]
        text = project_path.read_text(encoding="utf-8").strip()
        if text:
            _trip_context_cache[key] = text
            return text
    default_path = (Path(__file__).parent.parent / "templates" / "trip_context.md").resolve()
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


def _prompt_context_parts(config: AppConfig, context_override: str | None = None) -> list[str]:
    """Return the exact context fragments prepended to task prompts."""
    parts: list[str] = []
    text = _read_trip_context(str(config.project_dir) if config.project_dir else "")
    if text:
        parts.append(text)
    if config.ai.context:
        parts.append(config.ai.context)
    if context_override:
        parts.append(context_override)
    return parts


def _coerce_str(value: Any, default: str) -> tuple[str, bool]:
    if isinstance(value, str):
        return value, True
    return default, False


def _coerce_number(value: Any, default: float) -> tuple[float, bool]:
    if isinstance(value, bool):
        return default, False
    try:
        number = float(value)
        if not math.isfinite(number):
            return default, False
        return number, True
    except (TypeError, ValueError):
        return default, False


def _coerce_list(value: Any) -> tuple[list[Any], bool]:
    if isinstance(value, list):
        return value, True
    return [], False


def _validate_analysis(data: dict, source: str) -> dict:
    """校验 AI 分析结果，缺失字段补默认值并对错误类型告警归一。"""
    data = copy.deepcopy(data) if isinstance(data, dict) else {}
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
    for key in ("timeline", "highlights"):
        value, ok = _coerce_list(data[key])
        if not ok:
            print(f"  [警告] {source}: 字段 {key} 类型非法（{type(data[key]).__name__}），重置为空列表")
        data[key] = value
    # Downstream prompt construction expects mapping-shaped timeline entries.
    # Keep valid entries and discard malformed model output before it can crash.
    data["timeline"] = [item for item in data["timeline"] if isinstance(item, dict)]
    data["highlights"] = [item for item in data["highlights"] if isinstance(item, (dict, str))]
    value, ok = _coerce_number(data["_confidence"], 0.0)
    if not ok:
        print(f"  [警告] {source}: 字段 _confidence 类型非法，重置为 0.0")
    data["_confidence"] = min(1.0, max(0.0, value))
    return data


def _validate_voiceover(data: dict, source: str) -> dict:
    """校验 AI 口播文案结果。"""
    data = copy.deepcopy(data) if isinstance(data, dict) else {}
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
    data["duration_hint_sec"] = max(0.0, value)
    value, ok = _coerce_number(data["_confidence"], 0.0)
    if not ok:
        print(f"  [警告] {source}: 字段 _confidence 类型非法，重置为 0.0")
    data["_confidence"] = min(1.0, max(0.0, value))
    return data


def _validate_plan(data: dict, source: str) -> dict:
    """校验 AI vlog 规划结果。"""
    data = copy.deepcopy(data) if isinstance(data, dict) else {}
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
    sequence, ok = _coerce_list(data["sequence"])
    if not ok:
        print(f"  [警告] {source}: 字段 sequence 类型非法，重置为空列表")
    data["sequence"] = [s for s in sequence if isinstance(s, dict)]
    value, ok = _coerce_number(data["total_estimated_sec"], 180)
    if not ok:
        print(f"  [警告] {source}: 字段 total_estimated_sec 类型非法，重置为 180")
    data["total_estimated_sec"] = max(0.0, value)
    value, ok = _coerce_number(data["_confidence"], 0.0)
    if not ok:
        print(f"  [警告] {source}: 字段 _confidence 类型非法，重置为 0.0")
    data["_confidence"] = min(1.0, max(0.0, value))
    return data


def _merge_refinement_result(original: dict, candidate: Any, *, required_field: str | None = None) -> dict:
    """Apply a conservative refinement without allowing field loss or drift.

    Refinement prompts ask the model to return the complete JSON object, but a
    model may omit unchanged fields or add explanatory keys.  The original
    object remains authoritative: only existing fields and the reserved
    ``_changelog`` field can be copied from the candidate.
    """
    if not isinstance(original, dict) or not isinstance(candidate, dict):
        return copy.deepcopy(original)
    if required_field and required_field in original and required_field not in candidate:
        return copy.deepcopy(original)

    merged = copy.deepcopy(original)
    unknown: list[str] = []
    for key, value in candidate.items():
        if key in _REFINEMENT_IMMUTABLE_FIELDS:
            continue
        if key == "_changelog":
            if isinstance(value, list):
                merged[key] = [str(item) for item in value if isinstance(item, (str, int, float))]
            else:
                merged[key] = []
        elif key in original:
            if _refinement_value_compatible(original[key], value):
                merged[key] = copy.deepcopy(value)
            else:
                print(f"  [警告] refine: 忽略类型不兼容字段 {key}")
        else:
            unknown.append(str(key))
    if unknown:
        print(f"  [警告] refine: 忽略未声明字段 {', '.join(sorted(unknown))}")
    return merged


def _wrap_with_context(prompt: str, config: AppConfig, context_override: str | None = None) -> str:
    """将背景/规范附加在 prompt 前面。

    层级（从上到下叠加）：
    1. templates/trip_context.md（项目级优先 → 包默认）
    2. config.ai.context（用户在设置页填写的项目特定内容）
    3. context_override（临时覆写，如 refine 时的额外说明）
    """
    parts = _prompt_context_parts(config, context_override)
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


def _timeline_intervals(clip: dict) -> list[tuple[float, float]]:
    def parse_strict(value: Any) -> float:
        if isinstance(value, bool) or value is None:
            raise ValueError("invalid timestamp")
        if isinstance(value, (int, float)):
            result = float(value)
            if math.isfinite(result):
                return result
            raise ValueError("invalid timestamp")
        text = str(value).strip()
        parts = text.split(":")
        if len(parts) == 2:
            result = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            result = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            raise ValueError("invalid timestamp")
        if not math.isfinite(result):
            raise ValueError("invalid timestamp")
        return result

    intervals: list[tuple[float, float]] = []
    timeline = clip.get("timeline", [])
    if not isinstance(timeline, list):
        return intervals
    for item in timeline:
        if not isinstance(item, dict):
            continue
        try:
            start = parse_strict(item.get("start"))
            end = parse_strict(item.get("end"))
        except (TypeError, ValueError):
            continue
        if start >= 0 and end > start:
            intervals.append((start, end))
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _range_is_in_timeline(start: float, end: float, intervals: list[tuple[float, float]]) -> bool:
    """Return whether a planned range is covered by one or more timeline ranges."""
    cursor = start
    for left, right in intervals:
        if right <= cursor:
            continue
        if left > cursor + 0.05:
            return False
        cursor = max(cursor, right)
        if cursor >= end - 0.05:
            return True
    return False


def _select_transcript_segments(
    transcript: dict,
    intervals: list[tuple[float, float]],
    *,
    offset_sec: float,
    limit: int,
) -> list[dict]:
    """Select mostly-overlapping, high-confidence ASR segments in time order."""
    candidates: list[dict] = []
    if not isinstance(transcript, dict):
        return candidates
    seen: set[tuple[float, float, str]] = set()
    shifted = [(start + offset_sec, end + offset_sec) for start, end in intervals]
    segments = transcript.get("segments", [])
    if not isinstance(segments, list):
        return candidates
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError):
            continue
        duration = end - start
        if duration <= 0:
            continue
        overlap = max((max(0.0, min(end, right) - max(start, left)) for left, right in shifted), default=0.0)
        if overlap / duration < 0.5:
            continue
        key = (start, end, str(segment.get("text") or ""))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(segment)

    def confidence_key(segment: dict) -> tuple[bool, float]:
        try:
            confidence = float(segment.get("avg_logprob", float("-inf")))
        except (TypeError, ValueError):
            confidence = float("-inf")
        return (not bool(segment.get("low_confidence")), confidence)

    if limit > 0:
        candidates = sorted(candidates, key=confidence_key, reverse=True)[:limit]
    return sorted(candidates, key=lambda segment: float(segment.get("start", 0.0)))


def _validate_plan_ranges(result: dict, clips: list[dict], max_clips: int, target_duration_sec: float) -> dict:
    """Filter impossible plan segments and normalize the reported duration."""
    by_index: dict[int | str, list[tuple[float, float]]] = {}
    for clip in clips:
        raw = clip.get("index")
        try:
            key: int | str = int(str(raw).strip())
        except (TypeError, ValueError):
            key = str(raw).strip()
        by_index[key] = _timeline_intervals(clip)

    valid: list[dict] = []
    dropped = 0
    for segment in result.get("sequence", []):
        if not isinstance(segment, dict):
            continue
        raw_idx = segment.get("index")
        try:
            idx: int | str = int(str(raw_idx).strip())
        except (TypeError, ValueError):
            idx = str(raw_idx).strip()
        timeline = str(segment.get("use_timeline") or "").strip()
        intervals = by_index.get(idx, [])
        if timeline and intervals:
            try:
                start, end = parse_time_range(timeline)
            except (TypeError, ValueError):
                start = end = 0.0
            if end <= start or not _range_is_in_timeline(start, end, intervals):
                dropped += 1
                continue
        valid.append(segment)

    if dropped:
        print(f"  [规划] 已过滤 {dropped} 个超出素材 timeline 的 segment")
    if max_clips > 0 and len(valid) > max_clips:
        print(f"  [规划] sequence 超过 max_clips={max_clips}，已保留前 {max_clips} 段")
        valid = valid[:max_clips]

    total = 0.0
    for segment in valid:
        try:
            start, end = parse_time_range(str(segment.get("use_timeline") or ""))
        except (TypeError, ValueError):
            continue
        total += max(0.0, end - start)
    if total > 0:
        if target_duration_sec > 0 and abs(total - target_duration_sec) > max(10.0, target_duration_sec * 0.25):
            print(f"  [规划] 实际片段总时长 {total:.1f}s 与目标 {target_duration_sec:.1f}s 偏差较大，已按实际值记录")
        result["total_estimated_sec"] = round(total, 2)
    result["sequence"] = valid
    return result


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

    timeline = clip_data.get("timeline", [])
    if not isinstance(timeline, list):
        timeline = []
    timeline_lines = "\n".join(
        f"- {t.get('start', '?')}-{t.get('end', '?')}: {t.get('description', '')}"
        for t in timeline
        if isinstance(t, dict)
    )
    intervals = _timeline_intervals(clip_data)
    timeline_duration = sum(end - start for start, end in intervals)
    duration_note = (
        f"当前素材 timeline 覆盖时长约 {timeline_duration:.1f} 秒；按每秒约 2.5-3.5 个汉字，建议口播约 "
        f"{max(1, round(timeline_duration * 2.5))}-{max(1, round(timeline_duration * 3.5))} 字。"
        "只按给出的素材范围估算，不要假设未提供的剪辑区间。\n"
        if timeline_duration > 0
        else "当前素材没有可解析的 timeline 时长，请勿虚构精确时长。\n"
    )
    template_text = resolve_prompt_template("voiceover", SCRIPT_PROMPT, config, task_prompts=task_prompts)
    base = format_prompt_template(
        "voiceover",
        template_text,
        index=clip_data.get("index", ""),
        title=clip_data.get("title", ""),
        summary=clip_data.get("summary", ""),
        location=clip_data.get("location", ""),
        timeline_text=duration_note + (timeline_lines or "（无）"),
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

    plan_template = resolve_prompt_template("vlog_plan", PLAN_PROMPT, config, task_prompts=task_prompts)
    # Keep the legacy override placeholder meaningful for custom templates,
    # while the built-in example uses a neutral marker instead of a real index.
    first_idx = clips[0].get("index", "001") if clips else "001"
    example_index = first_idx if plan_template != PLAN_PROMPT else "<必须从上面的素材列表原样选择>"
    base = format_prompt_template(
        "vlog_plan",
        plan_template,
        clips_json=json.dumps(clips, ensure_ascii=False, indent=None),
        max_clips=config.plan.max_clips_per_day,
        target_duration_sec=config.plan.target_duration_sec,
        example_index=example_index,
    )
    if transcripts_map and use_transcripts and config.whisper.enabled:
        transcript_info = []
        for clip in clips:
            clip_stem = clip.get("match_stem") or clip.get("source_stem", "")
            trans = transcripts_map.get(clip_stem.lower()) if clip_stem else None
            if trans is None:
                continue
            offset = float(clip.get("segment_offset_sec", 0.0) or 0.0)
            matched = _select_transcript_segments(
                trans,
                _timeline_intervals(clip),
                offset_sec=offset,
                limit=config.whisper.max_segments_per_clip,
            )
            if matched:
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

    result = _validate_plan_ranges(
        result,
        clips,
        max_clips=int(getattr(config.plan, "max_clips_per_day", 0) or 0),
        target_duration_sec=float(getattr(config.plan, "target_duration_sec", 0) or 0),
    )

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
    if not isinstance(result, dict):
        print("  [警告] refine_text: AI 返回结构异常，使用原始数据")
        return analysis
    return _merge_refinement_result(analysis, result)


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
    if not isinstance(result, dict):
        print("  [警告] refine_script: AI 返回结构异常，使用原始数据")
        return script
    return _merge_refinement_result(script, result, required_field="voiceover")

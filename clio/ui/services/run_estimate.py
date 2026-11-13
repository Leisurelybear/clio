"""Conservative pre-run estimates for AI calls, upload volume, tokens, and cost."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import ceil
from typing import Any

from clio.ai.base import TaskName
from clio.ai.token_usage import estimate_cost_usd
from clio.analyze_windows import build_analyze_windows
from clio.config.models import AppConfig

# Video analysis tokenization is model-specific. These values are deliberately
# coarse budgets for a preview, not an attempt to mirror provider internals.
ANALYZE_PROMPT_TOKENS_PER_VIDEO_SECOND = {"low": 32, "medium": 64, "high": 128}
ANALYZE_PROMPT_TOKENS_PER_VIDEO_SECOND_DEFAULT = 64
ANALYZE_CALL_PROMPT_OVERHEAD_TOKENS = 1_200
ANALYZE_CALL_COMPLETION_TOKENS = 900
ANALYZE_SUMMARY_PROMPT_TOKENS = 3_000
ANALYZE_SUMMARY_COMPLETION_TOKENS = 1_600
CONSISTENCY_PROMPT_TOKENS = 4_000
CONSISTENCY_COMPLETION_TOKENS = 1_800
VOICEOVER_PROMPT_TOKENS = 3_000
PLAN_CALL_TOKEN_BUDGETS = (
    (6_000, 1_800),  # outline
    (8_000, 2_400),  # shot selection
    (7_000, 1_800),  # review
)
PLAN_VOICEOVER_PROMPT_TOKENS = 2_500


@dataclass(frozen=True)
class EstimatedCall:
    task: TaskName
    prompt_tokens: int
    completion_tokens: int


@dataclass(frozen=True)
class AnalyzeInput:
    """One media item that will be sent through the analyze task."""

    duration_sec: float
    file_bytes: int
    legacy_split: bool = False


def not_applicable_usage() -> dict[str, Any]:
    return {
        "planned_calls": 0,
        "estimated_prompt_tokens": 0,
        "estimated_completion_tokens": 0,
        "estimated_cost_usd": 0.0,
        "estimate_status": "not_applicable",
        "estimated_upload_bytes": 0,
    }


def analyze_video_call(config: AppConfig, duration_sec: float) -> EstimatedCall:
    resolution = str(getattr(config.analyze, "media_resolution", "medium") or "medium").lower()
    tokens_per_second = ANALYZE_PROMPT_TOKENS_PER_VIDEO_SECOND.get(
        resolution,
        ANALYZE_PROMPT_TOKENS_PER_VIDEO_SECOND_DEFAULT,
    )
    video_tokens = ceil(max(0.0, duration_sec) * tokens_per_second)
    return EstimatedCall(
        TaskName.VIDEO_ANALYZE,
        prompt_tokens=ANALYZE_CALL_PROMPT_OVERHEAD_TOKENS + video_tokens,
        completion_tokens=ANALYZE_CALL_COMPLETION_TOKENS,
    )


def analyze_summary_call() -> EstimatedCall:
    return EstimatedCall(
        TaskName.REFINE_TEXT,
        prompt_tokens=ANALYZE_SUMMARY_PROMPT_TOKENS,
        completion_tokens=ANALYZE_SUMMARY_COMPLETION_TOKENS,
    )


def consistency_call() -> EstimatedCall:
    return EstimatedCall(
        TaskName.REFINE_TEXT,
        prompt_tokens=CONSISTENCY_PROMPT_TOKENS,
        completion_tokens=CONSISTENCY_COMPLETION_TOKENS,
    )


def voiceover_call(config: AppConfig) -> EstimatedCall:
    target_words = max(1, int(getattr(config.script, "target_words", 80) or 80))
    return EstimatedCall(
        TaskName.VOICEOVER,
        prompt_tokens=VOICEOVER_PROMPT_TOKENS,
        completion_tokens=target_words * 2,
    )


def plan_calls() -> tuple[EstimatedCall, EstimatedCall, EstimatedCall]:
    outline = EstimatedCall(
        TaskName.VLOG_PLAN,
        prompt_tokens=PLAN_CALL_TOKEN_BUDGETS[0][0],
        completion_tokens=PLAN_CALL_TOKEN_BUDGETS[0][1],
    )
    shot_selection = EstimatedCall(
        TaskName.VLOG_PLAN,
        prompt_tokens=PLAN_CALL_TOKEN_BUDGETS[1][0],
        completion_tokens=PLAN_CALL_TOKEN_BUDGETS[1][1],
    )
    review = EstimatedCall(
        TaskName.VLOG_PLAN,
        prompt_tokens=PLAN_CALL_TOKEN_BUDGETS[2][0],
        completion_tokens=PLAN_CALL_TOKEN_BUDGETS[2][1],
    )
    return outline, shot_selection, review


def plan_voiceover_call(config: AppConfig) -> EstimatedCall:
    target_words = max(1, int(getattr(config.script, "target_words", 80) or 80))
    return EstimatedCall(
        TaskName.VOICEOVER,
        prompt_tokens=PLAN_VOICEOVER_PROMPT_TOKENS,
        completion_tokens=target_words * 2,
    )


def estimate_analyze_usage(config: AppConfig, inputs: Iterable[AnalyzeInput]) -> dict[str, Any]:
    calls: list[EstimatedCall] = []
    upload_bytes = 0

    for item in inputs:
        duration_sec = max(0.0, item.duration_sec)
        source_bytes = max(0, item.file_bytes)
        windows = (
            []
            if item.legacy_split or duration_sec <= 0
            else build_analyze_windows(
                duration_sec,
                int(getattr(config.analyze, "window_max_min", 15) or 15),
                int(getattr(config.analyze, "window_overlap_sec", 20) or 0),
            )
        )
        if item.legacy_split or not windows:
            calls.append(analyze_video_call(config, duration_sec))
            upload_bytes += source_bytes
            continue

        calls.extend(analyze_video_call(config, window.duration_sec) for window in windows)
        if len(windows) > 1:
            calls.append(analyze_summary_call())
            upload_bytes += round(source_bytes * sum(window.duration_sec for window in windows) / duration_sec)
        else:
            upload_bytes += source_bytes

    return estimate_usage(config, calls, upload_bytes=upload_bytes)


def estimate_usage(
    config: AppConfig,
    calls: Iterable[EstimatedCall],
    *,
    upload_bytes: int = 0,
) -> dict[str, Any]:
    planned_calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    cost_usd = 0.0
    priced_calls = 0
    unpriced_calls = 0

    for call in calls:
        planned_calls += 1
        prompt_tokens += max(0, call.prompt_tokens)
        completion_tokens += max(0, call.completion_tokens)
        prices = _task_prices(config, call.task)
        if prices is None:
            unpriced_calls += 1
            continue
        prompt_price, completion_price = prices
        priced_calls += 1
        cost_usd += estimate_cost_usd(
            max(0, call.prompt_tokens),
            max(0, call.completion_tokens),
            prompt_price,
            completion_price,
        )

    if planned_calls == 0:
        return not_applicable_usage()

    if unpriced_calls and not priced_calls:
        status = "unknown"
        estimated_cost: float | None = None
    elif unpriced_calls:
        status = "partial"
        estimated_cost = None
    else:
        status = "estimated"
        estimated_cost = round(cost_usd, 8)

    return {
        "planned_calls": planned_calls,
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_completion_tokens": completion_tokens,
        "estimated_cost_usd": estimated_cost,
        "estimate_status": status,
        "estimated_upload_bytes": max(0, int(upload_bytes)),
    }


def combine_usage(estimates: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(estimates)
    planned_calls = sum(_non_negative_int(item.get("planned_calls")) for item in items)
    if planned_calls == 0:
        return not_applicable_usage()

    statuses = {
        str(item.get("estimate_status") or "unknown") for item in items if _non_negative_int(item.get("planned_calls"))
    }
    if "estimated" not in statuses:
        status = "partial" if "partial" in statuses else "unknown"
        cost: float | None = None
    elif statuses <= {"estimated", "not_applicable"}:
        status = "estimated"
        cost = round(sum(float(item.get("estimated_cost_usd") or 0.0) for item in items), 8)
    else:
        status = "partial"
        cost = None

    return {
        "planned_calls": planned_calls,
        "estimated_prompt_tokens": sum(_non_negative_int(item.get("estimated_prompt_tokens")) for item in items),
        "estimated_completion_tokens": sum(
            _non_negative_int(item.get("estimated_completion_tokens")) for item in items
        ),
        "estimated_cost_usd": cost,
        "estimate_status": status,
        "estimated_upload_bytes": sum(_non_negative_int(item.get("estimated_upload_bytes")) for item in items),
    }


def _task_prices(config: AppConfig, task: TaskName) -> tuple[float, float] | None:
    task_config = config.ai.tasks.get(task.value)
    if task_config is None:
        return None
    provider = config.ai.providers.get(task_config.provider)
    if provider is None:
        return None
    prompt_price = float(provider.prompt_price_per_1m_usd or 0.0)
    completion_price = float(provider.completion_price_per_1m_usd or 0.0)
    if prompt_price <= 0 or completion_price <= 0:
        return None
    return prompt_price, completion_price


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0

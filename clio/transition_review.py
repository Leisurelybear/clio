"""Opt-in visual review of transitions in a validated daily vlog plan."""

from __future__ import annotations

import copy
import json
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from clio.ai.base import TaskName
from clio.ai.factory import get_task_config, get_video_provider, provider_use
from clio.ai.structured import TRANSITION_REVIEW_OUTPUT, TransitionReview
from clio.config import AppConfig
from clio.cut import parse_time_range
from clio.prompt_overrides import format_prompt_template, resolve_prompt_template
from clio.prompts import TRANSITION_REVIEW_PROMPT
from clio.utils import extract_json, resolve_binary, run_ffmpeg

_MAX_REVIEW_PAIRS = 12
_FRAME_SIZE = "640:360"
_ALLOWED_TRANSITIONS = frozenset({"hard_cut", "match_action", "b_roll", "ambient_audio", "dissolve"})


@dataclass(frozen=True)
class _FrameSpec:
    index: str
    use_timeline: str
    media_candidates: tuple[tuple[Path, float], ...]


@dataclass(frozen=True)
class _ReviewPair:
    position: int
    previous: _FrameSpec
    next: _FrameSpec


def _index_key(value: Any) -> int | str:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return str(value).strip()


def _existing_path(raw: str, base: Path | None) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    candidates = [candidate]
    if not candidate.is_absolute() and base is not None:
        candidates.append(base / candidate)
    for item in candidates:
        try:
            if item.is_file():
                return item
        except OSError:
            continue
    return None


def _frame_spec(
    clip: dict[str, Any],
    segment: dict[str, Any],
    config: AppConfig,
    *,
    at_end: bool,
) -> _FrameSpec | None:
    try:
        start, end = parse_time_range(str(segment.get("use_timeline") or ""))
    except (TypeError, ValueError):
        return None
    timestamp = max(start, end - 0.05) if at_end else start + 0.05
    try:
        segment_offset = float(clip.get("segment_offset_sec", 0.0) or 0.0)
    except (TypeError, ValueError):
        segment_offset = 0.0

    candidates: list[tuple[Path, float]] = []
    compressed = _existing_path(str(clip.get("_compressed_path") or ""), config.compressed_dir)
    if compressed is not None:
        candidates.append((compressed, timestamp))
    original = _existing_path(
        str(clip.get("_original_path") or ""),
        getattr(config, "project_dir", None),
    )
    if original is not None:
        candidates.append((original, timestamp + segment_offset))
    if not candidates:
        return None
    return _FrameSpec(
        index=str(clip.get("index") or ""),
        use_timeline=str(segment.get("use_timeline") or ""),
        media_candidates=tuple(candidates),
    )


def _adjacent_pairs(plan: dict[str, Any], clips: list[dict[str, Any]], config: AppConfig) -> list[_ReviewPair] | None:
    sequence = plan.get("sequence")
    if not isinstance(sequence, list) or len(sequence) < 2:
        return []
    clips_by_index = {_index_key(clip.get("index")): clip for clip in clips if isinstance(clip, dict)}
    pairs: list[_ReviewPair] = []
    for offset in range(min(len(sequence) - 1, _MAX_REVIEW_PAIRS)):
        previous = sequence[offset]
        following = sequence[offset + 1]
        if not isinstance(previous, dict) or not isinstance(following, dict):
            return None
        previous_clip = clips_by_index.get(_index_key(previous.get("index")))
        following_clip = clips_by_index.get(_index_key(following.get("index")))
        if previous_clip is None or following_clip is None:
            return None
        previous_spec = _frame_spec(previous_clip, previous, config, at_end=True)
        following_spec = _frame_spec(following_clip, following, config, at_end=False)
        if previous_spec is None or following_spec is None:
            return None
        pairs.append(_ReviewPair(position=offset + 2, previous=previous_spec, next=following_spec))
    return pairs


def _extract_frame(
    spec: _FrameSpec,
    output: Path,
    ffmpeg: str,
    cancel_event: threading.Event | None,
) -> Path:
    last_error: Exception | None = None
    for media_path, timestamp in spec.media_candidates:
        if output.exists():
            output.unlink()
        args = [
            "-ss",
            str(timestamp),
            "-i",
            str(media_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={_FRAME_SIZE}:force_original_aspect_ratio=decrease,pad={_FRAME_SIZE}:(ow-iw)/2:(oh-ih)/2",
            "-y",
            str(output),
        ]
        try:
            run_ffmpeg(args, ffmpeg, cancel_event=cancel_event)
            if output.is_file() and output.stat().st_size > 0:
                return output
            last_error = RuntimeError(f"ffmpeg produced no frame for {spec.index}")
        except InterruptedError:
            raise
        except Exception as exc:  # noqa: BLE001 - try the original after a compressed-media failure
            last_error = exc
    raise last_error or RuntimeError(f"no media available for {spec.index}")


def _build_contact_sheet(
    pairs: list[_ReviewPair],
    temp_dir: Path,
    ffmpeg: str,
    cancel_event: threading.Event | None,
) -> Path:
    frame_pattern = temp_dir / "frame_%03d.png"
    for frame_index, spec in enumerate(
        (spec for pair in pairs for spec in (pair.previous, pair.next)),
    ):
        _extract_frame(
            spec,
            temp_dir / f"frame_{frame_index:03d}.png",
            ffmpeg,
            cancel_event,
        )
    contact_sheet = temp_dir / "transitions.png"
    args = [
        "-framerate",
        "1",
        "-start_number",
        "0",
        "-i",
        str(frame_pattern),
        "-vf",
        f"tile=2x{len(pairs)}:padding=0:margin=0",
        "-frames:v",
        "1",
        "-y",
        str(contact_sheet),
    ]
    run_ffmpeg(args, ffmpeg, cancel_event=cancel_event)
    if not contact_sheet.is_file() or contact_sheet.stat().st_size == 0:
        raise RuntimeError("ffmpeg produced no transition contact sheet")
    return contact_sheet


def _pairs_payload(pairs: list[_ReviewPair]) -> str:
    return json.dumps(
        [
            {
                "position": pair.position,
                "previous": {"index": pair.previous.index, "use_timeline": pair.previous.use_timeline},
                "next": {"index": pair.next.index, "use_timeline": pair.next.use_timeline},
            }
            for pair in pairs
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _apply_review(plan: dict[str, Any], response_text: str, expected_positions: set[int]) -> dict[str, Any]:
    original = copy.deepcopy(plan)
    try:
        review = TransitionReview.model_validate(extract_json(response_text))
    except (ValueError, ValidationError):
        return original
    positions = [item.position for item in review.items]
    if len(positions) != len(set(positions)) or set(positions) != expected_positions:
        return original
    sequence = original.get("sequence")
    if not isinstance(sequence, list):
        return original
    for item in review.items:
        if item.position not in expected_positions or item.transition not in _ALLOWED_TRANSITIONS:
            return original
    reviewed = copy.deepcopy(original)
    for item in review.items:
        reviewed["sequence"][item.position - 1]["transition"] = item.transition
    return reviewed


def _review_with_gemini(
    plan: dict[str, Any],
    config: AppConfig,
    pairs: list[_ReviewPair],
    contact_sheet: Path,
    cancel_event: threading.Event | None,
    task_prompts: dict[str, str] | None,
) -> dict[str, Any]:
    original = copy.deepcopy(plan)
    try:
        provider, model = get_video_provider(config, TaskName.VIDEO_ANALYZE)
    except Exception:  # noqa: BLE001 - optional review must never invalidate a valid plan
        return original
    analyze_images = getattr(provider, "analyze_images", None)
    if not callable(analyze_images):
        return original
    try:
        template = resolve_prompt_template(
            "transition_review",
            TRANSITION_REVIEW_PROMPT,
            config,
            task_prompts=task_prompts,
        )
        prompt = format_prompt_template("transition_review", template, pairs_json=_pairs_payload(pairs))
        try:
            temperature = get_task_config(config, TaskName.VIDEO_ANALYZE).temperature
        except (AttributeError, KeyError, ValueError):
            temperature = None
        with provider_use(provider):
            response = analyze_images(
                [str(contact_sheet)],
                prompt,
                model,
                cancel_event=cancel_event,
                temperature=temperature,
                structured_output=TRANSITION_REVIEW_OUTPUT,
            )
        return _apply_review(plan, response.text, {pair.position for pair in pairs})
    except InterruptedError:
        raise
    except Exception:  # noqa: BLE001 - Gemini failure falls back to the three-stage plan
        return original


def review_plan_transitions(
    plan: dict[str, Any],
    config: AppConfig,
    clips: list[dict[str, Any]],
    cancel_event: threading.Event | None = None,
    task_prompts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a plan whose adjacent transitions were optionally reviewed visually."""
    original = copy.deepcopy(plan)
    if not bool(getattr(config.plan, "visual_transition_review", False)):
        return original
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("transition review cancelled")
    pairs = _adjacent_pairs(plan, clips, config)
    if not pairs:
        return original

    try:
        ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
        with tempfile.TemporaryDirectory(prefix="clio-transition-review-") as raw_temp:
            contact_sheet = _build_contact_sheet(pairs, Path(raw_temp), ffmpeg, cancel_event)
            return _review_with_gemini(
                plan,
                config,
                pairs,
                contact_sheet,
                cancel_event,
                task_prompts,
            )
    except InterruptedError:
        raise
    except Exception:  # noqa: BLE001 - extraction/contact-sheet failures keep the validated plan
        return original

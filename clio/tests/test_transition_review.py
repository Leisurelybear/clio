"""Tests for opt-in visual transition review."""

from __future__ import annotations

import copy
import json
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from clio.ai.base import AIResponse
from clio.ai.structured import TRANSITION_REVIEW_OUTPUT, TransitionReview
from clio.config import AppConfig
from clio.plan_input import build_plan_clips
from clio.transition_review import (
    _adjacent_pairs,
    _apply_review,
    _build_contact_sheet,
    _extract_frame,
    _frame_spec,
    _pairs_payload,
    review_plan_transitions,
)


@pytest.fixture
def cfg(loaded_config: AppConfig) -> AppConfig:
    return loaded_config


def _plan() -> dict[str, Any]:
    return {
        "day_title": "test day",
        "theme": "walk",
        "total_estimated_sec": 30,
        "sequence": [
            {
                "index": "001",
                "title": "one",
                "use_timeline": "00:00-00:10",
                "transition": "hard_cut",
                "reason": "opening",
            },
            {
                "index": "002",
                "title": "two",
                "use_timeline": "00:20-00:30",
                "transition": "dissolve",
                "reason": "middle",
            },
            {
                "index": "003",
                "title": "three",
                "use_timeline": "00:40-00:50",
                "transition": "b_roll",
                "reason": "ending",
            },
        ],
    }


def _clips(cfg: AppConfig) -> list[dict[str, Any]]:
    project_dir = cfg.project_dir
    assert project_dir is not None
    cfg.compressed_dir.mkdir(parents=True, exist_ok=True)
    clips: list[dict[str, Any]] = []
    for number in range(1, 4):
        original = project_dir / f"A{number:03d}.mp4"
        compressed = cfg.compressed_dir / f"A{number:03d}.mp4"
        original.write_bytes(b"original")
        compressed.write_bytes(b"compressed")
        clips.append(
            {
                "index": f"{number:03d}",
                "title": f"clip {number}",
                "_original_path": str(original),
                "_compressed_path": str(compressed),
                "segment_offset_sec": 5.5,
            }
        )
    return clips


def _configure_review(cfg: AppConfig) -> None:
    cfg.plan.visual_transition_review = True


def _contact_sheet(cfg: AppConfig) -> Path:
    project_dir = cfg.project_dir
    assert project_dir is not None
    path = project_dir / "transitions.png"
    path.write_bytes(b"png")
    return path


def _review_response(plan: dict[str, Any], transitions: list[str]) -> AIResponse:
    return AIResponse(
        text=json.dumps(
            {
                "items": [
                    {"position": index + 2, "transition": transition} for index, transition in enumerate(transitions)
                ]
            }
        )
    )


def test_adjacent_pairs_use_next_segment_positions_and_frame_timestamps(cfg: AppConfig) -> None:
    pairs = _adjacent_pairs(_plan(), _clips(cfg), cfg)

    assert pairs is not None
    assert [pair.position for pair in pairs] == [2, 3]
    assert [pair.previous.index for pair in pairs] == ["001", "002"]
    assert [pair.next.index for pair in pairs] == ["002", "003"]
    assert pairs[0].previous.media_candidates[0][1] == pytest.approx(9.95)
    assert pairs[0].next.media_candidates[0][1] == pytest.approx(20.05)
    assert pairs[1].previous.media_candidates[0][1] == pytest.approx(29.95)
    assert pairs[1].next.media_candidates[0][1] == pytest.approx(40.05)


def test_adjacent_pairs_are_capped_at_twelve(cfg: AppConfig) -> None:
    plan = _plan()
    plan["sequence"] = [{"index": f"{number:03d}", "use_timeline": "00:00-00:10"} for number in range(1, 16)]
    clips = []
    for number in range(1, 16):
        clip = _clips(cfg)[0]
        clip["index"] = f"{number:03d}"
        clips.append(clip)

    pairs = _adjacent_pairs(plan, clips, cfg)

    assert pairs is not None
    assert [pair.position for pair in pairs] == list(range(2, 14))


def test_frame_spec_prefers_compressed_and_offsets_original_fallback(cfg: AppConfig) -> None:
    clip = _clips(cfg)[0]
    segment = {"use_timeline": "00:10-00:20"}

    preferred = _frame_spec(clip, segment, cfg, at_end=False)
    assert preferred is not None
    assert preferred.media_candidates == (
        (Path(clip["_compressed_path"]), pytest.approx(10.05)),
        (Path(clip["_original_path"]), pytest.approx(15.55)),
    )

    Path(clip["_compressed_path"]).unlink()
    fallback = _frame_spec(clip, segment, cfg, at_end=True)
    assert fallback is not None
    assert fallback.media_candidates == ((Path(clip["_original_path"]), pytest.approx(25.45)),)


def test_extract_frame_retries_original_with_scaled_ffmpeg_args(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clip = _clips(cfg)[0]
    spec = _frame_spec(clip, {"use_timeline": "00:10-00:20"}, cfg, at_end=True)
    assert spec is not None
    calls: list[list[str]] = []

    def _fake_run(args: list[str], ffmpeg: str, **kwargs: object) -> None:
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("compressed frame failed")
        Path(args[-1]).write_bytes(b"frame")

    monkeypatch.setattr("clio.transition_review.run_ffmpeg", _fake_run)
    output = tmp_path / "frame.png"

    assert _extract_frame(spec, output, "ffmpeg", threading.Event()) == output
    assert len(calls) == 2
    assert calls[1][calls[1].index("-i") + 1] == clip["_original_path"]
    assert calls[1][calls[1].index("-ss") + 1] == "25.45"
    assert "scale=640:360:force_original_aspect_ratio=decrease" in calls[1][calls[1].index("-vf") + 1]


def test_contact_sheet_extracts_previous_then_next_frames(
    cfg: AppConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pairs = _adjacent_pairs(_plan(), _clips(cfg), cfg)
    assert pairs is not None
    extracted: list[tuple[str, str]] = []
    ffmpeg_calls: list[list[str]] = []

    def _fake_extract(spec: Any, output: Path, ffmpeg: str, cancel_event: threading.Event | None) -> Path:
        extracted.append((spec.index, output.name))
        output.write_bytes(b"frame")
        return output

    def _fake_run(args: list[str], ffmpeg: str, **kwargs: object) -> None:
        ffmpeg_calls.append(args)
        Path(args[-1]).write_bytes(b"sheet")

    monkeypatch.setattr("clio.transition_review._extract_frame", _fake_extract)
    monkeypatch.setattr("clio.transition_review.run_ffmpeg", _fake_run)

    contact_sheet = _build_contact_sheet(pairs, tmp_path, "ffmpeg", threading.Event())

    assert contact_sheet == tmp_path / "transitions.png"
    assert extracted == [
        ("001", "frame_000.png"),
        ("002", "frame_001.png"),
        ("002", "frame_002.png"),
        ("003", "frame_003.png"),
    ]
    args = ffmpeg_calls[0]
    assert args[args.index("-i") + 1] == str(tmp_path / "frame_%03d.png")
    assert args[args.index("-vf") + 1] == "tile=2x2:padding=0:margin=0"


def test_pairs_payload_contains_only_plan_metadata(cfg: AppConfig) -> None:
    clips = _clips(cfg)
    pairs = _adjacent_pairs(_plan(), clips, cfg)
    assert pairs is not None

    payload = _pairs_payload(pairs)

    assert '"position":2' in payload
    assert '"use_timeline":"00:20-00:30"' in payload
    for clip in clips:
        assert clip["_original_path"] not in payload
        assert clip["_compressed_path"] not in payload


def test_disabled_review_skips_ffmpeg_and_provider(cfg: AppConfig) -> None:
    cfg.plan.visual_transition_review = False
    plan = _plan()

    with (
        patch("clio.transition_review.resolve_binary") as resolve_binary,
        patch("clio.transition_review.get_video_provider") as get_provider,
    ):
        result = review_plan_transitions(plan, cfg, _clips(cfg))

    assert result == plan
    resolve_binary.assert_not_called()
    get_provider.assert_not_called()


def test_short_plan_skips_ffmpeg_and_provider(cfg: AppConfig) -> None:
    _configure_review(cfg)
    plan = _plan()
    plan["sequence"] = plan["sequence"][:1]

    with (
        patch("clio.transition_review.resolve_binary") as resolve_binary,
        patch("clio.transition_review.get_video_provider") as get_provider,
    ):
        result = review_plan_transitions(plan, cfg, _clips(cfg))

    assert result == plan
    resolve_binary.assert_not_called()
    get_provider.assert_not_called()


def test_contact_sheet_failure_returns_original_plan(cfg: AppConfig) -> None:
    _configure_review(cfg)
    plan = _plan()

    with (
        patch("clio.transition_review.resolve_binary", return_value="ffmpeg"),
        patch("clio.transition_review._build_contact_sheet", side_effect=RuntimeError("ffmpeg failed")),
    ):
        result = review_plan_transitions(plan, cfg, _clips(cfg))

    assert result == plan
    assert result is not plan


def test_provider_failure_returns_original_plan(cfg: AppConfig) -> None:
    _configure_review(cfg)
    plan = _plan()

    with (
        patch("clio.transition_review.resolve_binary", return_value="ffmpeg"),
        patch("clio.transition_review._build_contact_sheet", return_value=_contact_sheet(cfg)),
        patch("clio.transition_review.get_video_provider", side_effect=RuntimeError("no Gemini")),
    ):
        result = review_plan_transitions(plan, cfg, _clips(cfg))

    assert result == plan


def test_invalid_json_returns_original_plan(cfg: AppConfig) -> None:
    _configure_review(cfg)
    plan = _plan()
    provider = MagicMock()
    provider.analyze_images.return_value = AIResponse(text="not json")

    with (
        patch("clio.transition_review.resolve_binary", return_value="ffmpeg"),
        patch("clio.transition_review._build_contact_sheet", return_value=_contact_sheet(cfg)),
        patch("clio.transition_review.get_video_provider", return_value=(provider, "gemini-2.5-flash")),
    ):
        result = review_plan_transitions(plan, cfg, _clips(cfg))

    assert result == plan


def test_interrupted_contact_sheet_propagates(cfg: AppConfig) -> None:
    _configure_review(cfg)

    with (
        patch("clio.transition_review.resolve_binary", return_value="ffmpeg"),
        patch("clio.transition_review._build_contact_sheet", side_effect=InterruptedError("stop")),
        pytest.raises(InterruptedError, match="stop"),
    ):
        review_plan_transitions(_plan(), cfg, _clips(cfg))


def test_interrupted_provider_propagates(cfg: AppConfig) -> None:
    _configure_review(cfg)
    provider = MagicMock()
    provider.analyze_images.side_effect = InterruptedError("stop")

    with (
        patch("clio.transition_review.resolve_binary", return_value="ffmpeg"),
        patch("clio.transition_review._build_contact_sheet", return_value=_contact_sheet(cfg)),
        patch("clio.transition_review.get_video_provider", return_value=(provider, "gemini-2.5-flash")),
        pytest.raises(InterruptedError, match="stop"),
    ):
        review_plan_transitions(_plan(), cfg, _clips(cfg))


def test_review_updates_only_transition_fields(cfg: AppConfig) -> None:
    _configure_review(cfg)
    plan = _plan()
    original = copy.deepcopy(plan)
    clips = _clips(cfg)
    contact_sheet = _contact_sheet(cfg)
    event = threading.Event()
    provider = MagicMock()
    provider.analyze_images.return_value = _review_response(plan, ["match_action", "ambient_audio"])

    with (
        patch("clio.transition_review.resolve_binary", return_value="ffmpeg"),
        patch("clio.transition_review._build_contact_sheet", return_value=contact_sheet),
        patch("clio.transition_review.get_video_provider", return_value=(provider, "gemini-2.5-flash")),
    ):
        result = review_plan_transitions(plan, cfg, clips, cancel_event=event, task_prompts={})

    expected = copy.deepcopy(original)
    expected["sequence"][1]["transition"] = "match_action"
    expected["sequence"][2]["transition"] = "ambient_audio"
    assert result == expected
    assert plan == original
    args, kwargs = provider.analyze_images.call_args
    assert args[0] == [str(contact_sheet)]
    assert '"position":2' in args[1]
    assert str(clips[0]["_compressed_path"]) not in args[1]
    assert kwargs["cancel_event"] is event
    assert kwargs["structured_output"] is TRANSITION_REVIEW_OUTPUT


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [{"position": 2, "transition": "hard_cut"}]},
        {
            "items": [
                {"position": 2, "transition": "hard_cut"},
                {"position": 3, "transition": "hard_cut"},
                {"position": 4, "transition": "hard_cut"},
            ]
        },
        {
            "items": [
                {"position": 2, "transition": "hard_cut"},
                {"position": 2, "transition": "hard_cut"},
            ]
        },
        {
            "items": [
                {"position": 2, "transition": "hard_cut"},
                {"position": 3, "transition": "wipe"},
            ]
        },
        {
            "items": [
                {"position": 2, "transition": "hard_cut"},
                {"position": 3, "transition": "hard_cut", "extra": True},
            ]
        },
    ],
)
def test_apply_review_rejects_incomplete_or_invalid_results(
    payload: dict[str, Any],
) -> None:
    plan = _plan()

    assert _apply_review(plan, json.dumps(payload), {2, 3}) == plan


def test_transition_review_schema_is_strict() -> None:
    assert (
        TransitionReview.model_validate({"items": [{"position": 2, "transition": "match_action"}]}).items[0].transition
        == "match_action"
    )
    with pytest.raises(ValidationError):
        TransitionReview.model_validate({"items": [{"position": 2, "transition": "match_action", "extra": True}]})
    with pytest.raises(ValidationError):
        TransitionReview.model_validate({"items": [{"position": 2, "transition": "wipe"}]})


def test_plan_projection_removes_internal_media_paths(cfg: AppConfig) -> None:
    projected = build_plan_clips(_clips(cfg), cfg)

    assert all("_original_path" not in clip for clip in projected)
    assert all("_compressed_path" not in clip for clip in projected)

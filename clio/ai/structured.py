from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from clio.ai.base import StructuredOutput


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisTimelineItem(_StrictModel):
    start: str
    end: str
    description: str
    score: int | None = Field(default=None, ge=1, le=5)
    camera: Literal["fixed", "handheld", "pan", "zoom", "follow"] | None = None
    quality: Literal["ok", "shaky", "dark", "blocked"] | None = None
    shot_size: Literal["extreme_wide", "wide", "medium", "close_up", "extreme_close_up"] | None = None
    camera_direction: Literal["static", "left", "right", "forward", "backward", "up", "down", "orbit"] | None = None
    first_frame: str | None = None
    last_frame: str | None = None
    energy: int | None = Field(default=None, ge=1, le=5)
    audio: str | None = None
    tags: list[str] = Field(default_factory=list)


class AnalysisKeyMoment(_StrictModel):
    start: str
    end: str
    title: str
    why: str
    score: int | None = Field(default=None, ge=1, le=5)


class AnalysisHighlight(_StrictModel):
    start: str
    description: str
    reason: str


class VideoAnalysis(_StrictModel):
    title: str
    summary: str
    location: str
    mood: str
    timeline: list[AnalysisTimelineItem]
    key_moments: list[AnalysisKeyMoment] = Field(default_factory=list)
    highlights: list[AnalysisHighlight]
    suggested_use: str
    cover_timestamp: str


class Voiceover(_StrictModel):
    index: str
    title: str
    voiceover: str
    duration_hint_sec: float
    edit_tip: str


class PlanSegment(_StrictModel):
    index: str
    title: str
    reason: str
    use_timeline: str
    voiceover_hint: str
    narrative_beat: str
    transition: str


class DailyVlogPlan(_StrictModel):
    day_title: str
    theme: str
    total_estimated_sec: float
    sequence: list[PlanSegment]
    opening_tip: str
    ending_tip: str


class PlanVoiceoverSegment(_StrictModel):
    position: int
    voiceover: str


class PlanVoiceover(_StrictModel):
    segments: list[PlanVoiceoverSegment]


class WindowSummary(_StrictModel):
    title: str
    summary: str
    location: str
    mood: str
    suggested_use: str


def _output(name: str, model: type[BaseModel]) -> StructuredOutput:
    schema: dict[str, Any] = model.model_json_schema()
    return StructuredOutput(name=name, schema=schema)


VIDEO_ANALYSIS_OUTPUT = _output("video_analysis", VideoAnalysis)
VOICEOVER_OUTPUT = _output("voiceover", Voiceover)
PLAN_VOICEOVER_OUTPUT = _output("plan_voiceover", PlanVoiceover)
VLOG_PLAN_OUTPUT = _output("vlog_plan", DailyVlogPlan)
WINDOW_SUMMARY_OUTPUT = _output("window_summary", WindowSummary)
JSON_OBJECT_OUTPUT = StructuredOutput(name="json_object")

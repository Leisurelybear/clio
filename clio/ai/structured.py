from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from clio.ai.base import StructuredOutput


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisTimelineItem(_StrictModel):
    start: str
    end: str
    description: str


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
VLOG_PLAN_OUTPUT = _output("vlog_plan", DailyVlogPlan)
WINDOW_SUMMARY_OUTPUT = _output("window_summary", WindowSummary)
JSON_OBJECT_OUTPUT = StructuredOutput(name="json_object")

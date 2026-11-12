from __future__ import annotations

import pytest

from clio.ai.base import StructuredOutput
from clio.ai.structured import (
    JSON_OBJECT_OUTPUT,
    VIDEO_ANALYSIS_OUTPUT,
    VLOG_PLAN_OUTLINE_OUTPUT,
    VLOG_PLAN_OUTPUT,
    VLOG_PLAN_REVIEW_OUTPUT,
    VOICEOVER_OUTPUT,
    WINDOW_SUMMARY_OUTPUT,
)


def test_fixed_task_schemas_are_strict_and_required() -> None:
    outputs = (
        VIDEO_ANALYSIS_OUTPUT,
        VOICEOVER_OUTPUT,
        VLOG_PLAN_OUTPUT,
        VLOG_PLAN_OUTLINE_OUTPUT,
        VLOG_PLAN_REVIEW_OUTPUT,
        WINDOW_SUMMARY_OUTPUT,
    )

    for output in outputs:
        assert output.schema is not None
        assert output.schema["type"] == "object"
        assert output.schema["additionalProperties"] is False
        assert output.schema["required"]


def test_video_analysis_schema_contains_timeline_and_highlights() -> None:
    schema = VIDEO_ANALYSIS_OUTPUT.schema
    assert schema is not None
    assert set(schema["properties"]) >= {
        "title",
        "summary",
        "location",
        "mood",
        "timeline",
        "key_moments",
        "highlights",
        "suggested_use",
        "cover_timestamp",
    }


def test_json_object_output_has_no_schema() -> None:
    assert JSON_OBJECT_OUTPUT.name == "json_object"
    assert JSON_OBJECT_OUTPUT.schema is None


def test_structured_output_rejects_invalid_name() -> None:
    with pytest.raises(ValueError, match="StructuredOutput.name"):
        StructuredOutput("not valid")


def test_video_analysis_schema_contains_timeline_significance_fields() -> None:
    schema = VIDEO_ANALYSIS_OUTPUT.schema
    assert schema is not None
    timeline_schema = schema["$defs"]["AnalysisTimelineItem"]
    assert set(timeline_schema["properties"]) >= {
        "score",
        "camera",
        "quality",
        "shot_size",
        "camera_direction",
        "first_frame",
        "last_frame",
        "energy",
        "audio",
        "tags",
    }
    moment_schema = schema["$defs"]["AnalysisKeyMoment"]
    assert set(moment_schema["properties"]) >= {"start", "end", "title", "why", "score"}


def test_plan_review_schema_contains_issues_and_corrected_plan() -> None:
    schema = VLOG_PLAN_REVIEW_OUTPUT.schema
    assert schema is not None
    assert set(schema["properties"]) == {"issues", "corrected_plan"}
    assert set(schema["$defs"]["DailyVlogPlan"]["properties"]) == {
        "day_title",
        "theme",
        "total_estimated_sec",
        "sequence",
        "opening_tip",
        "ending_tip",
    }


def test_plan_outline_schema_contains_candidate_beats() -> None:
    schema = VLOG_PLAN_OUTLINE_OUTPUT.schema
    assert schema is not None
    assert set(schema["properties"]) == {"day_title", "theme", "beats", "opening_tip", "ending_tip"}
    beat_schema = schema["$defs"]["PlanOutlineBeat"]
    assert set(beat_schema["properties"]) >= {
        "position",
        "narrative_beat",
        "purpose",
        "target_duration_sec",
        "candidate_indices",
    }

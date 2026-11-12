from __future__ import annotations

import pytest

from clio.ai.base import StructuredOutput
from clio.ai.structured import (
    JSON_OBJECT_OUTPUT,
    VIDEO_ANALYSIS_OUTPUT,
    VLOG_PLAN_OUTPUT,
    VOICEOVER_OUTPUT,
    WINDOW_SUMMARY_OUTPUT,
)


def test_fixed_task_schemas_are_strict_and_required() -> None:
    outputs = (
        VIDEO_ANALYSIS_OUTPUT,
        VOICEOVER_OUTPUT,
        VLOG_PLAN_OUTPUT,
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

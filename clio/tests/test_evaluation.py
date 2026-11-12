"""Tests for deterministic analysis evaluation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import clio.i18n as i18n
from clio.evaluation import (
    evaluate_manifest,
    load_evaluation_manifest,
    render_evaluation_markdown,
    run_evaluation,
    score_analysis_prediction,
)


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _reference() -> dict:
    return {
        "_schema_version": 1,
        "review_status": "reviewed",
        "duration_sec": 60,
        "required_facts": [
            {"id": "market", "description": "visited the market", "aliases": ["market", "tea"]},
            {"id": "lanterns", "description": "saw red lanterns", "aliases": ["lanterns"]},
        ],
        "key_moments": [
            {"id": "opening", "label": "Opening walk", "start": "00:00", "end": "00:10"},
            {"id": "tea", "label": "Tea purchase", "start": "00:35", "end": "00:45"},
        ],
        "density": {
            "min_coverage_ratio": 1.0,
            "min_entries_per_min": 1.0,
            "max_entries_per_min": 5.0,
        },
    }


def _prediction() -> dict:
    return {
        "title": "Old town morning",
        "summary": "We walked through the market and saw red lanterns.",
        "location": "Lijiang",
        "mood": "calm",
        "timeline": [
            {"start": "00:00", "end": "00:30", "description": "Walking through the old town"},
            {"start": "00:30", "end": "01:00", "description": "Buying tea at the market"},
        ],
        "highlights": [{"start": "00:10", "description": "Red lanterns overhead"}],
        "suggested_use": "opening",
        "cover_timestamp": "00:10",
    }


def _manifest(tmp_path: Path, *, prediction: str = "prediction.json", reference: str = "reference.json") -> Path:
    return _write_json(
        tmp_path / "manifest.json",
        {
            "_schema_version": 1,
            "suite": "fixture-v1",
            "cases": [
                {
                    "id": "case-one",
                    "prediction": prediction,
                    "reference": reference,
                    "duration_sec": 60,
                }
            ],
        },
    )


def _score(prediction: dict, reference: dict) -> dict:
    return score_analysis_prediction(prediction, reference, case_id="case-one")


def test_perfect_prediction_scores_100():
    result = _score(_prediction(), _reference())

    assert result["score"] == 100
    assert {name: metric["score"] for name, metric in result["metrics"].items()} == {
        "coverage": 100,
        "density": 100,
        "facts": 100,
        "key_moments": 100,
        "structure": 100,
    }


def test_coverage_gap_reduces_only_coverage():
    prediction = _prediction()
    prediction["timeline"] = [
        {"start": "00:00", "end": "00:30", "description": "First half"},
    ]

    result = _score(prediction, _reference())

    assert result["metrics"]["coverage"]["score"] == 50
    assert result["metrics"]["density"]["score"] == 100
    assert result["metrics"]["facts"]["score"] == 100
    assert result["metrics"]["key_moments"]["score"] == 50
    assert result["score"] == 77.5


def test_density_upper_bound_reduces_density_score():
    prediction = _prediction()
    prediction["timeline"] = [
        {"start": index * 10, "end": index * 10 + 10, "description": f"Part {index}"} for index in range(6)
    ]

    result = _score(prediction, _reference())

    assert result["metrics"]["density"]["score"] < 100
    assert result["metrics"]["coverage"]["score"] == 100
    assert result["score"] < 100


def test_missing_fact_and_alias_matching():
    prediction = _prediction()
    prediction["summary"] = "We bought tea."
    prediction["timeline"] = [
        {"start": "00:00", "end": "00:30", "description": "First half"},
        {"start": "00:30", "end": "01:00", "description": "Second half"},
    ]
    prediction["highlights"] = [{"start": "00:10", "description": "A view"}]
    result = _score(prediction, _reference())
    assert result["metrics"]["facts"]["score"] == 50
    assert result["metrics"]["facts"]["missing"] == ["lanterns"]

    alias_reference = _reference()
    alias_reference["required_facts"] = [
        {"id": "old-town", "description": "historic center", "aliases": ["old town"]},
    ]
    alias_prediction = _prediction()
    alias_prediction["summary"] = "A morning in the OLD TOWN."
    alias_result = _score(alias_prediction, alias_reference)
    assert alias_result["metrics"]["facts"]["score"] == 100
    assert alias_result["metrics"]["facts"]["matches"][0]["alias"] == "old town"


def test_key_moment_requires_configured_overlap_ratio():
    reference = _reference()
    reference["key_moments"] = [
        {"id": "central", "start": "00:10", "end": "00:20", "min_overlap_ratio": 0.6},
    ]
    prediction = _prediction()
    prediction["timeline"] = [
        {"start": "00:00", "end": "00:10", "description": "Before"},
        {"start": "00:15", "end": "00:20", "description": "Half overlap"},
        {"start": "00:20", "end": "01:00", "description": "After"},
    ]

    result = _score(prediction, reference)

    moment = result["metrics"]["key_moments"]["missing"][0]
    assert moment["overlap_ratio"] == 0.5
    assert result["metrics"]["key_moments"]["score"] == 0


def test_structure_missing_field_reduces_score():
    prediction = _prediction()
    prediction.pop("title")

    result = _score(prediction, _reference())

    assert result["metrics"]["structure"]["score"] == 87.5
    assert result["score"] == 98.12


def test_manifest_rejects_schema_duplicate_ids_and_path_escape(tmp_path):
    manifest_path = _manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["_schema_version"] = 2
    _write_json(manifest_path, payload)
    with pytest.raises(ValueError):
        load_evaluation_manifest(manifest_path)

    manifest_path = _manifest(tmp_path)
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["cases"].append(dict(payload["cases"][0]))
    _write_json(manifest_path, payload)
    with pytest.raises(ValueError):
        load_evaluation_manifest(manifest_path)

    manifest_path = _manifest(tmp_path, reference="../outside.json")
    with pytest.raises(ValueError):
        load_evaluation_manifest(manifest_path)


def test_versioned_suite_has_12_reviewed_real_footage_cases():
    root = Path(__file__).resolve().parents[2] / "docs/analysis/2026-11-12-analysis-evaluation-suite-v1"
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["_schema_version"] == 1
    assert len(manifest["cases"]) == 12
    assert len({case["id"] for case in manifest["cases"]}) == 12
    for case in manifest["cases"]:
        reference = json.loads((root / case["reference"]).read_text(encoding="utf-8"))
        assert reference["review_status"] == "reviewed"
        assert reference["duration_sec"] > 0
        assert "SCAFFOLD ONLY" not in case.get("description", "")
        assert (root / case["prediction"]).is_file()

    report = evaluate_manifest(load_evaluation_manifest(manifest_path), min_score=70)
    assert report["summary"]["scored_count"] == 12
    assert report["summary"]["error_count"] == 0
    assert report["gate"]["passed"] is True


def test_manifest_rejects_absolute_reference_path(tmp_path):
    outside = _write_json(tmp_path / "outside.json", _reference())
    manifest_path = _manifest(tmp_path, reference=str(outside))

    with pytest.raises(ValueError):
        load_evaluation_manifest(manifest_path)


def test_reference_requires_explicit_review_status():
    reference = _reference()
    reference.pop("review_status")

    with pytest.raises(ValueError, match="missing"):
        _score(_prediction(), reference)


def test_reference_rejects_duplicate_fact_and_moment_ids():
    duplicate_facts = _reference()
    duplicate_facts["required_facts"].append(dict(duplicate_facts["required_facts"][0]))
    with pytest.raises(ValueError):
        _score(_prediction(), duplicate_facts)

    duplicate_moments = _reference()
    duplicate_moments["key_moments"].append(dict(duplicate_moments["key_moments"][0]))
    with pytest.raises(ValueError):
        _score(_prediction(), duplicate_moments)


def test_predictions_directory_uses_case_id_then_safe_basename(tmp_path):
    manifest_path = _manifest(tmp_path, prediction="nested/source.json")
    predictions = tmp_path / "predictions"
    _write_json(predictions / "source.json", _prediction())
    _write_json(tmp_path / "reference.json", _reference())

    manifest = load_evaluation_manifest(manifest_path, predictions_dir=predictions)

    assert manifest.cases[0].prediction_path == (predictions / "source.json").resolve()


def test_report_writes_json_and_markdown_and_passes_gate(tmp_path, monkeypatch):
    manifest_path = _manifest(tmp_path)
    _write_json(tmp_path / "prediction.json", _prediction())
    _write_json(tmp_path / "reference.json", _reference())
    out_dir = tmp_path / "reports"
    monkeypatch.setenv("CLIO_LANG", "en")
    i18n._reset_cache_for_tests()

    result = run_evaluation(manifest_path, out_dir=out_dir, min_score=70)

    assert result == 0
    report = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert report["summary"]["mean_score"] == 100
    assert report["gate"]["passed"] is True
    markdown = (out_dir / "evaluation.md").read_text(encoding="utf-8")
    assert "Analysis evaluation: fixture-v1" in markdown
    assert "Minimum-score gate passed" in markdown


def test_minimum_score_gate_returns_nonzero_and_records_reason(tmp_path, monkeypatch):
    manifest_path = _manifest(tmp_path)
    prediction = _prediction()
    prediction["summary"] = "No required facts here."
    prediction["timeline"] = [
        {"start": "00:00", "end": "00:30", "description": "First half"},
        {"start": "00:30", "end": "01:00", "description": "Second half"},
    ]
    prediction["highlights"] = [{"start": "00:10", "description": "A view"}]
    _write_json(tmp_path / "prediction.json", prediction)
    _write_json(tmp_path / "reference.json", _reference())
    out_dir = tmp_path / "reports"
    monkeypatch.setenv("CLIO_LANG", "en")
    i18n._reset_cache_for_tests()

    result = run_evaluation(manifest_path, out_dir=out_dir, min_score=90)

    report = json.loads((out_dir / "evaluation.json").read_text(encoding="utf-8"))
    assert result == 1
    assert report["summary"]["mean_score"] < 90
    assert report["gate"]["below_min_score"] is True
    assert report["gate"]["passed"] is False


def test_case_error_fails_gate_even_without_minimum_score(tmp_path):
    manifest_path = _manifest(tmp_path)
    _write_json(tmp_path / "reference.json", _reference())
    manifest = load_evaluation_manifest(manifest_path)

    report = evaluate_manifest(manifest)

    assert report["summary"]["error_count"] == 1
    assert report["gate"]["passed"] is False
    assert "prediction" in report["cases"][0]["error"]


def test_markdown_renderer_handles_case_errors(monkeypatch):
    monkeypatch.setenv("CLIO_LANG", "en")
    i18n._reset_cache_for_tests()
    report = {
        "suite": "fixture",
        "summary": {
            "case_count": 1,
            "scored_count": 0,
            "error_count": 1,
            "mean_score": 0.0,
            "min_case_score": 0.0,
        },
        "gate": {"min_score": 70.0, "passed": False},
        "cases": [{"id": "broken", "status": "error", "score": 0.0, "error": "bad json"}],
    }

    markdown = render_evaluation_markdown(report)

    assert "broken" in markdown
    assert "bad json" in markdown
    assert "gate failed" in markdown.lower()

"""Deterministic evaluation for video-analysis JSON artifacts."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clio.i18n import t
from clio.utils import write_text_atomic

MANIFEST_SCHEMA_VERSION = 1
REFERENCE_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
METRIC_WEIGHTS: dict[str, float] = {
    "coverage": 0.20,
    "density": 0.15,
    "facts": 0.25,
    "key_moments": 0.25,
    "structure": 0.15,
}
DEFAULT_REQUIRED_STRUCTURE = (
    "title",
    "summary",
    "location",
    "mood",
    "timeline",
    "highlights",
    "suggested_use",
    "cover_timestamp",
)
_CASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    prediction_path: Path
    reference_path: Path
    duration_sec: float | None
    description: str = ""


@dataclass(frozen=True)
class EvaluationManifest:
    suite: str
    cases: tuple[EvaluationCase, ...]


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(t("evaluation.error.file_missing", {"label": label, "path": path})) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(t("evaluation.error.json_invalid", {"label": label, "path": path, "detail": exc})) from exc
    if not isinstance(payload, dict):
        raise ValueError(t("evaluation.error.json_object_required", {"label": label, "path": path}))
    return payload


def _text_field(value: Any, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(t("evaluation.error.field_empty", {"field": label}))
    return text


def _positive_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(t("evaluation.error.number_required", {"field": label}))
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(t("evaluation.error.number_required", {"field": label})) from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(t("evaluation.error.number_required", {"field": label}))
    return number


def _optional_positive_number(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    return _positive_number(value, label=label)


def _resolve_manifest_path(root: Path, raw: Any, *, field: str, case_id: str) -> Path:
    text = _text_field(raw, label=f"{case_id}.{field}")
    relative = Path(text)
    if relative.is_absolute() or relative.drive or relative.anchor:
        raise ValueError(t("evaluation.error.path_absolute", {"path": text}))
    resolved = (root / relative).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(t("evaluation.error.path_escape", {"path": text}))
    return resolved


def load_evaluation_manifest(
    manifest_path: Path,
    *,
    predictions_dir: Path | None = None,
) -> EvaluationManifest:
    manifest_path = manifest_path.resolve()
    payload = _read_json_object(manifest_path, label="manifest")
    version = payload.get("_schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            t(
                "evaluation.error.schema_version",
                {"label": "manifest", "actual": version, "expected": MANIFEST_SCHEMA_VERSION},
            )
        )
    suite = _text_field(payload.get("suite"), label="suite")
    cases_raw = payload.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise ValueError(t("evaluation.error.cases_required"))

    root = manifest_path.parent
    prediction_root = predictions_dir.resolve() if predictions_dir is not None else None
    seen: set[str] = set()
    cases: list[EvaluationCase] = []
    for index, raw_case in enumerate(cases_raw, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(t("evaluation.error.case_object_required", {"index": index}))
        case_id = _text_field(raw_case.get("id"), label=f"cases[{index}].id")
        if not _CASE_ID.fullmatch(case_id):
            raise ValueError(t("evaluation.error.case_id_invalid", {"case_id": case_id}))
        if case_id in seen:
            raise ValueError(t("evaluation.error.case_id_duplicate", {"case_id": case_id}))
        seen.add(case_id)

        reference_path = _resolve_manifest_path(
            root,
            raw_case.get("reference"),
            field="reference",
            case_id=case_id,
        )
        prediction_raw = raw_case.get("prediction")
        if prediction_root is None:
            prediction_path = _resolve_manifest_path(
                root,
                prediction_raw,
                field="prediction",
                case_id=case_id,
            )
        else:
            prediction_path = prediction_root / f"{case_id}.json"
            if not prediction_path.is_file() and prediction_raw:
                prediction_path = prediction_root / Path(str(prediction_raw)).name
        duration = _optional_positive_number(raw_case.get("duration_sec"), label=f"{case_id}.duration_sec")
        description = str(raw_case.get("description") or "").strip()
        cases.append(
            EvaluationCase(
                case_id=case_id,
                prediction_path=prediction_path,
                reference_path=reference_path,
                duration_sec=duration,
                description=description,
            )
        )
    return EvaluationManifest(suite=suite, cases=tuple(cases))


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        values = [float(part) for part in parts]
    except ValueError:
        return None
    if any(not math.isfinite(part) or part < 0 for part in values):
        return None
    if len(values) == 3:
        hours, minutes, seconds = values
        if minutes >= 60 or seconds >= 60:
            return None
        return hours * 3600 + minutes * 60 + seconds
    if len(values) == 2:
        minutes, seconds = values
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    return values[0]


def _merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    ordered = sorted((start, end) for start, end in intervals if end > start)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1] + 0.05:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def _prediction_intervals(
    prediction: dict[str, Any], duration_sec: float
) -> tuple[list[tuple[float, float]], list[str]]:
    raw_timeline = prediction.get("timeline")
    if not isinstance(raw_timeline, list) or not raw_timeline:
        return [], [t("evaluation.issue.timeline_missing")]
    intervals: list[tuple[float, float]] = []
    issues: list[str] = []
    for position, entry in enumerate(raw_timeline, start=1):
        if not isinstance(entry, dict):
            issues.append(t("evaluation.issue.timeline_entry_object", {"position": position}))
            continue
        start = _timestamp_seconds(entry.get("start"))
        end = _timestamp_seconds(entry.get("end"))
        description = str(entry.get("description") or "").strip()
        if start is None or end is None:
            issues.append(t("evaluation.issue.timeline_timestamp", {"position": position}))
            continue
        if end <= start:
            issues.append(t("evaluation.issue.timeline_order", {"position": position}))
            continue
        if not description:
            issues.append(t("evaluation.issue.timeline_description", {"position": position}))
            continue
        if start > duration_sec + 0.05 or end > duration_sec + 0.05:
            issues.append(
                t(
                    "evaluation.issue.timeline_bounds",
                    {"position": position, "duration": f"{duration_sec:.2f}"},
                )
            )
        intervals.append((start, end))
    return intervals, issues


def _validated_facts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(t("evaluation.error.facts_required"))
    facts: list[dict[str, Any]] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(t("evaluation.error.fact_object_required", {"position": position}))
        fact_id = _text_field(item.get("id"), label=f"required_facts[{position}].id")
        description = str(item.get("description") or "").strip()
        raw_aliases = item.get("aliases", [])
        if raw_aliases is None:
            raw_aliases = []
        if not isinstance(raw_aliases, list):
            raise ValueError(t("evaluation.error.fact_aliases", {"fact_id": fact_id}))
        aliases = [str(alias).strip() for alias in raw_aliases if str(alias).strip()]
        if not aliases and description:
            aliases = [description]
        if not aliases:
            raise ValueError(t("evaluation.error.fact_aliases", {"fact_id": fact_id}))
        if any(fact["id"] == fact_id for fact in facts):
            raise ValueError(t("evaluation.error.fact_id_duplicate", {"fact_id": fact_id}))
        facts.append({"id": fact_id, "description": description, "aliases": aliases})
    return facts


def _validated_key_moments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(t("evaluation.error.key_moments_required"))
    moments: list[dict[str, Any]] = []
    for position, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(t("evaluation.error.key_moment_object_required", {"position": position}))
        moment_id = _text_field(item.get("id"), label=f"key_moments[{position}].id")
        start = _timestamp_seconds(item.get("start_sec", item.get("start")))
        end = _timestamp_seconds(item.get("end_sec", item.get("end")))
        if start is None or end is None or end <= start:
            raise ValueError(t("evaluation.error.key_moment_range", {"moment_id": moment_id}))
        overlap = item.get("min_overlap_ratio", 0.5)
        try:
            overlap_ratio = float(overlap)
        except (TypeError, ValueError) as exc:
            raise ValueError(t("evaluation.error.key_moment_overlap", {"moment_id": moment_id})) from exc
        if not math.isfinite(overlap_ratio) or not 0 < overlap_ratio <= 1:
            raise ValueError(t("evaluation.error.key_moment_overlap", {"moment_id": moment_id}))
        if any(moment["id"] == moment_id for moment in moments):
            raise ValueError(t("evaluation.error.key_moment_id_duplicate", {"moment_id": moment_id}))
        moments.append(
            {
                "id": moment_id,
                "label": str(item.get("label") or moment_id).strip(),
                "start": start,
                "end": end,
                "min_overlap_ratio": overlap_ratio,
            }
        )
    return moments


def _validated_density(raw: Any) -> dict[str, float]:
    payload = raw if isinstance(raw, dict) else {}
    min_coverage = payload.get("min_coverage_ratio", 0.8)
    min_entries = payload.get("min_entries_per_min", 1.0)
    max_entries = payload.get("max_entries_per_min", 5.0)
    try:
        coverage = float(min_coverage)
        lower = float(min_entries)
        upper = float(max_entries)
    except (TypeError, ValueError) as exc:
        raise ValueError(t("evaluation.error.density_invalid")) from exc
    if not math.isfinite(coverage) or not 0 <= coverage <= 1:
        raise ValueError(t("evaluation.error.density_invalid"))
    if not math.isfinite(lower) or not math.isfinite(upper) or lower < 0 or upper <= 0 or upper < lower:
        raise ValueError(t("evaluation.error.density_invalid"))
    return {
        "min_coverage_ratio": coverage,
        "min_entries_per_min": lower,
        "max_entries_per_min": upper,
    }


def _validated_required_structure(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return DEFAULT_REQUIRED_STRUCTURE
    if not isinstance(raw, list) or not raw:
        raise ValueError(t("evaluation.error.structure_required"))
    fields = tuple(str(value).strip() for value in raw if str(value).strip())
    if not fields:
        raise ValueError(t("evaluation.error.structure_required"))
    return fields


def _validated_reference(reference: dict[str, Any], duration_override: float | None) -> dict[str, Any]:
    review_status = str(reference.get("review_status") or "missing").strip().lower()
    if review_status != "reviewed":
        raise ValueError(t("evaluation.error.reference_not_reviewed", {"status": review_status}))
    version = reference.get("_schema_version")
    if version != REFERENCE_SCHEMA_VERSION:
        raise ValueError(
            t(
                "evaluation.error.schema_version",
                {"label": "reference", "actual": version, "expected": REFERENCE_SCHEMA_VERSION},
            )
        )
    reference_duration = _optional_positive_number(reference.get("duration_sec"), label="reference.duration_sec")
    duration = duration_override or reference_duration
    if duration is None:
        raise ValueError(t("evaluation.error.duration_required"))
    key_moments = _validated_key_moments(reference.get("key_moments"))
    for moment in key_moments:
        if float(moment["end"]) > duration + 0.05:
            raise ValueError(t("evaluation.error.key_moment_range", {"moment_id": moment["id"]}))
    return {
        "duration_sec": duration,
        "required_facts": _validated_facts(reference.get("required_facts")),
        "key_moments": key_moments,
        "density": _validated_density(reference.get("density")),
        "required_structure": _validated_required_structure(reference.get("required_structure")),
    }


def _normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _searchable_text(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    return _normalize_text(" ".join(parts))


def _score_facts(prediction: dict[str, Any], facts: list[dict[str, Any]]) -> dict[str, Any]:
    searchable = _searchable_text(prediction)
    matches: list[dict[str, Any]] = []
    missing: list[str] = []
    for fact in facts:
        matched_alias = next(
            (alias for alias in fact["aliases"] if _normalize_text(alias) and _normalize_text(alias) in searchable),
            None,
        )
        if matched_alias is None:
            missing.append(str(fact["id"]))
            continue
        matches.append({"id": fact["id"], "alias": matched_alias})
    total = len(facts)
    score = len(matches) / total * 100 if total else 100.0
    return {"score": score, "matches": matches, "missing": missing, "total": total}


def _score_key_moments(intervals: list[tuple[float, float]], moments: list[dict[str, Any]]) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for moment in moments:
        reference_start = float(moment["start"])
        reference_end = float(moment["end"])
        reference_duration = reference_end - reference_start
        best = 0.0
        for start, end in intervals:
            overlap = max(0.0, min(end, reference_end) - max(start, reference_start))
            best = max(best, overlap / reference_duration)
        matched = best + 1e-9 >= float(moment["min_overlap_ratio"])
        item = {"id": moment["id"], "label": moment["label"], "overlap_ratio": round(best, 4), "matched": matched}
        if matched:
            matches.append(item)
        else:
            missing.append(item)
    total = len(moments)
    score = len(matches) / total * 100 if total else 100.0
    return {"score": score, "matches": matches, "missing": missing, "total": total}


def _score_structure(
    prediction: dict[str, Any],
    required_structure: tuple[str, ...],
    duration_sec: float,
    timeline_issues: list[str],
) -> dict[str, Any]:
    valid: dict[str, bool] = {}
    issues: list[str] = []
    if "timeline" in required_structure and timeline_issues:
        issues.extend(timeline_issues)
    for field in required_structure:
        if field == "timeline":
            passed = (
                isinstance(prediction.get("timeline"), list)
                and bool(prediction.get("timeline"))
                and not timeline_issues
            )
        elif field == "highlights":
            raw = prediction.get("highlights")
            if not isinstance(raw, list) or not raw:
                passed = False
            else:
                passed = True
                for item in raw:
                    if isinstance(item, str):
                        passed = passed and bool(item.strip())
                    elif isinstance(item, dict):
                        passed = passed and bool(str(item.get("description") or "").strip())
                        passed = passed and _timestamp_seconds(item.get("start")) is not None
                    else:
                        passed = False
        elif field == "cover_timestamp":
            timestamp = _timestamp_seconds(prediction.get("cover_timestamp"))
            passed = timestamp is not None and timestamp <= duration_sec + 0.05
        else:
            value = prediction.get(field)
            passed = isinstance(value, str) and bool(value.strip())
        valid[field] = passed
        if not passed:
            issues.append(t("evaluation.issue.structure_field", {"field": field}))
    total = len(required_structure)
    score = sum(1 for passed in valid.values() if passed) / total * 100 if total else 100.0
    return {"score": score, "valid_fields": valid, "issues": issues, "total": total}


def _score_coverage(intervals: list[tuple[float, float]], duration_sec: float, target: float) -> dict[str, Any]:
    clipped = [
        (max(0.0, start), min(duration_sec, end)) for start, end in intervals if end > 0 and start < duration_sec
    ]
    merged = _merge_intervals(clipped)
    covered_sec = sum(end - start for start, end in merged)
    ratio = min(1.0, covered_sec / duration_sec)
    score = min(1.0, ratio / target) * 100 if target > 0 else 100.0
    gaps: list[float] = []
    cursor = 0.0
    for start, end in merged:
        if start > cursor + 0.05:
            gaps.append(start - cursor)
        cursor = max(cursor, end)
    if cursor < duration_sec - 0.05:
        gaps.append(duration_sec - cursor)
    return {
        "score": score,
        "coverage_ratio": round(ratio, 4),
        "covered_sec": round(covered_sec, 3),
        "duration_sec": round(duration_sec, 3),
        "target_ratio": target,
        "gap_count": len(gaps),
        "max_gap_sec": round(max(gaps, default=0.0), 3),
    }


def _score_density(
    intervals: list[tuple[float, float]], duration_sec: float, density: dict[str, float]
) -> dict[str, Any]:
    entries = len(intervals)
    entries_per_min = entries / (duration_sec / 60)
    lower = density["min_entries_per_min"]
    upper = density["max_entries_per_min"]
    if entries_per_min < lower:
        score = entries_per_min / lower * 100 if lower > 0 else 100.0
    elif entries_per_min > upper:
        score = upper / entries_per_min * 100
    else:
        score = 100.0
    return {
        "score": score,
        "entries": entries,
        "entries_per_min": round(entries_per_min, 4),
        "min_entries_per_min": lower,
        "max_entries_per_min": upper,
    }


def _weighted_total(metrics: dict[str, dict[str, Any]]) -> float:
    return sum(metrics[name]["score"] * weight for name, weight in METRIC_WEIGHTS.items())


def score_analysis_prediction(
    prediction: dict[str, Any],
    reference: dict[str, Any],
    *,
    case_id: str,
    duration_sec: float | None = None,
) -> dict[str, Any]:
    validated = _validated_reference(reference, duration_sec)
    effective_duration = float(validated["duration_sec"])
    intervals, timeline_issues = _prediction_intervals(prediction, effective_duration)
    metrics = {
        "coverage": _score_coverage(
            intervals,
            effective_duration,
            validated["density"]["min_coverage_ratio"],
        ),
        "density": _score_density(intervals, effective_duration, validated["density"]),
        "facts": _score_facts(prediction, validated["required_facts"]),
        "key_moments": _score_key_moments(intervals, validated["key_moments"]),
        "structure": _score_structure(
            prediction,
            validated["required_structure"],
            effective_duration,
            timeline_issues,
        ),
    }
    total = _weighted_total(metrics)
    return {
        "id": case_id,
        "status": "ok",
        "score": round(total, 2),
        "duration_sec": round(effective_duration, 3),
        "metrics": metrics,
    }


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def evaluate_manifest(
    manifest: EvaluationManifest,
    *,
    min_score: float | None = None,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in manifest.cases:
        try:
            prediction = _read_json_object(case.prediction_path, label=f"prediction:{case.case_id}")
            reference = _read_json_object(case.reference_path, label=f"reference:{case.case_id}")
            result = score_analysis_prediction(
                prediction,
                reference,
                case_id=case.case_id,
                duration_sec=case.duration_sec,
            )
        except ValueError as exc:
            result = {"id": case.case_id, "status": "error", "score": 0.0, "error": str(exc)}
        if case.description:
            result["description"] = case.description
        cases.append(result)

    successful = [case for case in cases if case["status"] == "ok"]
    scores = [float(case["score"]) for case in successful]
    errors = len(cases) - len(successful)
    mean_score = round(_mean(scores), 2)
    metric_means = {
        metric: round(_mean([float(case["metrics"][metric]["score"]) for case in successful]), 2)
        for metric in METRIC_WEIGHTS
    }
    below_gate = min_score is not None and mean_score + 1e-9 < min_score
    gate_passed = errors == 0 and not below_gate
    return {
        "_schema_version": REPORT_SCHEMA_VERSION,
        "suite": manifest.suite,
        "weights": METRIC_WEIGHTS,
        "gate": {
            "min_score": min_score,
            "below_min_score": below_gate,
            "passed": gate_passed,
        },
        "summary": {
            "case_count": len(cases),
            "scored_count": len(successful),
            "error_count": errors,
            "mean_score": mean_score,
            "min_case_score": round(min(scores), 2) if scores else 0.0,
            "max_case_score": round(max(scores), 2) if scores else 0.0,
            "metrics": metric_means,
        },
        "cases": cases,
    }


def _md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_evaluation_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    gate = report["gate"]
    lines = [
        f"# {t('evaluation.report.title', {'suite': report['suite']})}",
        "",
        t(
            "evaluation.report.summary",
            {
                "cases": summary["case_count"],
                "scored": summary["scored_count"],
                "errors": summary["error_count"],
                "mean": f"{summary['mean_score']:.2f}",
                "minimum": f"{summary['min_case_score']:.2f}",
            },
        ),
        "",
        "| Case | Score | Coverage | Density | Facts | Key moments | Structure |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        if case["status"] == "ok":
            metrics = case["metrics"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(case["id"]),
                        f"{case['score']:.2f}",
                        f"{metrics['coverage']['score']:.2f}",
                        f"{metrics['density']['score']:.2f}",
                        f"{metrics['facts']['score']:.2f}",
                        f"{metrics['key_moments']['score']:.2f}",
                        f"{metrics['structure']['score']:.2f}",
                    ]
                )
                + " |"
            )
        else:
            lines.append(f"| {_md_cell(case['id'])} | 0.00 |  |  |  |  |  |")

    lines.extend(["", f"## {t('evaluation.report.gate_title')}", ""])
    if gate["min_score"] is None:
        lines.append(t("evaluation.report.gate_not_set"))
    elif gate["passed"]:
        lines.append(t("evaluation.report.gate_passed", {"score": f"{gate['min_score']:.2f}"}))
    else:
        lines.append(
            t(
                "evaluation.report.gate_failed",
                {"score": f"{gate['min_score']:.2f}", "mean": f"{summary['mean_score']:.2f}"},
            )
        )
    if summary["error_count"]:
        lines.append(t("evaluation.report.gate_errors", {"count": summary["error_count"]}))

    lines.extend(["", f"## {t('evaluation.report.details_title')}", ""])
    for case in report["cases"]:
        lines.append(f"### {case['id']}")
        if case["status"] != "ok":
            lines.append(f"- {t('evaluation.report.error', {'detail': case['error']})}")
            lines.append("")
            continue
        metrics = case["metrics"]
        case_score = f"{case['score']:.2f}"
        lines.append(f"- {t('evaluation.report.case_score', {'score': case_score})}")
        coverage_values = {
            "ratio": metrics["coverage"]["coverage_ratio"],
            "gap": metrics["coverage"]["max_gap_sec"],
        }
        lines.append(f"- {t('evaluation.report.coverage', coverage_values)}")
        density_values = {
            "density": metrics["density"]["entries_per_min"],
            "entries": metrics["density"]["entries"],
        }
        lines.append(f"- {t('evaluation.report.density', density_values)}")
        if metrics["facts"]["missing"]:
            lines.append(f"- {t('evaluation.report.missing_facts', {'items': ', '.join(metrics['facts']['missing'])})}")
        if metrics["key_moments"]["missing"]:
            labels = ", ".join(str(item["id"]) for item in metrics["key_moments"]["missing"])
            lines.append(f"- {t('evaluation.report.missing_moments', {'items': labels})}")
        if metrics["structure"]["issues"]:
            lines.append(
                f"- {t('evaluation.report.structure_issues', {'items': '; '.join(metrics['structure']['issues'])})}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_evaluation(
    manifest_path: Path,
    *,
    predictions_dir: Path | None = None,
    out_dir: Path | None = None,
    min_score: float | None = None,
) -> int:
    if min_score is not None and (not math.isfinite(min_score) or not 0 <= min_score <= 100):
        print(t("cli.error", {"detail": t("evaluation.error.min_score_invalid")}))
        return 2
    try:
        manifest = load_evaluation_manifest(manifest_path, predictions_dir=predictions_dir)
        report = evaluate_manifest(manifest, min_score=min_score)
    except ValueError as exc:
        print(t("cli.error", {"detail": exc}))
        return 2

    destination = out_dir or manifest_path.resolve().parent / "reports"
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "evaluation.json"
    markdown_path = destination / "evaluation.md"
    write_text_atomic(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text_atomic(markdown_path, render_evaluation_markdown(report))
    print(
        t(
            "evaluation.cli.complete",
            {
                "score": f"{report['summary']['mean_score']:.2f}",
                "json": json_path,
                "markdown": markdown_path,
            },
        )
    )
    return 0 if report["gate"]["passed"] else 1

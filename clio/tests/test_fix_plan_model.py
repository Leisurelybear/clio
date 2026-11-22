"""Regression tests for Plan round-trips (A-25)."""

from __future__ import annotations

import json

from clio.plan_model import Plan


def test_schema_version_survives_round_trip():
    data = {"day_title": "d", "sequence": [], "_schema_version": 2}

    assert Plan.from_dict(data).to_dict()["_schema_version"] == 2


def test_missing_or_invalid_schema_version_is_not_invented():
    assert "_schema_version" not in Plan.from_dict({"sequence": []}).to_dict()
    assert "_schema_version" not in Plan.from_dict({"sequence": [], "_schema_version": True}).to_dict()


def test_non_finite_numbers_never_reach_json():
    plan = Plan.from_dict({"sequence": [], "total_estimated_sec": float("nan"), "_confidence": float("inf")})
    dumped = plan.to_dict()

    assert dumped["total_estimated_sec"] == 180
    assert "_confidence" not in dumped
    json.dumps(dumped, allow_nan=False)

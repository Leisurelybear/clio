"""Regression test: plan day labels strip only the trailing _plan marker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from clio.ui.routes.plan import handle_get_plans


def test_day_label_keeps_inner_plan_text(tmp_path: Path):
    (tmp_path / "trip_plan_b_plan.json").write_text("{}", encoding="utf-8")
    (tmp_path / "day1_plan.json").write_text("{}", encoding="utf-8")
    handler = MagicMock()
    handler._get_config.return_value.plans_dir = tmp_path

    handle_get_plans(handler, {})

    labels = [p["day_label"] for p in handler._send_json.call_args.args[0]["plans"]]
    assert labels == ["day1", "trip_plan_b"]

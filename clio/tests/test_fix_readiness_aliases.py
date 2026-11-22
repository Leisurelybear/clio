"""Regression test for A-16b: readiness routes scan media index aliases once."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from clio.ui.routes.plan import handle_post_plan_readiness


def test_plan_readiness_shares_media_aliases(tmp_path: Path, monkeypatch) -> None:
    handler = MagicMock()
    handler._resolve_project_dir.return_value = tmp_path
    cfg = MagicMock()
    cfg.plans_dir = tmp_path / "plans"
    cfg.plan.target_duration_sec = 0
    handler._get_config.return_value = cfg
    handler._send_json = MagicMock()

    aliases = {"7": {"001"}}
    alias_calls: list[Any] = []
    seen: dict[str, Any] = {}

    def fake_aliases(c: Any) -> dict[str, set[str]]:
        alias_calls.append(c)
        return aliases

    def fake_indices(c: Any, **kw: Any) -> tuple[set[str], set[str]]:
        seen["indices"] = kw.get("media_aliases")
        return {"001"}, set()

    def fake_durations(c: Any, **kw: Any) -> dict[str, float]:
        seen["durations"] = kw.get("media_aliases")
        return {"001": 60.0}

    monkeypatch.setattr("clio.ui.routes.plan.collect_media_index_aliases", fake_aliases)
    monkeypatch.setattr("clio.ui.routes.plan.collect_project_indices", fake_indices)
    monkeypatch.setattr("clio.ui.routes.plan.collect_project_media_durations", fake_durations)
    monkeypatch.setattr("clio.ui.routes.plan.collect_project_analysis_timelines", lambda _cfg: {})

    plan = {
        "day_title": "d",
        "total_estimated_sec": 10,
        "sequence": [
            {"index": "001", "title": "t", "reason": "r", "use_timeline": "00:00-00:10", "voiceover_hint": "v"}
        ],
    }
    handle_post_plan_readiness(handler, {}, {"day": "day1", "plan": plan})

    assert alias_calls == [cfg]
    assert seen["indices"] is aliases
    assert seen["durations"] is aliases

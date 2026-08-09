"""Tests for clio/ui/routes/plan.py — plan/cut route handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from clio.ui.routes.plan import (
    handle_get_cut_orphaned_backups,
    handle_get_plan,
    handle_get_plans,
    handle_post_cut,
    handle_post_cut_restore_backups,
    handle_post_plan_readiness,
    handle_put_plan,
)


class TestHandleGetPlans:
    def test_no_plans_dir(self):
        handler = MagicMock()
        handler._get_project_output.return_value = Path("/nonexistent/output")
        handler._send_json = MagicMock()

        handle_get_plans(handler, {})

        handler._send_json.assert_called_once_with({"plans": []})

    def test_lists_plans(self, tmp_path: Path):
        handler = MagicMock()
        proj_out = tmp_path / "output"
        plans_dir = proj_out / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "day1_plan.json").write_bytes(b"{}")
        (plans_dir / "day2_plan.json").write_bytes(b"{}")
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_plans(handler, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert len(payload["plans"]) == 2
        assert payload["plans"][0]["day_label"] == "day1"


class TestHandleGetPlan:
    def test_forbidden_day(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_get_plan(handler, {"day": ["../secret"]})

        handler._send_json.assert_called_once_with({"error": "forbidden"}, 403)

    def test_not_found(self, tmp_path: Path):
        handler = MagicMock()
        proj_out = tmp_path / "output"
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_plan(handler, {"day": ["day1"]})

        args = handler._send_json.call_args
        assert "规划文件不存在" in args[0][0]["error"]
        assert args[0][1] == 404

    def test_found(self, tmp_path: Path):
        handler = MagicMock()
        proj_out = tmp_path / "output"
        plans_dir = proj_out / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "day1_plan.json").write_text(json.dumps({"plan": "test"}), encoding="utf-8")
        handler._get_project_output.return_value = proj_out
        handler._send_bytes = MagicMock()

        handle_get_plan(handler, {"day": ["day1"]})

        handler._send_bytes.assert_called_once()
        args = handler._send_bytes.call_args
        assert b'"plan": "test"' in args[0][0]


class TestHandlePutPlan:
    def test_forbidden_day(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_put_plan(handler, {"day": ["../evil"]}, {"test": True})
        handler._send_json.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)

    def test_saves_plan(self, tmp_path: Path):
        handler = MagicMock()
        proj_out = tmp_path / "output"
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_put_plan(handler, {"day": ["day1"]}, {"title": "test plan"})

        handler._send_json.assert_called_once()
        saved = proj_out / "plans" / "day1_plan.json"
        assert saved.is_file()
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert data["title"] == "test plan"
        assert "_schema_version" in data


class TestHandlePutPlanValidation:
    def test_rejects_invalid_timeline(self, tmp_path: Path):
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path / "output"
        handler._send_json = MagicMock()
        body = {
            "day_title": "d",
            "sequence": [{"index": "001", "use_timeline": "99:99-00:00"}],
        }
        handle_put_plan(handler, {"day": ["day1"]}, body)
        args = handler._send_json.call_args
        assert args[0][1] == 400
        assert args[0][0]["ok"] is False
        assert args[0][0]["issues"]

    def test_saves_normalized_plan_with_schema(self, tmp_path: Path):
        handler = MagicMock()
        proj_out = tmp_path / "output"
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()
        body = {
            "day_title": "d",
            "sequence": [{"index": "001", "use_timeline": "00:00-00:10", "title": "t"}],
        }
        handle_put_plan(handler, {"day": ["day1"]}, body)
        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][0]["ok"] is True
        saved = json.loads((proj_out / "plans" / "day1_plan.json").read_text(encoding="utf-8"))
        assert saved["sequence"][0]["index"] == "001"
        assert "_schema_version" in saved


class TestHandlePostPlanReadiness:
    def test_readiness_with_inline_plan_empty_sequence(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.plans_dir = tmp_path / "plans"
        cfg.compressed_dir = tmp_path / "compressed"
        cfg.texts_dir = tmp_path / "texts"
        cfg.compressed_dir.mkdir()
        cfg.texts_dir.mkdir()
        cfg.project_dir = None
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()
        handle_post_plan_readiness(
            handler,
            {},
            {"day": "day1", "plan": {"day_title": "d", "sequence": []}},
        )
        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is False
        assert payload["errors"]


class TestHandlePostCut:
    def test_invalid_source(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_post_cut(handler, {}, {"source": "invalid"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "source must be compressed|original"}, 400)

    def test_non_string_day_label_returns_400(self):
        """day_label must be str (same as run routes); list/dict must not TypeError → 500."""
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_post_cut(handler, {}, {"day_label": ["day1"], "source": "compressed"})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        status = args[0][1] if len(args[0]) > 1 else None
        assert status == 400
        assert payload.get("ok") is False
        assert "day_label" in payload.get("error", "")

    def test_existing_output_returns_409_without_overwrite(self, tmp_path: Path):
        from clio.config import AppConfig
        from clio.config.models import (
            AnalyzeConfig,
            GlobalConfig,
            GlobalPathsConfig,
            NamingConfig,
            PlanConfig,
            ProjectConfig,
            ProjectPathsConfig,
            ScriptConfig,
        )

        plans = tmp_path / "plans"
        plans.mkdir()
        plan = {
            "day_title": "d",
            "sequence": [{"index": "001", "title": "A", "use_timeline": "00:00-00:05", "reason": "r"}],
        }
        (plans / "day1_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        # Provide a compressed video so index 001 is discoverable (empty scan now
        # means "media missing" and would 400 before reaching the overwrite check).
        comp_dir = tmp_path / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_A.mp4").write_bytes(b"x")
        cuts = tmp_path / "cuts" / "day1"
        cuts.mkdir(parents=True)
        (cuts / "old.mp4").write_bytes(b"\x00")

        cfg = AppConfig(
            global_cfg=GlobalConfig(
                paths=GlobalPathsConfig(ffmpeg="ffmpeg", ffprobe="ffprobe"),
                naming=NamingConfig(index_width=3),
            ),
            project_cfg=ProjectConfig(
                paths=ProjectPathsConfig(output_dir=tmp_path),
                analyze=AnalyzeConfig(
                    skip_existing=True,
                    texts_subdir="texts",
                    compressed_subdir="compressed",
                ),
                script=ScriptConfig(scripts_subdir="scripts"),
                plan=PlanConfig(plans_subdir="plans"),
            ),
            project_dir=tmp_path,
        )
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed", "overwrite": False, "force": True})
        args = handler._send_json.call_args
        payload = args[0][0]
        status = args[0][1] if len(args[0]) > 1 else args[1].get("status") if args[1] else None
        # status may be positional second arg
        if status is None and len(handler._send_json.call_args.args) > 1:
            status = handler._send_json.call_args.args[1]
        assert status == 409
        assert payload["code"] == "cut_output_exists"
        assert payload["count"] == 1
        assert "old.mp4" in payload["files"]


class TestOrphanedCutBackupRoutes:
    def test_get_lists_backups(self, tmp_path: Path):
        day = tmp_path / "cuts" / "day1"
        day.mkdir(parents=True)
        (day / "a.mp4.clio_bak").write_bytes(b"old")
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._send_json = MagicMock()
        handle_get_cut_orphaned_backups(handler, {})
        payload = handler._send_json.call_args[0][0]
        assert payload["count"] == 1
        assert payload["items"][0]["name"] == "a.mp4"

    def test_post_restores(self, tmp_path: Path):
        day = tmp_path / "cuts" / "day1"
        day.mkdir(parents=True)
        bak = day / "a.mp4.clio_bak"
        bak.write_bytes(b"old")
        (day / "a.mp4").write_bytes(b"partial")
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._send_json = MagicMock()
        handle_post_cut_restore_backups(handler, {}, {})
        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is True
        assert payload["count"] == 1
        assert (day / "a.mp4").read_bytes() == b"old"
        assert not bak.exists()

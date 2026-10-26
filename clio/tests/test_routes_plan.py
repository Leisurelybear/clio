"""Tests for clio/ui/routes/plan.py — plan/cut route handlers."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clio.ui.routes.plan import (
    handle_get_cut_orphaned_backups,
    handle_get_plan,
    handle_get_plan_preview_media,
    handle_get_plans,
    handle_post_cut,
    handle_post_cut_restore_backups,
    handle_post_plan_readiness,
    handle_put_plan,
)


def _mock_plans_cfg(handler: MagicMock, plans_dir: Path, proj_dir: Path | None = None) -> None:
    handler._resolve_project_dir.return_value = proj_dir or plans_dir.parent
    cfg = MagicMock()
    cfg.plans_dir = plans_dir
    handler._get_config.return_value = cfg


class TestHandleGetPlans:
    def test_no_plans_dir(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_json = MagicMock()

        handle_get_plans(handler, {})

        handler._send_json.assert_called_once_with({"plans": []})

    def test_lists_plans(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "day1_plan.json").write_bytes(b"{}")
        (plans_dir / "day2_plan.json").write_bytes(b"{}")
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_json = MagicMock()

        handle_get_plans(handler, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert len(payload["plans"]) == 2
        assert payload["plans"][0]["day_label"] == "day1"

    def test_custom_plans_subdir(self, tmp_path: Path):
        handler = MagicMock()
        custom = tmp_path / "output" / "my_plans"
        custom.mkdir(parents=True)
        (custom / "day1_plan.json").write_bytes(b"{}")
        _mock_plans_cfg(handler, custom, tmp_path)
        handler._send_json = MagicMock()

        handle_get_plans(handler, {})

        payload = handler._send_json.call_args[0][0]
        assert len(payload["plans"]) == 1
        assert payload["plans"][0]["day_label"] == "day1"
        assert "my_plans" in payload["plans"][0]["path"]


class TestHandleGetPlan:
    def test_forbidden_day(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_get_plan(handler, {"day": ["../secret"]})

        handler._send_json.assert_called_once_with({"error": "forbidden"}, 403)

    def test_not_found(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_json = MagicMock()

        handle_get_plan(handler, {"day": ["day1"]})

        args = handler._send_json.call_args
        assert "规划文件不存在" in args[0][0]["error"]
        assert args[0][1] == 404

    def test_found(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "day1_plan.json").write_text(json.dumps({"plan": "test"}), encoding="utf-8")
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_bytes = MagicMock()

        handle_get_plan(handler, {"day": ["day1"]})

        handler._send_bytes.assert_called_once()
        assert b"test" in handler._send_bytes.call_args[0][0]

    def test_found_with_sanitized_filename(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "Ep2_-_agnes-3-flash_plan.json").write_text(json.dumps({"plan": "test"}), encoding="utf-8")
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_bytes = MagicMock()

        handle_get_plan(handler, {"day": ["Ep2 - agnes-3-flash"]})

        handler._send_bytes.assert_called_once()
        assert b"test" in handler._send_bytes.call_args[0][0]


class TestHandlePutPlan:
    def test_forbidden_day(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_put_plan(handler, {"day": ["../evil"]}, {"test": True})
        handler._send_json.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)

    def test_saves_plan(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_json = MagicMock()

        handle_put_plan(handler, {"day": ["day1"]}, {"title": "test plan"})

        handler._send_json.assert_called_once()
        saved = plans_dir / "day1_plan.json"
        assert saved.is_file()
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert data["title"] == "test plan"
        assert "_schema_version" in data

    def test_saves_to_custom_plans_subdir(self, tmp_path: Path):
        handler = MagicMock()
        custom = tmp_path / "output" / "storyboard"
        _mock_plans_cfg(handler, custom, tmp_path)
        handler._send_json = MagicMock()

        handle_put_plan(handler, {"day": ["day1"]}, {"title": "custom"})

        saved = custom / "day1_plan.json"
        assert saved.is_file()
        assert (tmp_path / "output" / "plans" / "day1_plan.json").exists() is False

    def test_saves_plan_with_sanitized_filename(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_json = MagicMock()

        handle_put_plan(handler, {"day": ["Ep2 - agnes-3-flash"]}, {"title": "test plan"})

        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][0]["ok"] is True
        assert (plans_dir / "Ep2_-_agnes-3-flash_plan.json").is_file()
        assert not (plans_dir / "Ep2 - agnes-3-flash_plan.json").exists()


class TestHandlePutPlanValidation:
    def test_rejects_invalid_timeline(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        _mock_plans_cfg(handler, plans_dir, tmp_path)
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
        plans_dir = tmp_path / "output" / "plans"
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_json = MagicMock()
        body = {
            "day_title": "d",
            "sequence": [{"index": "001", "use_timeline": "00:00-00:10", "title": "t"}],
        }
        handle_put_plan(handler, {"day": ["day1"]}, body)
        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][0]["ok"] is True
        saved = json.loads((plans_dir / "day1_plan.json").read_text(encoding="utf-8"))
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

    def test_missing_media_dependency_blocks_before_task_submit(self, tmp_path: Path, monkeypatch):
        plans = tmp_path / "plans"
        plans.mkdir()
        (plans / "day1_plan.json").write_text(json.dumps({"sequence": []}), encoding="utf-8")
        cfg = MagicMock()
        cfg.plans_dir = plans
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        handler._get_config.return_value = cfg
        handler._get_task_manager = MagicMock()
        failed = {
            "ok": False,
            "required": ["ffmpeg", "ffprobe"],
            "ffmpeg": None,
            "ffprobe": None,
            "missing": ["ffmpeg"],
            "detail": "未找到 ffmpeg",
        }
        monkeypatch.setattr("clio.ui.routes.plan.preflight_config_media_deps", lambda *a, **k: failed)

        # Plan validation is bypassed so this test isolates the dependency gate.
        monkeypatch.setattr("clio.ui.routes.plan.readiness_block_payload", lambda *a, **k: None)
        monkeypatch.setattr("clio.ui.routes.plan.collect_project_indices", lambda cfg: (set(), set()))
        monkeypatch.setattr("clio.ui.routes.plan.resolve_cut_output_dir", lambda *a: tmp_path / "cuts")
        monkeypatch.setattr("clio.ui.routes.plan.list_existing_cut_videos", lambda *a: [])

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed", "force": True})

        payload, status = handler._send_json.call_args.args
        assert status == 424
        assert payload["code"] == "media_dependency_missing"
        assert handler._get_task_manager.call_count == 0

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

    @pytest.mark.parametrize("output_dir", [123, {"path": "cuts"}, ["cuts"], True])
    def test_non_string_output_dir_returns_400(self, output_dir):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_post_cut(
            handler,
            {},
            {"day_label": "day1", "source": "compressed", "output_dir": output_dir},
        )

        payload, status = handler._send_json.call_args.args
        assert status == 400
        assert payload == {"ok": False, "error": "output_dir must be a string"}
        handler._get_config.assert_not_called()

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
                paths=GlobalPathsConfig(),
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

        ffmpeg = tmp_path / "ffmpeg"
        ffprobe = tmp_path / "ffprobe"
        ffmpeg.write_bytes(b"fake ffmpeg")
        ffprobe.write_bytes(b"fake ffprobe")
        cfg.global_cfg.paths.ffmpeg = str(ffmpeg)
        cfg.global_cfg.paths.ffprobe = str(ffprobe)

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

    def test_managed_cut_returns_task_id(self, tmp_path: Path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind, TaskStatus
        from clio.task_center.store import TaskStore

        monkeypatch.setattr("clio.ui.routes.plan.preflight_config_media_deps", lambda *a, **k: {"ok": True})
        project_dir = tmp_path / "project"
        plans_dir = tmp_path / "plans"
        output_dir = tmp_path / "cuts" / "day1"
        project_dir.mkdir()
        plans_dir.mkdir()
        output_dir.mkdir(parents=True)
        plan = {
            "day_title": "d",
            "sequence": [{"index": "001", "title": "A", "use_timeline": "00:00-00:05", "reason": "r"}],
        }
        (plans_dir / "day1_plan.json").write_text(json.dumps(plan), encoding="utf-8")
        cfg = MagicMock()
        cfg.plans_dir = plans_dir
        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        handler = MagicMock()
        handler.config_path = None
        handler._resolve_project_dir.return_value = project_dir
        handler._get_config.return_value = cfg
        handler._get_task_manager = lambda: manager
        captured = {}

        def worker(context):
            captured.update(context.input_data)
            return {"clip_count": 1}

        monkeypatch.setattr("clio.ui.routes.plan._run_cut_task", worker)
        monkeypatch.setattr("clio.ui.routes.plan.collect_project_indices", lambda cfg: ({"001"}, set()))
        monkeypatch.setattr("clio.ui.routes.plan.readiness_block_payload", lambda result, force: None)
        monkeypatch.setattr("clio.ui.routes.plan.resolve_cut_output_dir", lambda *args: output_dir)
        monkeypatch.setattr("clio.ui.routes.plan.list_existing_cut_videos", lambda path: [])

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        payload = handler._send_json.call_args.args[0]
        task = manager.wait(payload["task_id"])
        assert payload["started"] is True
        assert task.kind is TaskKind.CUT_EXPORT
        assert task.status is TaskStatus.SUCCEEDED
        assert captured["day_label"] == "day1"


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
        handle_post_cut_restore_backups(handler, {}, {"keep_target": False})
        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is True
        assert payload["count"] == 1
        assert (day / "a.mp4").read_bytes() == b"old"
        assert not bak.exists()

    def test_post_coexist_returns_conflict_409(self, tmp_path: Path):
        day = tmp_path / "cuts" / "day1"
        day.mkdir(parents=True)
        bak = day / "a.mp4.clio_bak"
        bak.write_bytes(b"old")
        (day / "a.mp4").write_bytes(b"newcut")
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._send_json = MagicMock()
        handle_post_cut_restore_backups(handler, {}, {})
        status = handler._send_json.call_args[0][1]
        payload = handler._send_json.call_args[0][0]
        assert status == 409
        assert payload["ok"] is False
        assert len(payload["conflicts"]) == 1
        assert (day / "a.mp4").read_bytes() == b"newcut"
        assert bak.exists()

    def test_post_coexist_keep_new(self, tmp_path: Path):
        day = tmp_path / "cuts" / "day1"
        day.mkdir(parents=True)
        bak = day / "a.mp4.clio_bak"
        bak.write_bytes(b"old")
        (day / "a.mp4").write_bytes(b"newcut")
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._send_json = MagicMock()
        handle_post_cut_restore_backups(handler, {}, {"keep_target": True})
        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is True
        assert payload["count"] == 1
        assert (day / "a.mp4").read_bytes() == b"newcut"
        assert not bak.exists()


def _raise_unsafe_path(*args, **kwargs):
    raise ValueError("path escapes root")


class TestPlanRoutesRejectUnsafePaths:
    def test_get_plan_forbidden_when_root_escapes(self, tmp_path: Path, monkeypatch):
        handler = MagicMock()
        _mock_plans_cfg(handler, tmp_path / "output" / "plans", tmp_path)
        handler._send_json = MagicMock()
        monkeypatch.setattr("clio.ui.routes.plan.validate_within_root", _raise_unsafe_path)

        handle_get_plan(handler, {"day": ["day1"]})

        handler._send_json.assert_called_once_with({"error": "forbidden"}, 403)

    def test_put_plan_forbidden_when_root_escapes(self, tmp_path: Path, monkeypatch):
        handler = MagicMock()
        _mock_plans_cfg(handler, tmp_path / "output" / "plans", tmp_path)
        handler._send_json = MagicMock()
        monkeypatch.setattr("clio.ui.routes.plan.validate_within_root", _raise_unsafe_path)

        handle_put_plan(handler, {"day": ["day1"]}, {"title": "t"})

        handler._send_json.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)

    def test_preview_media_forbidden_when_root_escapes(self, tmp_path: Path, monkeypatch):
        handler = MagicMock()
        _mock_plans_cfg(handler, tmp_path / "output" / "plans", tmp_path)
        handler._send_json = MagicMock()
        monkeypatch.setattr("clio.ui.routes.plan.validate_within_root", _raise_unsafe_path)

        handle_get_plan_preview_media(handler, {"day": ["day1"]})

        handler._send_json.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)


def _raise_value_error(message: str):
    def _raiser(*args, **kwargs):
        raise ValueError(message)

    return _raiser


class TestPlanPreviewMediaValidation:
    def test_resolves_sanitized_plan_for_unsanitized_day(self, tmp_path: Path):
        handler = MagicMock()
        output_dir = tmp_path / "output"
        plans_dir = output_dir / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "Ep2_-_agnes-3-flash_plan.json").write_text(json.dumps({"sequence": []}), encoding="utf-8")
        cfg = MagicMock()
        cfg.plans_dir = plans_dir
        cfg.paths.output_dir = output_dir
        handler._resolve_project_dir.return_value = tmp_path
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()

        handle_get_plan_preview_media(handler, {"day": ["Ep2 - agnes-3-flash"]})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True
        assert payload["day"] == "Ep2_-_agnes-3-flash"

    def test_invalid_plan_json_returns_400(self, tmp_path: Path):
        handler = MagicMock()
        plans_dir = tmp_path / "output" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "day1_plan.json").write_text("{broken", encoding="utf-8")
        _mock_plans_cfg(handler, plans_dir, tmp_path)
        handler._send_json = MagicMock()

        handle_get_plan_preview_media(handler, {"day": ["day1"]})

        payload, status = handler._send_json.call_args.args
        assert status == 400
        assert payload["ok"] is False


class TestRunCutTask:
    def test_reports_clip_count(self, tmp_path: Path, monkeypatch):
        from clio.ui.routes.plan import _run_cut_task

        monkeypatch.setattr("clio.ui.routes.plan.load_config", lambda *a, **k: MagicMock())
        monkeypatch.setattr("clio.ui.routes.plan.run_cut_all", lambda *a, **k: ["a.mp4", "b.mp4"])
        context = SimpleNamespace(
            input_data={"project_dir": str(tmp_path), "day_label": "day2"},
            task=SimpleNamespace(project_path=None),
            reporter=MagicMock(),
            cancel_event=threading.Event(),
        )

        assert _run_cut_task(context) == {"clip_count": 2, "artifact": "cuts"}

    def test_raises_when_cancelled(self, tmp_path: Path, monkeypatch):
        from clio.task_center.reporter import TaskCancelled
        from clio.ui.routes.plan import _run_cut_task

        monkeypatch.setattr("clio.ui.routes.plan.load_config", lambda *a, **k: MagicMock())
        monkeypatch.setattr("clio.ui.routes.plan.run_cut_all", lambda *a, **k: [])
        cancel_event = threading.Event()
        cancel_event.set()
        context = SimpleNamespace(
            input_data={"project_dir": str(tmp_path)},
            task=SimpleNamespace(project_path=None),
            reporter=MagicMock(),
            cancel_event=cancel_event,
        )

        with pytest.raises(TaskCancelled):
            _run_cut_task(context)


def _prepare_cut_project(tmp_path: Path, monkeypatch) -> tuple[MagicMock, object, Path]:
    from clio.task_center.manager import TaskManager
    from clio.task_center.store import TaskStore

    monkeypatch.setattr("clio.ui.routes.plan.preflight_config_media_deps", lambda *a, **k: {"ok": True})
    project_dir = tmp_path / "project"
    plans_dir = tmp_path / "plans"
    output_dir = tmp_path / "cuts" / "day1"
    project_dir.mkdir()
    plans_dir.mkdir()
    output_dir.mkdir(parents=True)
    plan = {
        "day_title": "d",
        "sequence": [{"index": "001", "title": "A", "use_timeline": "00:00-00:05", "reason": "r"}],
    }
    (plans_dir / "day1_plan.json").write_text(json.dumps(plan), encoding="utf-8")
    cfg = MagicMock()
    cfg.plans_dir = plans_dir
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
    handler = MagicMock()
    handler.config_path = None
    handler._resolve_project_dir.return_value = project_dir
    handler._get_config.return_value = cfg
    handler._get_task_manager = lambda: manager
    handler._send_json = MagicMock()
    monkeypatch.setattr("clio.ui.routes.plan.collect_project_indices", lambda cfg: ({"001"}, set()))
    monkeypatch.setattr("clio.ui.routes.plan.readiness_block_payload", lambda result, force: None)
    monkeypatch.setattr("clio.ui.routes.plan.resolve_cut_output_dir", lambda *args: output_dir)
    monkeypatch.setattr("clio.ui.routes.plan.list_existing_cut_videos", lambda path: [])
    monkeypatch.setattr("clio.ui.routes.plan._run_cut_task", lambda context: {"clip_count": 0})
    return handler, manager, output_dir


class TestHandlePostCutValidation:
    def test_rejects_non_bool_reencode(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_post_cut(handler, {}, {"day_label": "day1", "reencode": "yes"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "reencode must be a boolean"}, 400)

    def test_rejects_non_bool_force(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_post_cut(handler, {}, {"day_label": "day1", "force": "yes"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "force must be a boolean"}, 400)

    def test_rejects_non_bool_overwrite(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_post_cut(handler, {}, {"day_label": "day1", "overwrite": "yes"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "overwrite must be a boolean"}, 400)

    def test_rejects_unsafe_output_dir(self, monkeypatch):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = Path("/tmp/project")
        handler._get_config.return_value = MagicMock()
        handler._send_json = MagicMock()
        monkeypatch.setattr(
            "clio.ui.routes.plan.resolve_cut_output_dir", _raise_value_error("output_dir escapes project")
        )

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        payload, status = handler._send_json.call_args.args
        assert status == 400
        assert "escapes" in payload["error"]

    def test_missing_plan_file_returns_404(self, tmp_path: Path, monkeypatch):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.plans_dir = tmp_path / "plans"
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()
        monkeypatch.setattr("clio.ui.routes.plan.resolve_cut_output_dir", lambda *a: tmp_path / "cuts")

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        payload, status = handler._send_json.call_args.args
        assert status == 404
        assert "规划文件不存在" in payload["error"]

    def test_invalid_plan_json_returns_400(self, tmp_path: Path, monkeypatch):
        plans = tmp_path / "plans"
        plans.mkdir()
        (plans / "day1_plan.json").write_text("{broken", encoding="utf-8")
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.plans_dir = plans
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()
        monkeypatch.setattr("clio.ui.routes.plan.resolve_cut_output_dir", lambda *a: tmp_path / "cuts")

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        _, status = handler._send_json.call_args.args
        assert status == 400

    def test_blocked_by_readiness_returns_400(self, tmp_path: Path, monkeypatch):
        plans = tmp_path / "plans"
        plans.mkdir()
        (plans / "day1_plan.json").write_text(json.dumps({"sequence": []}), encoding="utf-8")
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.plans_dir = plans
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()
        monkeypatch.setattr("clio.ui.routes.plan.resolve_cut_output_dir", lambda *a: tmp_path / "cuts")
        monkeypatch.setattr("clio.ui.routes.plan.collect_project_indices", lambda cfg: (set(), set()))
        monkeypatch.setattr(
            "clio.ui.routes.plan.readiness_block_payload", lambda result, force: {"ok": False, "error": "blocked"}
        )

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        payload, status = handler._send_json.call_args.args
        assert status == 400
        assert payload["error"] == "blocked"

    def test_conflict_when_active_cut_task(self, tmp_path: Path, monkeypatch):
        handler, _manager, _output = _prepare_cut_project(tmp_path, monkeypatch)
        monkeypatch.setattr("clio.ui.routes.plan._active_cut_task", lambda manager, project_id: object())

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        payload, status = handler._send_json.call_args.args
        assert status == 409
        assert payload["ok"] is False

    def test_already_running_returns_409(self, tmp_path: Path, monkeypatch):
        from clio.task_center.manager import TaskAlreadyRunningError

        handler, manager, _output = _prepare_cut_project(tmp_path, monkeypatch)

        def _raise(*args, **kwargs):
            raise TaskAlreadyRunningError("busy")

        monkeypatch.setattr(manager, "submit", _raise)

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        payload, status = handler._send_json.call_args.args
        assert status == 409
        assert payload["ok"] is False


class _LegacyState:
    def __init__(self):
        self.job_lock = threading.Lock()
        self.job_thread = None
        self.job_cancel_event = threading.Event()


class _LegacyCutHandler:
    def __init__(self, cfg, project_dir: Path, state: _LegacyState):
        self.config_path = None
        self._cfg = cfg
        self._project_dir = project_dir
        self._state = state
        self.responses: list[tuple[dict, int]] = []

    def _resolve_project_dir(self, qs):
        return self._project_dir

    def _get_config(self, proj_dir):
        return self._cfg

    def _get_state(self, key):
        return self._state

    def _send_json(self, data, status=200):
        self.responses.append((data, status))


class TestLegacyCutThread:
    def _prepare(self, tmp_path: Path, monkeypatch, run_cut) -> _LegacyCutHandler:
        monkeypatch.setattr("clio.ui.routes.plan.preflight_config_media_deps", lambda *a, **k: {"ok": True})
        monkeypatch.setattr("clio.ui.routes.plan.collect_project_indices", lambda cfg: ({"001"}, set()))
        monkeypatch.setattr("clio.ui.routes.plan.readiness_block_payload", lambda result, force: None)
        monkeypatch.setattr("clio.ui.routes.plan.resolve_cut_output_dir", lambda *a: tmp_path / "cuts" / "day1")
        monkeypatch.setattr("clio.ui.routes.plan.list_existing_cut_videos", lambda path: [])
        monkeypatch.setattr("clio.ui.routes.plan.run_cut_all", run_cut)
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        (plans_dir / "day1_plan.json").write_text(
            json.dumps({"day_title": "d", "sequence": [{"index": "001", "use_timeline": "00:00-00:05"}]}),
            encoding="utf-8",
        )
        cfg = MagicMock()
        cfg.plans_dir = plans_dir
        state = _LegacyState()
        handler = _LegacyCutHandler(cfg, tmp_path, state)
        handler._state = state
        return handler

    def test_runs_in_background_thread(self, tmp_path: Path, monkeypatch):
        calls: list[dict] = []
        handler = self._prepare(tmp_path, monkeypatch, lambda *a, **k: calls.append(k) or [])

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        payload, _ = handler.responses[-1]
        assert payload["started"] is True
        assert "task_id" not in payload
        deadline = time.time() + 5
        while handler._state.job_thread is not None and time.time() < deadline:
            time.sleep(0.01)
        assert calls

    def test_swallows_worker_errors(self, tmp_path: Path, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("cut failed")

        handler = self._prepare(tmp_path, monkeypatch, _boom)

        handle_post_cut(handler, {}, {"day_label": "day1", "source": "compressed"})

        deadline = time.time() + 5
        while handler._state.job_thread is not None and time.time() < deadline:
            time.sleep(0.01)
        assert handler._state.job_thread is None


class TestHandlePostPlanReadinessValidation:
    def test_forbidden_day(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_post_plan_readiness(handler, {}, {"day": "../x"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "forbidden"}, 403)

    def test_missing_plan_file_returns_404(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.plans_dir = tmp_path / "plans"
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()

        handle_post_plan_readiness(handler, {}, {"day": "day1"})

        payload, status = handler._send_json.call_args.args
        assert status == 404
        assert payload["errors"][0]["code"] == "plan_missing"

    def test_invalid_plan_json_returns_400(self, tmp_path: Path):
        plans = tmp_path / "plans"
        plans.mkdir()
        (plans / "day1_plan.json").write_text("{broken", encoding="utf-8")
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.plans_dir = plans
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()

        handle_post_plan_readiness(handler, {}, {"day": "day1"})

        _, status = handler._send_json.call_args.args
        assert status == 400


class TestManagedTaskManagerLookup:
    def test_returns_none_without_task_manager(self):
        from clio.ui.routes.plan import _managed_task_manager

        assert _managed_task_manager(MagicMock(spec=[])) is None

    def test_returns_none_for_foreign_manager(self):
        from clio.ui.routes.plan import _managed_task_manager

        handler = MagicMock()
        handler._get_task_manager.return_value = "not-a-manager"
        assert _managed_task_manager(handler) is None


class TestRestoreBackupsValidation:
    def test_rejects_non_list_paths(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_post_cut_restore_backups(handler, {}, {"paths": "a.mp4"})

        payload, status = handler._send_json.call_args.args
        assert status == 400
        assert payload == {"ok": False, "error": "paths must be a list"}

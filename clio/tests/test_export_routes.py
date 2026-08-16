"""Tests for clio.ui.routes.export."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.routes.export import handle_post_export

_VALID_PLAN = {
    "day_title": "d",
    "sequence": [
        {
            "index": "001",
            "title": "t",
            "reason": "r",
            "use_timeline": "00:00-00:10",
            "voiceover_hint": "v",
        }
    ],
    "total_estimated_sec": 120,
}


@pytest.fixture
def handler(tmp_path: Path) -> MagicMock:
    h = MagicMock()
    cfg = MagicMock()
    cfg.plans_dir = tmp_path
    cfg.paths.output_dir = tmp_path / "output"
    cfg._project_dir = tmp_path / "input"
    cfg.project_dir = tmp_path / "input"
    cfg.paths.ffprobe = "ffprobe"
    cfg.texts_dir = tmp_path / "texts"
    cfg.export.canvas_ratio = "16:9"
    cfg.export.auto_copy_draft = False
    cfg.export.jianying_draft_dir = ""
    cfg.compressed_dir = tmp_path / "compressed"
    cfg.compressed_dir.mkdir()
    h._resolve_project_dir.return_value = "default"
    h._get_config.return_value = cfg
    mock_state = MagicMock()
    mock_state.job_thread = None
    mock_state.job_lock = MagicMock()
    h._get_state.return_value = mock_state
    return h


class TestHandlePostExport:
    def test_unsafe_day_rejected(self, handler: MagicMock) -> None:
        handle_post_export(handler, {}, {"day": "../evil", "format": "jianying"})
        args = handler._send_json.call_args[0]
        assert args[1] == 400
        assert "day" in args[0]["error"].lower() or "invalid" in args[0]["error"].lower()

    def test_day_with_slash_rejected(self, handler: MagicMock) -> None:
        handle_post_export(handler, {}, {"day": "a/b", "format": "jianying"})
        assert handler._send_json.call_args[0][1] == 400

    def test_missing_plan_returns_404(self, handler: MagicMock) -> None:
        handle_post_export(handler, {}, {"day": "day1", "format": "jianying"})

        handler._send_json.assert_called_once()
        args, kwargs = handler._send_json.call_args
        assert args[1] == 404 or kwargs.get("status") == 404
        assert not args[0].get("ok", True)

    def test_empty_sequence_blocked(self, handler: MagicMock) -> None:
        plan_path = handler._get_config.return_value.plans_dir / "day1_plan.json"
        plan_path.write_text("{}", encoding="utf-8")
        with patch("clio.ui.routes.export.collect_project_indices", return_value=(set(), set())):
            handle_post_export(handler, {}, {"day": "day1", "format": "jianying"})
        args = handler._send_json.call_args[0]
        assert args[1] == 400
        assert args[0]["ok"] is False
        assert args[0]["issues"]["errors"]

    def test_file_not_found_error_returns_400(self, handler: MagicMock) -> None:
        plan_path = handler._get_config.return_value.plans_dir / "day1_plan.json"
        plan_path.write_text(json.dumps(_VALID_PLAN), encoding="utf-8")

        with (
            patch("clio.ui.routes.export.collect_project_indices", return_value=({"001"}, set())),
            patch("clio.ui.routes.export.export_plan") as mock_export,
            patch("clio.ui.routes.export.resolve_binary", return_value="ffprobe"),
        ):
            mock_export.side_effect = FileNotFoundError("file not found")
            handle_post_export(handler, {}, {"day": "day1", "format": "jianying", "force": True})

        handler._send_json.assert_called_once_with({"ok": False, "error": "file not found"}, 400)

    def test_value_error_returns_400(self, handler: MagicMock) -> None:
        plan_path = handler._get_config.return_value.plans_dir / "day1_plan.json"
        plan_path.write_text(json.dumps(_VALID_PLAN), encoding="utf-8")

        with (
            patch("clio.ui.routes.export.collect_project_indices", return_value=({"001"}, set())),
            patch("clio.ui.routes.export.export_plan") as mock_export,
            patch("clio.ui.routes.export.resolve_binary", return_value="ffprobe"),
        ):
            mock_export.side_effect = ValueError("bad format")
            handle_post_export(handler, {}, {"day": "day1", "format": "jianying", "force": True})

        handler._send_json.assert_called_once_with({"ok": False, "error": "bad format"}, 400)

    def test_success_returns_ok_path(self, handler: MagicMock) -> None:
        plan_path = handler._get_config.return_value.plans_dir / "day1_plan.json"
        plan_path.write_text(json.dumps(_VALID_PLAN), encoding="utf-8")

        result_path = Path("/output/export/day1_jianying")

        with (
            patch("clio.ui.routes.export.collect_project_indices", return_value=({"001"}, set())),
            patch("clio.ui.routes.export.export_plan") as mock_export,
            patch("clio.ui.routes.export.resolve_binary", return_value="ffprobe"),
        ):
            mock_export.return_value = result_path
            handle_post_export(handler, {}, {"day": "day1", "format": "jianying", "force": True})

        handler._send_json.assert_called_once_with({"ok": True, "path": str(result_path)})

    def test_warnings_without_force_needs_force(self, handler: MagicMock) -> None:
        plan = {
            "day_title": "d",
            "total_estimated_sec": 120,
            "sequence": [
                {
                    "index": "001",
                    "title": "",
                    "reason": "",
                    "use_timeline": "00:00-00:10",
                    "voiceover_hint": "",
                }
            ],
        }
        plan_path = handler._get_config.return_value.plans_dir / "day1_plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with (
            patch("clio.ui.routes.export.collect_project_indices", return_value=({"001"}, set())),
            patch("clio.ui.routes.export.export_plan") as mock_export,
        ):
            handle_post_export(handler, {}, {"day": "day1", "format": "jianying"})
            mock_export.assert_not_called()
        args = handler._send_json.call_args[0]
        assert args[1] == 400
        assert args[0].get("needs_force") is True

    def test_real_manager_returns_export_task_id(self, handler: MagicMock, tmp_path: Path) -> None:
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind, TaskStatus
        from clio.task_center.store import TaskStore

        plan_path = handler._get_config.return_value.plans_dir / "day1_plan.json"
        plan_path.write_text(json.dumps(_VALID_PLAN), encoding="utf-8")
        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
        manager.register(TaskKind.EXPORT, lambda context: {"artifact": "export/day1_jianying"}, cancellable=True)
        handler._get_task_manager.return_value = manager
        handler._resolve_project_dir.return_value = tmp_path

        with patch("clio.ui.routes.export.collect_project_indices", return_value=({"001"}, set())):
            handle_post_export(handler, {}, {"day": "day1", "format": "jianying", "force": True})

        payload, status = handler._send_json.call_args[0]
        assert status == 202
        assert payload["task_id"]
        assert manager.wait(payload["task_id"]).status is TaskStatus.SUCCEEDED

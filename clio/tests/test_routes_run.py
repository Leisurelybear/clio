"""Tests for clio/ui/routes/run.py — run status/start/rerun handlers."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clio.ui.routes.run import (
    _apply_run_input_dir_override,
    _resolve_run_project_dir,
    handle_get_run_status,
    handle_post_rerun,
    handle_post_run_cancel,
    handle_post_run_preview,
    handle_post_run_start,
)


def _managed_handler(tmp_path, manager):
    from threading import Event, Lock

    class _State:
        def __init__(self):
            self.run_lock = Lock()
            self.run_thread = None
            self.cancel_event = Event()

    project_dir = tmp_path / "project"
    output_dir = tmp_path / "output"
    project_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)
    cfg = SimpleNamespace(
        paths=SimpleNamespace(output_dir=output_dir),
        plan=SimpleNamespace(use_transcripts=True),
    )
    handler = MagicMock()
    handler.config_path = None
    handler._resolve_project_dir.return_value = project_dir
    handler._get_config.return_value = cfg
    handler._get_task_manager = lambda: manager
    handler._get_state.return_value = _State()
    handler._get_project_output.return_value = output_dir
    return handler, project_dir, output_dir


@pytest.fixture
def _handler():
    """Create a mock handler with _get_state returning a per-project ServerState-like object."""
    from threading import Event, Lock

    class _FakeState:
        def __init__(self):
            self.run_lock = Lock()
            self.run_thread = None
            self.cancel_event = Event()

    handler = MagicMock()
    handler._get_state = lambda key: handler.__class__._fake_state
    handler.__class__._fake_state = _FakeState()
    return handler


@pytest.fixture
def _no_thread(monkeypatch):
    """Prevent background threads from actually starting — avoids copy.deepcopy leaks on CI."""
    monkeypatch.setattr(
        "clio.ui.routes.run.threading.Thread",
        lambda *a, **kw: MagicMock(start=lambda: None),
    )


class TestHandleGetRunStatus:
    def test_idle_when_no_progress_file(self, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = Path("/nonexistent")
        handler._get_project_output.return_value = Path("/nonexistent")

        handle_get_run_status(handler, {})

        handler._send_json.assert_called_once_with({"status": "idle", "running": False})

    def test_reads_progress_file(self, tmp_path: Path, _handler):
        handler = _handler
        proj_dir = tmp_path / "input"
        proj_out = tmp_path / "output"
        proj_out.mkdir(parents=True)
        progress = proj_out / ".progress.json"
        progress.write_text(json.dumps({"status": "running", "phase": "compress"}), encoding="utf-8")
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler.__class__._fake_state.run_thread = MagicMock()
        handler.__class__._fake_state.run_thread.is_alive.return_value = True

        handle_get_run_status(handler, {})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args[0][0]
        assert payload["status"] == "running"
        assert payload["phase"] == "compress"
        assert payload["running"] is True


class TestResolveRunProjectDir:
    def test_none_keeps_query_dir(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()

        result, error = _resolve_run_project_dir(proj, None)

        assert result == proj
        assert error is None

    def test_valid_override_returns_resolved_dir(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        override = tmp_path / "override"
        override.mkdir()

        result, error = _resolve_run_project_dir(proj, str(override))

        assert result == override.resolve()
        assert error is None

    def test_blank_override_keeps_query_dir(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()

        result, error = _resolve_run_project_dir(proj, "   ")

        assert result == proj
        assert error is None

    def test_non_string_override_error(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()

        result, error = _resolve_run_project_dir(proj, 123)

        assert result is proj
        assert error == "project_dir must be a string"

    def test_missing_override_error(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()

        result, error = _resolve_run_project_dir(proj, str(tmp_path / "missing"))

        assert result is proj
        assert "project_dir not found" in error

    def test_override_not_allowed_error(self, tmp_path: Path):
        proj = tmp_path / "proj"
        proj.mkdir()
        allowed_root = tmp_path / "allowed"
        allowed_root.mkdir()
        other = tmp_path / "other"
        other.mkdir()

        result, error = _resolve_run_project_dir(proj, str(other), allowed_paths={str(allowed_root.resolve())})

        assert result is proj
        assert "not allowed" in error


class TestApplyRunInputDirOverride:
    def test_none_keeps_config(self, tmp_path: Path):
        cfg = SimpleNamespace(paths=SimpleNamespace(input_dir=tmp_path))

        result, error = _apply_run_input_dir_override(cfg, None)

        assert result is cfg
        assert error is None

    def test_valid_input_dir_returns_config_copy(self, tmp_path: Path):
        cfg = SimpleNamespace(paths=SimpleNamespace(input_dir=tmp_path / "old"))
        new_input = tmp_path / "new"
        new_input.mkdir()

        result, error = _apply_run_input_dir_override(cfg, str(new_input))
        assert getattr(result, "_project_dir", None) == new_input.resolve() or getattr(result, "project_dir", None) in (
            new_input,
            new_input.resolve(),
        )

        assert error is None
        assert result is not cfg
        assert result._project_dir == new_input
        assert not hasattr(cfg, "_project_dir") or cfg._project_dir != new_input

    def test_missing_input_dir_returns_error(self, tmp_path: Path):
        cfg = SimpleNamespace(paths=SimpleNamespace(input_dir=tmp_path))

        result, error = _apply_run_input_dir_override(cfg, str(tmp_path / "missing"))

        assert result is cfg
        assert "project_dir not found" in error or "input_dir not found" in error

    def test_outside_allowlist_returns_error(self, tmp_path: Path):
        cfg = SimpleNamespace(paths=SimpleNamespace(input_dir=tmp_path))
        allowed_root = tmp_path / "proj"
        allowed_root.mkdir()
        other = tmp_path / "other"
        other.mkdir()
        result, error = _apply_run_input_dir_override(cfg, str(other), allowed_paths={str(allowed_root.resolve())})
        assert result is cfg
        assert error is not None
        assert "not allowed" in error

    def test_allowlist_accepts_registered_path(self, tmp_path: Path):
        cfg = SimpleNamespace(paths=SimpleNamespace(input_dir=tmp_path))
        allowed_root = tmp_path / "proj"
        allowed_root.mkdir()
        result, error = _apply_run_input_dir_override(
            cfg, str(allowed_root), allowed_paths={str(allowed_root.resolve())}
        )
        assert error is None
        assert result is not cfg
        assert result._project_dir == allowed_root.resolve()


class TestHandlePostRunStart:
    def test_already_running(self, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = Path("/input")
        handler.__class__._fake_state.run_thread = MagicMock()
        handler.__class__._fake_state.run_thread.is_alive.return_value = True

        handle_post_run_start(handler, {}, {})

        handler._send_json.assert_called_once_with({"ok": False, "error": "pipeline is already running"}, 409)

    def test_already_running_does_not_clobber_progress(self, tmp_path, _handler):
        """Duplicate run request must NOT overwrite existing progress file."""
        handler = _handler
        handler._resolve_project_dir.return_value = Path("/input")
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        progress = out_dir / ".progress.json"
        original = {"status": "running", "phase": "analyze", "message": "still running"}
        progress.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        cfg = MagicMock()
        cfg.paths.output_dir = out_dir
        handler._get_config.return_value = cfg
        handler.__class__._fake_state.run_thread = MagicMock()
        handler.__class__._fake_state.run_thread.is_alive.return_value = True

        handle_post_run_start(handler, {}, {})

        handler._send_json.assert_called_once_with({"ok": False, "error": "pipeline is already running"}, 409)
        assert json.loads(progress.read_text(encoding="utf-8")) == original

    def test_starts_thread(self, tmp_path: Path, _no_thread, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path / "input"
        handler._get_config.return_value = MagicMock()

        handle_post_run_start(handler, {}, {"steps": ["compress", "analyze"]})

        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][0]["ok"] is True

    def test_rejects_missing_input_dir_override(self, tmp_path: Path, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path / "input"
        cfg = SimpleNamespace(paths=SimpleNamespace(input_dir=tmp_path / "input", output_dir=tmp_path / "output"))
        handler._get_config.return_value = cfg

        handle_post_run_start(handler, {}, {"input_dir": str(tmp_path / "missing")})

        handler._send_json.assert_called_once()
        assert handler._send_json.call_args.args[1] == 400
        err = handler._send_json.call_args.args[0]["error"]
        assert "project_dir not found" in err or "input_dir not found" in err

    def test_unsafe_day_label_rejected(self, tmp_path: Path, _handler, _no_thread):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.paths.output_dir = tmp_path / "out"
        cfg.paths.output_dir.mkdir()
        cfg.plan.use_transcripts = True
        handler._get_config.return_value = cfg
        handle_post_run_start(handler, {}, {"day_label": "../x", "steps": ["plan"]})
        assert handler._send_json.call_args[0][1] == 400
        assert "day_label" in handler._send_json.call_args[0][0]["error"]

    def test_use_transcripts_does_not_mutate_cached_config(self, tmp_path: Path, _handler, _no_thread):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path
        out = tmp_path / "out"
        out.mkdir()
        plan = SimpleNamespace(use_transcripts=True)
        cfg = SimpleNamespace(paths=SimpleNamespace(output_dir=out), plan=plan)
        handler._get_config.return_value = cfg

        handle_post_run_start(handler, {}, {"day_label": "day1", "steps": ["plan"], "use_transcripts": False})
        # Shared cfg must remain default True (handler deepcopies before assign)
        assert cfg.plan.use_transcripts is True
        assert handler._send_json.call_args[0][0]["ok"] is True

    def test_override_keeps_config_and_state_unified(self, tmp_path: Path, _handler, _no_thread, monkeypatch):
        """A body project_dir override must not split config from state (P1-P31)."""
        handler = _handler
        query_dir = tmp_path / "query"
        query_dir.mkdir()
        override_dir = tmp_path / "override"
        override_dir.mkdir()
        handler._resolve_project_dir.return_value = query_dir
        cfg = MagicMock()
        cfg.paths.output_dir = tmp_path / "out"
        handler._get_config.return_value = cfg
        monkeypatch.setattr(
            "clio.ui.routes.run.collect_allowed_project_paths",
            lambda *a, **k: {str(query_dir.resolve()), str(override_dir.resolve())},
        )
        captured_keys = []
        handler._get_state = MagicMock(
            side_effect=lambda key: captured_keys.append(key) or handler.__class__._fake_state
        )

        handle_post_run_start(handler, {}, {"steps": ["compress"], "project_dir": str(override_dir)})

        assert handler._send_json.call_args[0][0]["ok"] is True
        assert handler._get_config.call_args[0][0] == override_dir.resolve()
        assert captured_keys == [str(override_dir.resolve())]
        assert handler._get_config.call_args[0][0].resolve() == Path(captured_keys[0])


class TestHandlePostRunPreview:
    def test_builds_preview_from_request(self, tmp_path: Path, _handler, monkeypatch):
        handler = _handler
        proj_dir = tmp_path / "input"
        cfg = MagicMock()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_config.return_value = cfg
        expected = {"input": {}, "steps": [], "totals": {}}
        build = MagicMock(return_value=expected)
        monkeypatch.setattr("clio.ui.routes.run.build_run_preview", build)

        handle_post_run_preview(
            handler,
            {},
            {
                "day_label": "day3",
                "steps": ["compress", "analyze"],
                "use_transcripts": False,
                "overwrite": True,
                "files": ["A.mp4"],
            },
        )

        build.assert_called_once_with(
            cfg,
            ["compress", "analyze"],
            force=True,
            use_transcripts=False,
            files=["A.mp4"],
            day_label="day3",
        )
        handler._send_json.assert_called_once_with({"ok": True, "preview": expected})

    def test_rejects_non_list_files(self, tmp_path: Path, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path / "input"
        handler._get_config.return_value = MagicMock()

        handle_post_run_preview(handler, {}, {"files": "A.mp4"})

        handler._send_json.assert_called_once_with({"ok": False, "error": "files must be a list of video names"}, 400)

    def test_override_applies_to_preview(self, tmp_path: Path, _handler, monkeypatch):
        """Preview must honor the same body project_dir override as run start (P1-P31)."""
        handler = _handler
        query_dir = tmp_path / "query"
        query_dir.mkdir()
        override_dir = tmp_path / "override"
        override_dir.mkdir()
        handler._resolve_project_dir.return_value = query_dir
        cfg = MagicMock()
        handler._get_config.return_value = cfg
        expected = {"input": {}, "steps": [], "totals": {}}
        build = MagicMock(return_value=expected)
        monkeypatch.setattr("clio.ui.routes.run.build_run_preview", build)
        monkeypatch.setattr(
            "clio.ui.routes.run.collect_allowed_project_paths",
            lambda *a, **k: {str(query_dir.resolve()), str(override_dir.resolve())},
        )

        handle_post_run_preview(handler, {}, {"steps": ["compress"], "project_dir": str(override_dir)})

        assert handler._get_config.call_args[0][0] == override_dir.resolve()
        build.assert_called_once_with(
            cfg,
            ["compress"],
            force=False,
            use_transcripts=True,
            files=None,
            day_label="day1",
        )


class TestHandlePostRerun:
    def test_missing_params(self, tmp_path: Path, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path

        handle_post_rerun(handler, {}, {})

        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][1] == 400

    def test_invalid_task(self, tmp_path: Path, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path

        handle_post_rerun(handler, {}, {"video": "test.mp4", "task": "invalid"})

        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][1] == 400

    def test_transcribe_valid_task(self, tmp_path: Path, _no_thread, _handler):
        """transcribe 应作为有效 task 被接受"""
        handler = _handler
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        (proj_dir / "GL010695.MP4").write_bytes(b"")
        handler._resolve_project_dir.return_value = proj_dir

        handle_post_rerun(handler, {}, {"video": "GL010695.MP4", "task": "transcribe", "source": "original"})

        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][0]["ok"] is True

    def test_starts_rerun(self, tmp_path: Path, _no_thread, _handler):
        handler = _handler
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        (proj_dir / "GL010695.MP4").write_bytes(b"")
        handler._resolve_project_dir.return_value = proj_dir

        handle_post_rerun(handler, {}, {"video": "001_GL010695.mp4", "task": "compress"})

        handler._send_json.assert_called_once()
        assert handler._send_json.call_args[0][0]["ok"] is True

    def test_rerun_with_external_original_returns_ok(self, tmp_path: Path, _handler):
        """Rerun with compressed source: verify original_video captured by the rerun
        thread closure is the correct external path (from .vmeta.source_path)."""
        import json as _json

        handler = _handler
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir(parents=True)

        # External original (outside proj_dir)
        ext_root = tmp_path / "external"
        ext_root.mkdir()
        original = ext_root / "GL010695.MP4"
        original.write_bytes(b"original data")

        # Compressed file with .vmeta pointing to external original
        compressed = comp_dir / "001_GL010695.mp4"
        compressed.write_bytes(b"compressed")
        from clio.vmeta import VideoMeta

        meta = VideoMeta.build(
            source=original,
            target=compressed,
            source_duration=10.0,
            target_duration=5.0,
        )
        meta.write(compressed)

        # videos.json with external path
        (proj_dir / "videos.json").write_text(_json.dumps([str(original.resolve())]))

        handler._resolve_project_dir.return_value = proj_dir
        cfg = MagicMock()
        cfg.compressed_dir = comp_dir
        cfg.paths = SimpleNamespace(ffprobe="", output_dir=proj_out, input_dir=proj_dir)
        cfg.analyze = SimpleNamespace(skip_existing=True)
        cfg.compress = SimpleNamespace()
        handler._get_config.return_value = cfg

        # Capture the thread target's default arguments
        # _no_thread can't be used here because we need to inspect the Thread call
        captured_thread_args = {}

        def _fake_thread(*a, **kw):
            captured_thread_args["target"] = kw.get("target")
            return MagicMock(start=lambda: None)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr("clio.ui.routes.run.threading.Thread", _fake_thread)

        try:
            handle_post_rerun(handler, {}, {"video": "001_GL010695.mp4", "task": "compress"})

            handler._send_json.assert_called_once()
            payload = handler._send_json.call_args[0][0]
            assert payload["ok"] is True, f"Expected ok=True, got {payload}"

            target_fn = captured_thread_args.get("target")
            assert target_fn is not None, "Thread was not created"
            # _rerun_worker has defaults: cfg, task, video_basename, original_video, ...
            defaults = target_fn.__defaults__
            assert defaults is not None, "No defaults on _rerun_worker"
        finally:
            monkeypatch.undo()
        # defaults: (cfg, task, video_basename, original_video, texts_json, proj_out, cancel_event)
        assert len(defaults) >= 4, f"Expected at least 4 defaults, got {len(defaults)}"
        original_video_arg = defaults[3]  # 4th default is original_video
        assert original_video_arg == original.resolve(), (
            f"Wrong original_video: expected {original.resolve()}, got {original_video_arg}"
        )


class TestHandlePostRunCancel:
    def test_cancel_sets_event(self, _handler):
        handler = _handler
        handler._resolve_project_dir.return_value = Path("/input")
        assert not handler.__class__._fake_state.cancel_event.is_set()

        handle_post_run_cancel(handler, {}, {})

        assert handler.__class__._fake_state.cancel_event.is_set()
        handler._send_json.assert_called_once_with({"ok": True, "message": "取消请求已发送"})


class TestManagedRunIntegration:
    def test_start_submits_managed_pipeline_and_returns_task_id(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind, TaskStatus
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        captured = {}

        def worker(context):
            captured.update(context.input_data)
            return {"ok": True}

        monkeypatch.setattr("clio.ui.routes.run._run_pipeline_task", worker)
        handler, project_dir, _ = _managed_handler(tmp_path, manager)

        handle_post_run_start(handler, {}, {"steps": ["compress", "analyze"], "files": ["A.mp4"]})

        payload = handler._send_json.call_args.args[0]
        task = manager.wait(payload["task_id"])
        assert payload["ok"] is True
        assert task.kind is TaskKind.PIPELINE
        assert task.status is TaskStatus.SUCCEEDED
        assert task.project_id == str(project_dir.resolve())
        assert captured["steps"] == ["compress", "analyze"]
        assert captured["files"] == ["A.mp4"]

    def test_duplicate_managed_run_returns_409(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        entered = threading.Event()
        release = threading.Event()

        def worker(context):
            entered.set()
            release.wait(timeout=2)

        monkeypatch.setattr("clio.ui.routes.run._run_pipeline_task", worker)
        handler, _, _ = _managed_handler(tmp_path, manager)
        handle_post_run_start(handler, {}, {"steps": ["compress"]})
        assert entered.wait(timeout=2)
        handler._send_json.reset_mock()

        handle_post_run_start(handler, {}, {"steps": ["analyze"]})

        assert handler._send_json.call_args.args[1] == 409
        release.set()

    def test_managed_cancel_targets_current_project_task(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskStatus
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        entered = threading.Event()

        def worker(context):
            entered.set()
            while True:
                context.reporter.raise_if_cancelled()
                time.sleep(0.005)

        monkeypatch.setattr("clio.ui.routes.run._run_pipeline_task", worker)
        handler, _, _ = _managed_handler(tmp_path, manager)
        handle_post_run_start(handler, {}, {"steps": ["compress"]})
        task_id = handler._send_json.call_args.args[0]["task_id"]
        assert entered.wait(timeout=2)
        handler._send_json.reset_mock()

        handle_post_run_cancel(handler, {}, {})

        assert manager.wait(task_id).status is TaskStatus.CANCELLED
        assert handler._send_json.call_args.args[0]["task_id"] == task_id

    def test_pipeline_reporter_keeps_legacy_progress_projection(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind, TaskStatus
        from clio.task_center.store import TaskStore
        from clio.ui.routes.run import _run_pipeline_task

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
        _, project_dir, output_dir = _managed_handler(tmp_path, manager)
        cfg = SimpleNamespace(paths=SimpleNamespace(output_dir=output_dir), plan=SimpleNamespace(use_transcripts=True))
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *args, **kwargs: cfg)

        def pipeline(config, day_label, steps, tracker, **kwargs):
            tracker.update(phase="analyze", current=1, total=2, message="分析中")
            tracker.next(message="分析完成")
            tracker.done("完成")

        monkeypatch.setattr("clio.ui.routes.run.run_pipeline_steps", pipeline)
        manager.register(TaskKind.PIPELINE, _run_pipeline_task, cancellable=True)
        task = manager.submit(
            TaskKind.PIPELINE,
            "处理素材",
            project_id=str(project_dir.resolve()),
            project_path=str(project_dir.resolve()),
            input_data={"project_dir": str(project_dir.resolve()), "steps": ["analyze"]},
        )

        finished = manager.wait(task.id)
        progress = json.loads((output_dir / ".progress.json").read_text(encoding="utf-8"))
        assert finished.status is TaskStatus.SUCCEEDED
        assert finished.current == 2
        assert finished.total == 2
        assert progress["status"] == "done"

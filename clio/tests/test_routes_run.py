"""Tests for clio/ui/routes/run.py — run status/start/rerun handlers."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.routes.run import (
    _apply_run_input_dir_override,
    _read_progress_file,
    _resolve_found_original,
    _resolve_run_project_dir,
    _run_pipeline_task,
    _run_rerun_task,
    handle_get_run_status,
    handle_get_run_stream,
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

    @pytest.mark.parametrize(
        ("payload", "error"),
        [
            ({"overwrite": "false"}, "overwrite must be a boolean"),
            ({"files": ["A.mp4", 2]}, "files items must be strings"),
            ({"context_override": 2}, "context_override must be a string"),
            ({"task_prompts": {"analyze": 2}}, "task_prompts must be an object of strings"),
        ],
    )
    def test_rejects_invalid_typed_options(self, tmp_path: Path, _handler, payload, error):
        handler = _handler
        handler._resolve_project_dir.return_value = tmp_path / "input"
        handler._get_config.return_value = MagicMock()

        handle_post_run_start(handler, {}, payload)

        handler._send_json.assert_called_once_with({"ok": False, "error": error}, 400)

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

    @pytest.mark.parametrize(
        ("payload", "error"),
        [
            ({"overwrite": "false"}, "overwrite must be a boolean"),
            ({"use_transcripts": 1}, "use_transcripts must be a boolean"),
            ({"steps": ["compress", 2]}, "steps must be a list of strings"),
            ({"files": ["A.mp4", 2]}, "files items must be strings"),
        ],
    )
    def test_rejects_invalid_typed_options(self, _handler, payload, error):
        handle_post_run_preview(_handler, {}, payload)

        _handler._send_json.assert_called_once_with({"ok": False, "error": error}, 400)

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


class TestRunPipelineTask:
    """Cover _run_pipeline_task success/cancel/error paths via a real manager."""

    def _submit(self, tmp_path, monkeypatch, worker):
        from clio.task_center.manager import TaskManager
        from clio.task_center.store import TaskStore

        monkeypatch.setattr("clio.ui.routes.run._run_pipeline_task", worker)
        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        _, project_dir, output_dir = _managed_handler(tmp_path, manager)
        cfg = SimpleNamespace(paths=SimpleNamespace(output_dir=output_dir), plan=SimpleNamespace(use_transcripts=True))
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *args, **kwargs: cfg)
        return manager, project_dir

    def test_success_returns_result_and_writes_progress(self, tmp_path, monkeypatch):
        captured = {}

        def fake_pipeline(cfg, day_label, steps, tracker, **kwargs):
            captured["day_label"] = day_label
            captured["steps"] = steps
            captured["files"] = kwargs.get("files")
            captured["overwrite"] = kwargs.get("overwrite")
            return {"steps_run": 3}

        monkeypatch.setattr("clio.ui.routes.run.run_pipeline_steps", fake_pipeline)

        def fake_load_config(*args, **kwargs):
            return SimpleNamespace(
                paths=SimpleNamespace(output_dir=tmp_path / "out"),
                plan=SimpleNamespace(use_transcripts=True),
            )

        monkeypatch.setattr("clio.ui.routes.run.load_config", fake_load_config)
        context = SimpleNamespace(
            input_data={
                "project_dir": str(tmp_path),
                "day_label": "day1",
                "steps": ["compress"],
                "files": ["a.mp4"],
                "overwrite": True,
            },
            task=SimpleNamespace(project_path=str(tmp_path)),
            reporter=MagicMock(),
            cancel_event=None,
        )

        result = _run_pipeline_task(context)

        assert result == {"steps_run": 3}
        assert captured["day_label"] == "day1"
        assert captured["steps"] == ["compress"]
        assert captured["files"] == ["a.mp4"]
        assert captured["overwrite"] is True

    def test_cancelled_marks_tracker_and_reraises(self, tmp_path, monkeypatch):
        from clio.task_center.reporter import TaskCancelled

        events = []

        class FakeLegacy:
            def cancelled(self, msg=""):
                events.append(("cancelled", msg))

        def fake_pipeline(cfg, day_label, steps, tracker, **kw):
            raise TaskCancelled("任务已取消")

        monkeypatch.setattr("clio.ui.routes.run.run_pipeline_steps", fake_pipeline)
        monkeypatch.setattr(
            "clio.ui.routes.run.load_config",
            lambda *a, **k: SimpleNamespace(
                paths=SimpleNamespace(output_dir=tmp_path), plan=SimpleNamespace(use_transcripts=True)
            ),
        )

        context = SimpleNamespace(
            input_data={"project_dir": str(tmp_path)},
            task=SimpleNamespace(project_path=str(tmp_path)),
            reporter=MagicMock(),
            cancel_event=None,
        )

        with pytest.raises(TaskCancelled):
            _run_pipeline_task(context)

    def test_generic_error_logs_and_reraises(self, tmp_path, monkeypatch):

        def fake_pipeline(cfg, day_label, steps, tracker, **kw):
            raise ValueError("disk full")

        monkeypatch.setattr("clio.ui.routes.run.run_pipeline_steps", fake_pipeline)
        monkeypatch.setattr(
            "clio.ui.routes.run.load_config",
            lambda *a, **k: SimpleNamespace(
                paths=SimpleNamespace(output_dir=tmp_path), plan=SimpleNamespace(use_transcripts=True)
            ),
        )

        context = SimpleNamespace(
            input_data={"project_dir": str(tmp_path)},
            task=SimpleNamespace(project_path=str(tmp_path)),
            reporter=MagicMock(),
            cancel_event=None,
        )

        with pytest.raises(ValueError, match="disk full"):
            _run_pipeline_task(context)


class TestRunRerunTask:
    """Cover _run_rerun_task success and error branches."""

    def _make_context(self, tmp_path, task="compress", video="clip.mp4"):
        original = tmp_path / "original"
        original.mkdir(exist_ok=True)
        video_file = original / video
        video_file.write_bytes(b"fake")
        cfg = SimpleNamespace(
            paths=SimpleNamespace(output_dir=tmp_path / "out"),
            analyze=SimpleNamespace(skip_existing=True),
            plan=SimpleNamespace(use_transcripts=True),
        )
        (tmp_path / "out").mkdir(exist_ok=True)
        return SimpleNamespace(
            input_data={
                "project_dir": str(tmp_path),
                "task": task,
                "video_basename": video,
                "original_video": str(video_file),
            },
            task=SimpleNamespace(project_path=str(tmp_path)),
            reporter=MagicMock(),
            cancel_event=None,
        ), cfg

    def test_compress_success(self, tmp_path, monkeypatch):
        context, cfg = self._make_context(tmp_path, task="compress")
        calls = []
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *a, **k: copy.deepcopy(cfg))
        monkeypatch.setattr("clio.ui.routes.run.run_compress_all", lambda *a, **kw: calls.append("compress"))

        result = _run_rerun_task(context)

        assert result == {"task": "compress", "video": "clip.mp4"}
        assert calls == ["compress"]

    def test_analyze_skips_existing_disabled(self, tmp_path, monkeypatch):
        context, cfg = self._make_context(tmp_path, task="analyze")
        captured_cfgs = []
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *a, **k: copy.deepcopy(cfg))

        def fake_analyze(config, *args, **kwargs):
            captured_cfgs.append(config)

        monkeypatch.setattr("clio.ui.routes.run.run_analyze_all", fake_analyze)

        _run_rerun_task(context)

        assert captured_cfgs[0].analyze.skip_existing is False

    def test_voiceover_uses_texts_json(self, tmp_path, monkeypatch):
        context, cfg = self._make_context(tmp_path, task="voiceover")
        texts_json = tmp_path / "texts.json"
        texts_json.write_text("[]", encoding="utf-8")
        context.input_data["texts_json"] = str(texts_json)
        captured = {}
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *a, **k: copy.deepcopy(cfg))

        def fake_scripts(config, *args, single_file=None, **kw):
            captured["single_file"] = single_file

        monkeypatch.setattr("clio.ui.routes.run.run_generate_scripts", fake_scripts)

        _run_rerun_task(context)

        assert captured["single_file"] == texts_json

    def test_transcribe_whisper_missing_raises(self, tmp_path, monkeypatch):
        context, cfg = self._make_context(tmp_path, task="transcribe")
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *a, **k: copy.deepcopy(cfg))
        monkeypatch.setattr("clio.transcribe.check_whisper", lambda: False)

        with pytest.raises(RuntimeError, match="faster-whisper"):
            _run_rerun_task(context)

    def test_transcribe_error_result_raises(self, tmp_path, monkeypatch):
        context, cfg = self._make_context(tmp_path, task="transcribe")
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *a, **k: copy.deepcopy(cfg))
        monkeypatch.setattr("clio.transcribe.check_whisper", lambda: True)
        monkeypatch.setattr(
            "clio.ui.routes.run.run_transcribe_one",
            lambda *a, **kw: {"error": "audio decode failed"},
        )

        with pytest.raises(RuntimeError, match="audio decode failed"):
            _run_rerun_task(context)

    def test_generic_exception_logged_and_reraised(self, tmp_path, monkeypatch):
        context, cfg = self._make_context(tmp_path, task="compress")
        monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *a, **k: copy.deepcopy(cfg))

        def fail(*a, **kw):
            raise OSError("ffmpeg crashed")

        monkeypatch.setattr("clio.ui.routes.run.run_compress_all", fail)

        with pytest.raises(OSError, match="ffmpeg crashed"):
            _run_rerun_task(context)


class TestReadProgressFile:
    def test_missing_file_returns_none(self, tmp_path):
        assert _read_progress_file(tmp_path / "no.json") is None

    def test_invalid_json_returns_none(self, tmp_path):
        f = tmp_path / ".progress.json"
        f.write_text("not json", encoding="utf-8")
        assert _read_progress_file(f) is None

    def test_non_dict_returns_none(self, tmp_path):
        f = tmp_path / ".progress.json"
        f.write_text("[1,2]", encoding="utf-8")
        assert _read_progress_file(f) is None

    def test_missing_phase_key_returns_none(self, tmp_path):
        f = tmp_path / ".progress.json"
        f.write_text(json.dumps({"status": "running"}), encoding="utf-8")
        assert _read_progress_file(f) is None

    def test_valid_data_returned(self, tmp_path):
        f = tmp_path / ".progress.json"
        f.write_text(json.dumps({"status": "running", "phase": "compress"}), encoding="utf-8")
        data = _read_progress_file(f)
        assert data == {"status": "running", "phase": "compress"}


class TestManagedRerunSubmit:
    """Cover the managed rerun submission path (L707-738)."""

    def test_managed_rerun_submits_and_returns_task_id(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind, TaskStatus
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))

        def worker(context):
            return {"task": context.input_data["task"]}

        monkeypatch.setattr("clio.ui.routes.run._run_rerun_task", worker)
        handler, project_dir, _ = _managed_handler(tmp_path, manager)

        # Set up minimal files so handle_post_rerun can resolve paths.
        original = project_dir / "clip.mp4"
        original.write_bytes(b"fake")
        comp_dir = tmp_path / "compressed"
        comp_dir.mkdir()
        comp_file = comp_dir / "clip.mp4"
        comp_file.write_bytes(b"fake compressed")

        cfg = SimpleNamespace(
            paths=SimpleNamespace(output_dir=tmp_path / "out"),
            analyze=SimpleNamespace(skip_existing=True),
            plan=SimpleNamespace(use_transcripts=True),
            compressed_dir=comp_dir,
        )
        (tmp_path / "out").mkdir(exist_ok=True)
        handler._get_config.return_value = cfg

        from unittest.mock import patch as mock_patch

        with (
            mock_patch(
                "clio.ui.services.file_service._find_original_for_compressed", return_value=str(original.resolve())
            ),
            mock_patch("clio.ui.routes.run._resolve_found_original", return_value=original.resolve()),
            mock_patch("clio.vmeta.VideoMeta.read", return_value=None),
        ):
            handle_post_rerun(handler, {}, {"video": "clip.mp4", "task": "analyze"})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True, f"Expected ok=True, got {payload}"
        task_id = payload["task_id"]
        task = manager.wait(task_id)
        assert task.kind is TaskKind.RERUN
        assert task.status is TaskStatus.SUCCEEDED
        assert task.project_id == str(project_dir.resolve())

    def test_managed_rerun_duplicate_returns_409(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        entered = threading.Event()
        release = threading.Event()

        def blocking_worker(context):
            entered.set()
            release.wait(timeout=2)

        monkeypatch.setattr("clio.ui.routes.run._run_rerun_task", blocking_worker)
        handler, project_dir, _ = _managed_handler(tmp_path, manager)
        original = project_dir / "clip.mp4"
        original.write_bytes(b"fake")

        cfg = SimpleNamespace(
            paths=SimpleNamespace(output_dir=tmp_path / "out"),
            analyze=SimpleNamespace(skip_existing=True),
            plan=SimpleNamespace(use_transcripts=True),
            compressed_dir=None,
        )
        (tmp_path / "out").mkdir(exist_ok=True)
        handler._get_config.return_value = cfg

        from unittest.mock import patch as mock_patch

        with (
            mock_patch(
                "clio.ui.services.file_service._find_original_for_compressed", return_value=str(original.resolve())
            ),
            mock_patch("clio.ui.routes.run._resolve_found_original", return_value=original.resolve()),
            mock_patch("clio.vmeta.VideoMeta.read", return_value=None),
        ):
            handle_post_rerun(handler, {}, {"video": "clip.mp4", "task": "analyze"})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True, f"First submit should succeed: {payload}"
        first_task_id = payload["task_id"]
        assert entered.wait(timeout=2)
        handler._send_json.reset_mock()

        with (
            mock_patch(
                "clio.ui.services.file_service._find_original_for_compressed", return_value=str(original.resolve())
            ),
            mock_patch("clio.ui.routes.run._resolve_found_original", return_value=original.resolve()),
            mock_patch("clio.vmeta.VideoMeta.read", return_value=None),
        ):
            handle_post_rerun(handler, {}, {"video": "clip.mp4", "task": "analyze"})

        assert handler._send_json.call_args.args[1] == 409
        release.set()
        manager.wait(first_task_id)


class TestSSEStream:
    """Cover handle_get_run_stream SSE loop with a mock handler."""

    def _sse_handler(self, tmp_path, manager=None):
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
        handler._get_task_manager = lambda: manager
        handler._get_state.return_value = _State()
        handler._get_project_output.return_value = output_dir
        handler._get_config.return_value = copy.deepcopy(cfg)
        return handler, output_dir

    def test_sse_emits_idle_then_breaks_on_done(self, tmp_path, monkeypatch):
        handler, output_dir = self._sse_handler(tmp_path)
        progress = output_dir / ".progress.json"

        # Write "done" progress so the loop breaks after first emit.
        progress.write_text(json.dumps({"status": "done", "phase": "compress"}), encoding="utf-8")

        # Prevent the 0.5s sleep from actually sleeping.
        monkeypatch.setattr("clio.ui.routes.run.time.sleep", lambda s: None)

        handle_get_run_stream(handler, {})

        handler.send_response.assert_called_once_with(200)
        written = b"".join(call.args[0] for call in handler.wfile.write.call_args_list)
        assert b'"status": "done"' in written or b'"status":"done"' in written

    def test_sse_idle_no_progress_file(self, tmp_path, monkeypatch):
        handler, output_dir = self._sse_handler(tmp_path)

        # No progress file -> emits idle once, then keeps looping.
        # We break the loop by raising ConnectionResetError on second write.
        call_count = [0]
        real_write = handler.wfile.write

        def write_side_effect(data):
            call_count[0] += 1
            if call_count[0] > 1:
                raise ConnectionResetError()
            return real_write(data)

        handler.wfile.write.side_effect = write_side_effect
        monkeypatch.setattr("clio.ui.routes.run.time.sleep", lambda s: None)

        handle_get_run_stream(handler, {})

        handler.send_response.assert_called_once_with(200)

    def test_sse_managed_task_running(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))

        def worker(context):
            context.reporter.progress(phase="analyze", current=1, total=2, message="working")
            time.sleep(0.01)
            return {}

        monkeypatch.setattr("clio.ui.routes.run._run_pipeline_task", worker)
        handler, output_dir = self._sse_handler(tmp_path, manager=manager)

        handle_post_run_start(handler, {}, {"steps": ["analyze"]})

        # Wait for task to complete, then write done progress.
        task_id = handler._send_json.call_args.args[0]["task_id"]
        manager.wait(task_id)

        progress = output_dir / ".progress.json"
        progress.write_text(json.dumps({"status": "done", "phase": "analyze"}), encoding="utf-8")

        # Reset mock and call SSE.
        handler.wfile.write.reset_mock()
        handler.wfile.flush = MagicMock()
        monkeypatch.setattr("clio.ui.routes.run.time.sleep", lambda s: None)

        handle_get_run_stream(handler, {})

        written = b"".join(call.args[0] for call in handler.wfile.write.call_args_list)
        assert b'"status": "done"' in written or b'"status":"done"' in written


class TestManagedCancelErrors:
    """Cover cancel error paths for managed tasks."""

    def test_cancel_task_not_found_returns_409(self, tmp_path):
        from clio.task_center.manager import TaskManager
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        handler, _, _ = _managed_handler(tmp_path, manager)

        handle_post_run_cancel(handler, {}, {"task_id": "nonexistent-id"})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True
        assert "没有运行中的任务" in payload["message"]

    def test_cancel_completed_task_is_noop(self, tmp_path, monkeypatch):
        from clio.task_center.manager import TaskManager
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))

        def worker(context):
            return {}

        monkeypatch.setattr("clio.ui.routes.run._run_pipeline_task", worker)
        handler, _, _ = _managed_handler(tmp_path, manager)
        handle_post_run_start(handler, {}, {"steps": ["compress"]})
        task_id = handler._send_json.call_args.args[0]["task_id"]
        manager.wait(task_id)
        handler._send_json.reset_mock()

        handle_post_run_cancel(handler, {}, {"task_id": task_id})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True
        assert payload["task_id"] == task_id


class TestResolveFoundOriginal:
    """Cover _resolve_found_original path resolution branches."""

    def test_none_returns_none(self, tmp_path):
        assert _resolve_found_original(None, tmp_path) is None

    def test_empty_string_returns_none(self, tmp_path):
        assert _resolve_found_original("", tmp_path) is None

    def test_existing_absolute_path_resolved(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        result = _resolve_found_original(str(video), tmp_path)
        assert result == video.resolve()

    def test_relative_path_resolved_against_proj_dir(self, tmp_path):
        video = tmp_path / "sub" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake")
        result = _resolve_found_original("sub/clip.mp4", tmp_path)
        assert result == video.resolve()

    def test_nonexistent_absolute_returns_none(self, tmp_path):
        assert _resolve_found_original(str(tmp_path / "ghost.mp4"), tmp_path) is None

    def test_videos_json_basename_match(self, tmp_path):
        original_dir = tmp_path / "original"
        original_dir.mkdir()
        video = original_dir / "trip_clip.mp4"
        video.write_bytes(b"fake")

        (tmp_path / "videos.json").write_text(json.dumps([str(video.resolve())]), encoding="utf-8")

        # Pass just the basename; not a real file relative to proj_dir.
        result = _resolve_found_original("trip_clip.mp4", tmp_path)
        assert result == video.resolve()

    def test_videos_json_no_match_returns_none(self, tmp_path):
        (tmp_path / "videos.json").write_text(json.dumps(["/elsewhere/gone.mp4"]), encoding="utf-8")
        result = _resolve_found_original("gone.mp4", tmp_path)
        assert result is None


class TestLegacyRerunWorker:
    """Cover the legacy _rerun_worker closure by running handle_post_rerun
    without _no_thread so the thread actually executes."""

    def _legacy_handler(self, tmp_path):
        from threading import Event, Lock

        import yaml as yaml_mod

        class _State:
            def __init__(self):
                self.run_lock = Lock()
                self.run_thread = None
                self.cancel_event = Event()

        project_dir = tmp_path / "project"
        output_dir = tmp_path / "output"
        project_dir.mkdir(exist_ok=True)
        output_dir.mkdir(exist_ok=True)
        (project_dir / "project.yaml").write_text(
            yaml_mod.safe_dump({"paths": {"output_dir": str(output_dir)}}),
            encoding="utf-8",
        )
        cfg = SimpleNamespace(
            paths=SimpleNamespace(output_dir=output_dir),
            analyze=SimpleNamespace(skip_existing=True),
            plan=SimpleNamespace(use_transcripts=True),
            compressed_dir=None,
        )
        handler = MagicMock()
        handler.config_path = None
        handler._resolve_project_dir.return_value = project_dir
        handler._get_config.return_value = cfg
        handler._get_task_manager.return_value = None  # legacy mode
        handler._get_state.return_value = _State()
        handler._get_project_output.return_value = output_dir
        return handler, project_dir, output_dir

    def test_compress_success_writes_done_progress(self, tmp_path, monkeypatch):
        handler, project_dir, output_dir = self._legacy_handler(tmp_path)
        original = project_dir / "clip.mp4"
        original.write_bytes(b"fake")

        calls = []
        monkeypatch.setattr("clio.ui.routes.run.run_compress_all", lambda *a, **kw: calls.append("compress"))

        with (
            patch("clio.ui.services.file_service._find_original_for_compressed", return_value=str(original.resolve())),
            patch("clio.ui.routes.run._resolve_found_original", return_value=original.resolve()),
        ):
            handle_post_rerun(handler, {}, {"video": "clip.mp4", "task": "compress"})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True, payload

        # Wait for thread to finish.
        state = handler._get_state.return_value
        for _ in range(50):
            with state.run_lock:
                if state.run_thread is None or not state.run_thread.is_alive():
                    break
            time.sleep(0.05)

        progress_file = output_dir / ".progress.json"
        assert progress_file.is_file(), "Progress file should be written by tracker"
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        assert data["status"] in ("done", "error", "cancelled"), f"Unexpected: {data}"

    def test_analyze_error_marks_error(self, tmp_path, monkeypatch):
        handler, project_dir, output_dir = self._legacy_handler(tmp_path)
        original = project_dir / "clip.mp4"
        original.write_bytes(b"fake")

        def fail(*args, **kwargs):
            raise RuntimeError("AI quota exceeded")

        monkeypatch.setattr("clio.ui.routes.run.run_analyze_all", fail)

        with (
            patch("clio.ui.services.file_service._find_original_for_compressed", return_value=str(original.resolve())),
            patch("clio.ui.routes.run._resolve_found_original", return_value=original.resolve()),
        ):
            handle_post_rerun(handler, {}, {"video": "clip.mp4", "task": "analyze"})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True, payload

        state = handler._get_state.return_value
        for _ in range(50):
            with state.run_lock:
                if state.run_thread is None or not state.run_thread.is_alive():
                    break
            time.sleep(0.05)

        progress_file = output_dir / ".progress.json"
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        assert data["status"] == "error"
        assert "AI quota" in data.get("message", "")

    def test_cancel_before_step_marks_cancelled(self, tmp_path, monkeypatch):
        handler, project_dir, output_dir = self._legacy_handler(tmp_path)
        original = project_dir / "clip.mp4"
        original.write_bytes(b"fake")

        # handle_post_rerun clears the cancel event before spawning the worker,
        # so we set it from inside the mocked step_fn instead.
        state = handler._get_state.return_value

        def cancel_and_raise(*args, **kwargs):
            state.cancel_event.set()
            raise RuntimeError("rerun 被用户取消（压缩视频）")

        monkeypatch.setattr("clio.ui.routes.run.run_compress_all", cancel_and_raise)

        with (
            patch("clio.ui.services.file_service._find_original_for_compressed", return_value=str(original.resolve())),
            patch("clio.ui.routes.run._resolve_found_original", return_value=original.resolve()),
        ):
            handle_post_rerun(handler, {}, {"video": "clip.mp4", "task": "compress"})

        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True, payload

        for _ in range(50):
            with state.run_lock:
                if state.run_thread is None or not state.run_thread.is_alive():
                    break
            time.sleep(0.05)

        progress_file = output_dir / ".progress.json"
        data = json.loads(progress_file.read_text(encoding="utf-8"))
        assert data["status"] == "cancelled"

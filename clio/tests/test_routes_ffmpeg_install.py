"""UI task and route tests for managed FFmpeg installation."""

from __future__ import annotations

import platform as platform_module
import threading
from pathlib import Path
from unittest.mock import MagicMock

from clio.ffmpeg_install import InstallResult
from clio.task_center.manager import TaskManager
from clio.task_center.models import TaskKind
from clio.task_center.reporter import TaskCancelled
from clio.task_center.store import TaskStore
from clio.ui.routes.deps import (
    _run_ffmpeg_install_task,
    handle_get_deps_ffmpeg_install_status,
    handle_post_deps_ffmpeg_install,
)


class _Reporter:
    def __init__(self):
        self.updates = []

    def progress(self, **kwargs):
        self.updates.append(kwargs)


class _Context:
    def __init__(self):
        self.reporter = _Reporter()
        self.cancel_event = threading.Event()


def test_ffmpeg_task_reports_progress_and_result(monkeypatch):
    def fake_install(**kwargs):
        kwargs["reporter"](100, "done")
        return InstallResult(True, "download", "/managed/ffmpeg", "/managed/ffprobe", "ffmpeg installed")

    monkeypatch.setattr("clio.ui.routes.deps.install_ffmpeg", fake_install)
    context = _Context()

    result = _run_ffmpeg_install_task(context)

    assert result == {"ffmpeg": "/managed/ffmpeg", "ffprobe": "/managed/ffprobe", "method": "download"}
    assert context.reporter.updates == [{"phase": "ffmpeg_install", "current": 100, "total": 100, "message": "done"}]


def test_ffmpeg_task_raises_install_failure(monkeypatch):
    monkeypatch.setattr(
        "clio.ui.routes.deps.install_ffmpeg",
        lambda **kwargs: InstallResult(False, "download", message="checksum mismatch"),
    )
    context = _Context()
    try:
        _run_ffmpeg_install_task(context)
    except RuntimeError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_ffmpeg_task_raises_when_installer_reports_cancellation(monkeypatch):
    monkeypatch.setattr(
        "clio.ui.routes.deps.install_ffmpeg",
        lambda **kwargs: InstallResult(False, "cancelled", message="cancelled"),
    )
    context = _Context()
    try:
        _run_ffmpeg_install_task(context)
    except TaskCancelled:
        pass
    else:
        raise AssertionError("expected TaskCancelled")


def test_ffmpeg_task_keeps_success_if_cancel_arrives_after_promotion(monkeypatch):
    context = _Context()

    def fake_install(**kwargs):
        context.cancel_event.set()
        return InstallResult(True, "download", "/managed/ffmpeg", "/managed/ffprobe", "installed")

    monkeypatch.setattr("clio.ui.routes.deps.install_ffmpeg", fake_install)

    result = _run_ffmpeg_install_task(context)

    assert result["method"] == "download"


def _handler(manager: TaskManager) -> MagicMock:
    handler = MagicMock()
    handler._get_task_manager.return_value = manager
    return handler


def _stub_supported_capabilities(monkeypatch) -> None:
    monkeypatch.setattr(
        "clio.ui.routes.deps._ffmpeg_install_capabilities",
        lambda: {"platform": "linux", "automatic": True},
    )


def test_start_status_and_duplicate_rejection(tmp_path: Path, monkeypatch):
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
    entered = threading.Event()
    release = threading.Event()

    def fake_install(**kwargs):
        entered.set()
        release.wait(timeout=3)
        return InstallResult(True, "download", "ffmpeg", "ffprobe", "ok")

    monkeypatch.setattr("clio.ui.routes.deps.install_ffmpeg", fake_install)
    _stub_supported_capabilities(monkeypatch)
    handler = _handler(manager)

    handle_post_deps_ffmpeg_install(handler, {})
    payload = handler._send_json.call_args.args[0]
    task_id = payload["task_id"]
    assert entered.wait(timeout=2)

    handler._send_json.reset_mock()
    handle_get_deps_ffmpeg_install_status(handler, {})
    status_payload = handler._send_json.call_args.args[0]
    assert status_payload["running"] is True
    assert status_payload["task_id"] == task_id

    handler._send_json.reset_mock()
    handle_post_deps_ffmpeg_install(handler, {})
    assert handler._send_json.call_args.args[1] == 409

    release.set()
    assert manager.wait(task_id).kind is TaskKind.FFMPEG_INSTALL
    manager.shutdown()


def test_terminal_install_does_not_block_retry(tmp_path: Path, monkeypatch):
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
    monkeypatch.setattr(
        "clio.ui.routes.deps.install_ffmpeg",
        lambda **kwargs: InstallResult(True, "download", "ffmpeg", "ffprobe", "ok"),
    )
    _stub_supported_capabilities(monkeypatch)
    handler = _handler(manager)

    handle_post_deps_ffmpeg_install(handler, {})
    first_id = handler._send_json.call_args.args[0]["task_id"]
    assert manager.wait(first_id).status.value == "succeeded"

    handler._send_json.reset_mock()
    handle_post_deps_ffmpeg_install(handler, {})

    assert handler._send_json.call_args.args[0]["ok"] is True
    second_id = handler._send_json.call_args.args[0]["task_id"]
    assert second_id != first_id
    assert manager.wait(second_id).status.value == "succeeded"
    manager.shutdown()


def test_start_returns_409_when_submit_races_active_task(tmp_path: Path, monkeypatch):
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
    entered = threading.Event()
    release = threading.Event()

    def fake_install(**kwargs):
        entered.set()
        release.wait(timeout=3)
        return InstallResult(True, "download", "ffmpeg", "ffprobe", "ok")

    monkeypatch.setattr("clio.ui.routes.deps.install_ffmpeg", fake_install)
    monkeypatch.setattr("clio.ui.routes.deps._active_ffmpeg_task", lambda manager: None)
    _stub_supported_capabilities(monkeypatch)
    handler = _handler(manager)

    handle_post_deps_ffmpeg_install(handler, {})
    assert handler._send_json.call_args.args[0]["ok"] is True
    assert entered.wait(timeout=2)

    handler._send_json.reset_mock()
    handle_post_deps_ffmpeg_install(handler, {})

    payload, status = handler._send_json.call_args.args
    assert status == 409
    assert payload["ok"] is False

    release.set()
    manager.shutdown()


def test_start_rejects_platform_without_verified_asset(tmp_path: Path, monkeypatch):
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
    handler = _handler(manager)
    monkeypatch.setattr(
        "clio.ui.routes.deps._ffmpeg_install_capabilities",
        lambda: {"platform": "darwin", "automatic": False},
    )

    handle_post_deps_ffmpeg_install(handler, {})

    payload = handler._send_json.call_args.args[0]
    assert handler._send_json.call_args.args[1] == 400
    assert payload["ok"] is False
    assert payload["automatic"] is False
    assert manager.store.active_tasks() == []
    manager.shutdown()


def test_capabilities_mark_unknown_os_as_unsupported(monkeypatch):
    from clio.ui.routes.deps import _ffmpeg_install_capabilities

    monkeypatch.setattr(platform_module, "system", lambda: "FreeBSD")
    monkeypatch.setattr(platform_module, "machine", lambda: "amd64")

    capabilities = _ffmpeg_install_capabilities()

    assert capabilities == {"platform": "unsupported", "automatic": False}


def test_status_and_start_require_task_manager():
    handler = MagicMock()
    handler._get_task_manager.side_effect = AttributeError

    handle_get_deps_ffmpeg_install_status(handler, {})
    payload = handler._send_json.call_args.args[0]
    assert payload["status"] == "idle"
    assert payload["running"] is False
    assert payload["platform"] in {"win32", "linux", "darwin", "unsupported"}
    assert isinstance(payload["automatic"], bool)

    handler._send_json.reset_mock()
    handle_post_deps_ffmpeg_install(handler, {})
    assert handler._send_json.call_args.args[1] == 503

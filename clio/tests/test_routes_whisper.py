"""Tests for clio/ui/routes/whisper_routes.py — project query and model persistence."""

from __future__ import annotations

import json
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from clio.ui.routes.whisper_download import (
    _install_progress_path,
    _pip_install_streaming,
    _run_install,
    _write_install_progress,
    handle_get_whisper_install_status,
    handle_post_whisper_install,
    handle_post_whisper_install_cancel,
)
from clio.ui.routes.whisper_models import _get_cache_dir
from clio.ui.routes.whisper_routes import (
    handle_get_whisper_check,
    handle_get_whisper_models,
    handle_put_whisper_model,
)


class TestFrozenInstallGuard:
    """F-1: packaged (PyInstaller) builds cannot install whisper via pip."""

    def test_run_install_fails_fast_when_frozen(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}
        progress_file = _install_progress_path(handler, qs)
        cancel = threading.Event()

        with patch("clio.ui.routes.whisper_download._is_frozen", return_value=True):
            _run_install(handler, qs, progress_file, cancel)

        status = json.loads(progress_file.read_text(encoding="utf-8"))
        assert status["status"] == "error"
        assert "打包版" in status["message"]
        assert "源码版" in status["message"]

    def test_pip_install_streaming_fails_fast_when_frozen(self, tmp_path: Path) -> None:
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        progress_file = proj_output / ".whisper_install.json"
        cancel = threading.Event()

        with patch("clio.ui.routes.whisper_download._is_frozen", return_value=True):
            ok, err = _pip_install_streaming(["faster-whisper"], progress_file, "test", cancel)

        assert ok is False
        assert "源码版" in err

    def test_get_whisper_check_reports_frozen(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "project_a"
        proj_dir.mkdir()
        proj_output = tmp_path / "output_a"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)

        with (
            patch("clio.ui.routes.whisper_check.check_whisper", return_value=False),
            patch("clio.ui.routes.whisper_check.sys.frozen", True, create=True),
        ):
            handle_get_whisper_check(handler, {})

        payload = handler._send_json.call_args[0][0]
        assert payload["frozen"] is True


class _FakePopen:
    """Minimal stand-in for subprocess.Popen used by _pip_install_streaming."""

    def __init__(self, cmd, **kwargs):
        import io

        self.cmd = cmd
        self.stdout = io.StringIO("Downloading package...\nInstalling...\n")
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


def _make_handler(proj_dir: Path, proj_output: Path) -> MagicMock:
    """Build a mock handler that resolves project input/output correctly."""
    handler = MagicMock()

    def _resolve_qs(qs: dict) -> Path:
        # If qs has "project", return a subdirectory; otherwise return proj_dir
        return proj_dir

    handler._resolve_project_dir.side_effect = _resolve_qs
    handler._get_project_output.return_value = proj_output

    cfg = MagicMock()
    cfg.whisper.model_size = "small"
    cfg.whisper.hf_endpoint = ""
    cfg.whisper.cache_dir = ""
    cfg.proxy.enabled = False
    cfg.proxy.url = ""
    handler._get_config.return_value = cfg

    handler._send_json = MagicMock()
    handler.__class__._config_cache = MagicMock()
    return handler


class TestProjectQueryConsistency:
    """Verify that Whisper routes respect the qs project parameter."""

    def test_get_whisper_check_uses_qs(self, tmp_path: Path) -> None:
        """handle_get_whisper_check should pass qs to _resolve_project_input."""
        proj_dir = tmp_path / "project_a"
        proj_dir.mkdir()
        proj_output = tmp_path / "output_a"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "project_a"}

        with patch("clio.ui.routes.whisper_check.check_whisper", return_value=False):
            handle_get_whisper_check(handler, qs)

        handler._resolve_project_dir.assert_called_with(qs)
        handler._get_config.assert_called_once_with(proj_dir)

    def test_get_whisper_models_uses_qs(self, tmp_path: Path) -> None:
        """handle_get_whisper_models should pass qs to _resolve_project_input."""
        proj_dir = tmp_path / "project_b"
        proj_dir.mkdir()
        proj_output = tmp_path / "output_b"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "project_b"}

        handle_get_whisper_models(handler, qs)

        # _resolve_project_input should be called with qs (at least once, possibly twice)
        call_args_list = handler._resolve_project_dir.call_args_list
        assert all(call.args[0] is qs for call in call_args_list), (
            f"Expected all calls with qs={qs}, got: {call_args_list}"
        )

    def test_install_progress_path_uses_qs(self, tmp_path: Path) -> None:
        """_install_progress_path should resolve output from qs, not from empty dict."""
        proj_dir = tmp_path / "project_c"
        proj_dir.mkdir()
        proj_output = tmp_path / "output_c"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "project_c"}

        path = _install_progress_path(handler, qs)

        handler._get_project_output.assert_called_with(qs)
        assert path == proj_output / ".whisper_install.json"

    def test_get_cache_dir_uses_qs(self, tmp_path: Path) -> None:
        """_get_cache_dir should pass qs to _resolve_project_input."""
        proj_dir = tmp_path / "project_d"
        proj_dir.mkdir()
        proj_output = tmp_path / "output_d"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "project_d"}

        _get_cache_dir(handler, qs)

        handler._resolve_project_dir.assert_called_with(qs)


class TestPutWhisperModelPersistence:
    """Verify handle_put_whisper_model correctly writes project.yaml."""

    def test_creates_project_yaml_when_missing(self, tmp_path: Path) -> None:
        """If project.yaml doesn't exist, it should be created with whisper config."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        assert not (proj_dir / "project.yaml").exists()

        handle_put_whisper_model(handler, qs, {"model_size": "medium"})

        # project.yaml should now exist with the model_size
        proj_yaml = proj_dir / "project.yaml"
        assert proj_yaml.is_file()
        raw = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
        assert raw["whisper"]["model_size"] == "medium"
        handler.__class__._config_cache.invalidate_key.assert_called_with(str(proj_dir.resolve()))

    def test_updates_existing_project_yaml(self, tmp_path: Path) -> None:
        """If project.yaml exists, it should be updated with the new model_size."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()

        # Create existing project.yaml with other config
        proj_yaml = proj_dir / "project.yaml"
        proj_yaml.write_text(
            yaml.dump({"whisper": {"model_size": "small", "language": "en"}, "other": "data"}),
            encoding="utf-8",
        )

        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        handle_put_whisper_model(handler, qs, {"model_size": "medium"})

        raw = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
        assert raw["whisper"]["model_size"] == "medium"
        assert raw["whisper"]["language"] == "en"  # preserved
        assert raw["other"] == "data"  # preserved

    def test_rejects_invalid_model_size(self, tmp_path: Path) -> None:
        """Invalid model_size should return 400 error."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        handle_put_whisper_model(handler, qs, {"model_size": "gigantic"})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 400  # status code
        assert "invalid" in args[0][0]["error"]

    def test_rejects_empty_model_size(self, tmp_path: Path) -> None:
        """Empty model_size should return 400 error."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        handle_put_whisper_model(handler, qs, {"model_size": ""})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 400

    def test_does_not_create_file_on_validation_failure(self, tmp_path: Path) -> None:
        """project.yaml should not be created if validation fails."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        handle_put_whisper_model(handler, qs, {"model_size": "invalid"})

        assert not (proj_dir / "project.yaml").exists()


class TestHandlePostWhisperInstallCancel:
    """Verify handle_post_whisper_install_cancel."""

    def test_cancel_updates_progress_file(self, tmp_path: Path) -> None:
        """Cancel should reset progress file status to idle."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        progress_file = _install_progress_path(handler, qs)
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text('{"status": "downloading", "progress_pct": 42}', encoding="utf-8")

        handle_post_whisper_install_cancel(handler, qs)

        data = json.loads(progress_file.read_text(encoding="utf-8"))
        assert data["status"] == "idle"
        assert data["progress_pct"] == 0
        assert "取消" in data["message"]
        handler._send_json.assert_called_once_with({"ok": True, "message": "cancel requested"})

    def test_cancel_without_progress_file(self, tmp_path: Path) -> None:
        """Cancel should succeed even if no progress file exists."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        handle_post_whisper_install_cancel(handler, qs)

        handler._send_json.assert_called_once_with({"ok": True, "message": "cancel requested"})


class TestRunWhisperInstall:
    def test_downloads_required_snapshot_files(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        cache_dir = tmp_path / "models"
        handler._get_config.return_value.whisper.cache_dir = str(cache_dir)
        handler._get_config.return_value.whisper.model_size = "small"
        qs = {"project": "test"}
        progress_file = _install_progress_path(handler, qs)
        cancel = threading.Event()

        fake_hub = types.SimpleNamespace(hf_hub_url=lambda repo_id, filename: f"https://example.test/{filename}")

        class Response:
            headers = {"Content-Length": "4"}

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int):
                yield b"data"

        with (
            patch.dict("sys.modules", {"huggingface_hub": fake_hub}),
            patch("clio.ui.routes.whisper_download._get_model_download_size", return_value=16),
            patch("clio.ui.routes.whisper_download._req.get", return_value=Response()) as mock_get,
            patch("clio.ui.routes.whisper_download.subprocess.Popen", _FakePopen),
            patch("clio.ui.routes.whisper_download.check_cublas", return_value=True),
            patch("clio.ui.routes.whisper_download._get_model"),
            patch("clio.ui.routes.whisper_download._clear_model_cache"),
        ):
            _run_install(handler, qs, progress_file, cancel)

        snap = cache_dir / "models--Systran--faster-whisper-small" / "snapshots" / "downloaded"
        assert (snap / "config.json").read_bytes() == b"data"
        assert (snap / "model.bin").read_bytes() == b"data"
        assert (snap / "tokenizer.json").read_bytes() == b"data"
        assert (snap / "vocabulary.txt").read_bytes() == b"data"
        assert (cache_dir / "models--Systran--faster-whisper-small" / "refs" / "main").read_text(
            encoding="utf-8"
        ) == "downloaded"
        assert mock_get.call_count == 4
        status = json.loads(progress_file.read_text(encoding="utf-8"))
        assert status["status"] == "done"
        assert status["progress_pct"] == 100

    def test_download_cancel_removes_tmp_file(self, tmp_path: Path) -> None:
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        cache_dir = tmp_path / "models"
        handler._get_config.return_value.whisper.cache_dir = str(cache_dir)
        handler._get_config.return_value.whisper.model_size = "small"
        qs = {"project": "test"}
        progress_file = _install_progress_path(handler, qs)
        cancel = threading.Event()

        fake_hub = types.SimpleNamespace(hf_hub_url=lambda repo_id, filename: f"https://example.test/{filename}")

        class Response:
            headers = {"Content-Length": "8"}

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int):
                yield b"data"
                cancel.set()
                yield b"more"

        with (
            patch.dict("sys.modules", {"huggingface_hub": fake_hub}),
            patch("clio.ui.routes.whisper_download._get_model_download_size", return_value=8),
            patch("clio.ui.routes.whisper_download._req.get", return_value=Response()),
            patch("clio.ui.routes.whisper_download.subprocess.Popen", _FakePopen),
        ):
            _run_install(handler, qs, progress_file, cancel)

        status = json.loads(progress_file.read_text(encoding="utf-8"))
        assert status["status"] == "idle"
        assert "取消" in status["message"]
        assert list(cache_dir.rglob("*.tmp")) == []
        assert not (
            cache_dir / "models--Systran--faster-whisper-small" / "snapshots" / "downloaded" / "config.json"
        ).exists()


class TestManagedWhisperInstall:
    def test_install_submits_managed_task_and_reports_progress(self, tmp_path: Path) -> None:
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind, TaskStatus
        from clio.task_center.store import TaskStore

        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        handler = _make_handler(proj_dir, proj_output)
        handler._get_task_manager = lambda: manager
        handler.config_path = None

        def install(adapter, qs, progress_path, cancel):
            _write_install_progress(
                progress_path,
                {"status": "downloading", "progress_pct": 40, "message": "下载中"},
            )
            _write_install_progress(
                progress_path,
                {"status": "done", "progress_pct": 100, "message": "完成"},
            )

        with patch("clio.ui.routes.whisper_download._run_install", side_effect=install):
            handle_post_whisper_install(handler, {})
            payload = handler._send_json.call_args.args[0]
            task = manager.wait(payload["task_id"])

        assert task.kind is TaskKind.WHISPER_INSTALL
        assert task.status is TaskStatus.SUCCEEDED
        assert task.progress_pct == 100
        assert json.loads((proj_output / ".whisper_install.json").read_text(encoding="utf-8"))["status"] == "done"

    def test_cancel_managed_install(self, tmp_path: Path) -> None:
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskStatus
        from clio.task_center.store import TaskStore

        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        handler = _make_handler(proj_dir, proj_output)
        handler._get_task_manager = lambda: manager
        handler.config_path = None
        entered = threading.Event()

        def install(adapter, qs, progress_path, cancel):
            entered.set()
            cancel.wait(timeout=2)
            _write_install_progress(
                progress_path,
                {"status": "idle", "progress_pct": 0, "message": "下载已取消"},
            )

        with patch("clio.ui.routes.whisper_download._run_install", side_effect=install):
            handle_post_whisper_install(handler, {})
            task_id = handler._send_json.call_args.args[0]["task_id"]
            assert entered.wait(timeout=2)
            handler._send_json.reset_mock()
            handle_post_whisper_install_cancel(handler, {})
            task = manager.wait(task_id)

        assert task.status is TaskStatus.CANCELLED
        assert handler._send_json.call_args.args[0]["task_id"] == task_id

    def test_status_reads_active_managed_task(self, tmp_path: Path) -> None:
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind
        from clio.task_center.store import TaskStore

        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        release = threading.Event()

        def worker(context):
            release.wait(timeout=2)

        manager.register(TaskKind.WHISPER_INSTALL, worker, cancellable=True)
        task = manager.submit(
            TaskKind.WHISPER_INSTALL,
            "安装",
            project_id=str(proj_dir.resolve()),
            project_path=str(proj_dir.resolve()),
        )
        handler = _make_handler(proj_dir, proj_output)
        handler._get_task_manager = lambda: manager

        handle_get_whisper_install_status(handler, {})

        payload = handler._send_json.call_args.args[0]
        assert payload["running"] is True
        assert payload["task_id"] == task.id
        release.set()


class TestLegacyWhisperCancellation:
    def test_cancel_with_corrupted_progress_file(self, tmp_path: Path) -> None:
        """Cancel should not crash on corrupted progress file."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_output = tmp_path / "output"
        proj_output.mkdir()
        handler = _make_handler(proj_dir, proj_output)
        qs = {"project": "test"}

        progress_file = _install_progress_path(handler, qs)
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text("not valid json", encoding="utf-8")

        handle_post_whisper_install_cancel(handler, qs)

        handler._send_json.assert_called_once_with({"ok": True, "message": "cancel requested"})

    def test_cancel_isolated_per_project(self, tmp_path: Path) -> None:
        """Cancel for project A must not affect project B's download."""
        proj_a = tmp_path / "proj_a"
        proj_a.mkdir()
        proj_b = tmp_path / "proj_b"
        proj_b.mkdir()
        out_a = tmp_path / "out_a"
        out_a.mkdir()
        out_b = tmp_path / "out_b"
        out_b.mkdir()

        handler_a = _make_handler(proj_a, out_a)
        handler_b = _make_handler(proj_b, out_b)
        cancel_a = threading.Event()
        cancel_b = threading.Event()

        qs_a = {"project": "proj_a"}
        qs_b = {"project": "proj_b"}

        progress_a = _install_progress_path(handler_a, qs_a)
        progress_b = _install_progress_path(handler_b, qs_b)

        cache_dir = tmp_path / "models"
        handler_a._get_config.return_value.whisper.cache_dir = str(cache_dir)
        handler_a._get_config.return_value.whisper.model_size = "small"
        handler_b._get_config.return_value.whisper.cache_dir = str(cache_dir)
        handler_b._get_config.return_value.whisper.model_size = "small"

        fake_hub = types.SimpleNamespace(hf_hub_url=lambda repo_id, filename: f"https://example.test/{filename}")

        class _CancellableResponse:
            def __init__(self, cancel_event: threading.Event) -> None:
                self.headers = {"Content-Length": "4"}
                self._cancel = cancel_event

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int):
                yield b"data"
                self._cancel.set()
                yield b"more"

        class _OKResponse:
            headers = {"Content-Length": "4"}

            def raise_for_status(self) -> None:
                return None

            def iter_content(self, chunk_size: int):
                yield b"data"

        call_count = [0]

        def _mock_get(url, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 4:
                return _CancellableResponse(cancel_a)
            return _OKResponse()

        with (
            patch.dict("sys.modules", {"huggingface_hub": fake_hub}),
            patch("clio.ui.routes.whisper_download._get_model_download_size", return_value=16),
            patch("clio.ui.routes.whisper_download._req.get", side_effect=_mock_get),
            patch("clio.ui.routes.whisper_download.subprocess.Popen", _FakePopen),
            patch("clio.ui.routes.whisper_download.check_cublas", return_value=True),
            patch("clio.ui.routes.whisper_download._get_model"),
            patch("clio.ui.routes.whisper_download._clear_model_cache"),
        ):
            _run_install(handler_a, qs_a, progress_a, cancel_a)
            _run_install(handler_b, qs_b, progress_b, cancel_b)

        status_a = json.loads(progress_a.read_text(encoding="utf-8"))
        status_b = json.loads(progress_b.read_text(encoding="utf-8"))
        assert status_a["status"] == "idle"
        assert "取消" in status_a["message"]
        assert status_b["status"] == "done"
        assert status_b["progress_pct"] == 100

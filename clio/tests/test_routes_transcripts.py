"""Tests for clio/ui/routes/transcripts.py and whisper_routes.py."""

from __future__ import annotations

import collections
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.routes.transcripts import handle_get_transcripts, handle_put_transcripts
from clio.ui.routes.whisper_download import _format_bytes
from clio.ui.routes.whisper_models import _list_cached_models
from clio.ui.routes.whisper_routes import (
    handle_get_whisper_check,
    handle_get_whisper_install_status,
    handle_get_whisper_models,
    handle_post_whisper_install,
    handle_post_whisper_model_delete,
    handle_put_whisper_model,
)
from clio.whisper_cache import REQUIRED_MODEL_FILES


def _handler_with_config(tmp_path: Path, transcripts_subdir: str = "transcripts") -> MagicMock:
    handler = MagicMock()
    handler.config_path = str(tmp_path / "config.yaml")
    handler._get_project_output.return_value = tmp_path
    handler._resolve_project_dir.return_value = tmp_path
    cfg = MagicMock()
    cfg.whisper.transcripts_subdir = transcripts_subdir
    handler._get_config.return_value = cfg
    return handler


class TestHandleGetTranscripts:
    def test_no_video_param(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_get_transcripts(handler, {})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing video param"}, 400)

    def test_present(self, tmp_path: Path):
        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()
        transcript = {
            "_rev": 3,
            "source_stem": "001_GL010683",
            "segments": [{"start": 0.0, "end": 2.0, "text": "hello"}],
        }
        (tmp_path / "transcripts" / "001_GL010683_transcript.json").write_text(json.dumps(transcript), encoding="utf-8")

        handler._send_json = MagicMock()
        handle_get_transcripts(handler, {"video": ["001_GL010683.mp4"]})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True
        assert args[0][0]["source_stem"] == "001_GL010683"
        assert args[0][0]["revision"] == 3

    def test_not_found(self, tmp_path: Path):
        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()

        handler._send_json = MagicMock()
        handle_get_transcripts(handler, {"video": ["nonexistent.mp4"]})
        handler._send_json.assert_called_once_with({"ok": False}, 404)


class TestHandlePutTranscripts:
    def test_update_segment(self, tmp_path: Path):
        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()
        transcript = {
            "source_stem": "001_GL010683",
            "segments": [{"start": 0.0, "end": 2.0, "text": "old text"}],
        }
        tf = tmp_path / "transcripts" / "001_GL010683_transcript.json"
        tf.write_text(json.dumps(transcript), encoding="utf-8")

        handler._send_json = MagicMock()
        handle_put_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {
                "segment_index": 0,
                "text": "new text",
                "revision": 0,
            },
        )
        handler._send_json.assert_called_once_with({"ok": True, "revision": 1})
        updated = json.loads(tf.read_text(encoding="utf-8"))
        assert updated["segments"][0]["text"] == "new text"
        assert updated["_rev"] == 1

    def test_segment_edit_rejected_on_stale_revision(self, tmp_path: Path):
        """P1-30: writing with a stale revision returns 409 and preserves the file."""
        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()
        transcript = {
            "_rev": 5,
            "source_stem": "001_GL010683",
            "segments": [{"start": 0.0, "end": 2.0, "text": "old text"}],
        }
        tf = tmp_path / "transcripts" / "001_GL010683_transcript.json"
        tf.write_text(json.dumps(transcript), encoding="utf-8")

        handler._send_json = MagicMock()
        result = handle_put_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {"segment_index": 0, "text": "new text", "revision": 4},
        )
        assert result is None
        payload, status = handler._send_json.call_args[0]
        assert status == 409
        assert payload["ok"] is False
        assert payload["revision"] == 5
        updated = json.loads(tf.read_text(encoding="utf-8"))
        assert updated["segments"][0]["text"] == "old text"
        assert updated["_rev"] == 5

    def test_missing_revision_rejected(self, tmp_path: Path):
        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()
        transcript = {"segments": [{"start": 0.0, "end": 2.0, "text": "old"}]}
        tf = tmp_path / "transcripts" / "001_GL010683_transcript.json"
        tf.write_text(json.dumps(transcript), encoding="utf-8")

        handler._send_json = MagicMock()
        handle_put_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {"segment_index": 0, "text": "new text"},
        )
        payload, status = handler._send_json.call_args[0]
        assert status == 400

    def test_invalid_index(self, tmp_path: Path):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_put_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {
                "segment_index": "not_an_int",
                "text": "new text",
            },
        )
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is False


class TestHandlePostTranscripts:
    def test_add_segment(self, tmp_path: Path):
        from clio.ui.routes.transcripts import handle_post_transcripts

        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()
        transcript = {"segments": []}
        tf = tmp_path / "transcripts" / "001_GL010683_transcript.json"
        tf.write_text(json.dumps(transcript), encoding="utf-8")

        handler._send_json = MagicMock()
        handle_post_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {"start": 5.0, "end": 8.0, "text": "hello", "revision": 0},
        )
        args = handler._send_json.call_args[0]
        assert args[0]["ok"] is True
        assert args[0]["revision"] == 1
        data = json.loads(tf.read_text(encoding="utf-8"))
        assert data["_rev"] == 1
        assert data["segments"][0]["text"] == "hello"

    def test_add_segment_rejected_on_stale_revision(self, tmp_path: Path):
        from clio.ui.routes.transcripts import handle_post_transcripts

        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()
        transcript = {"_rev": 2, "segments": []}
        tf = tmp_path / "transcripts" / "001_GL010683_transcript.json"
        tf.write_text(json.dumps(transcript), encoding="utf-8")

        handler._send_json = MagicMock()
        handle_post_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {"start": 5.0, "end": 8.0, "text": "hello", "revision": 1},
        )
        payload, status = handler._send_json.call_args[0]
        assert status == 409
        assert json.loads(tf.read_text(encoding="utf-8"))["_rev"] == 2

    def test_create_refuses_existing_file(self, tmp_path: Path):
        from clio.ui.routes.transcripts import handle_post_transcripts

        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()
        tf = tmp_path / "transcripts" / "001_GL010683_transcript.json"
        tf.write_text(json.dumps({"segments": []}), encoding="utf-8")

        handler._send_json = MagicMock()
        handle_post_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {"create": True},
        )
        payload, status = handler._send_json.call_args[0]
        assert status == 409
        assert json.loads(tf.read_text(encoding="utf-8")) == {"segments": []}

    def test_create_new_file(self, tmp_path: Path):
        from clio.ui.routes.transcripts import handle_post_transcripts

        handler = _handler_with_config(tmp_path)
        (tmp_path / "transcripts").mkdir()

        handler._send_json = MagicMock()
        handle_post_transcripts(
            handler,
            {"video": ["001_GL010683.mp4"]},
            {"create": True},
        )
        payload = handler._send_json.call_args[0][0]
        assert payload["ok"] is True
        data = json.loads((tmp_path / "transcripts" / "001_GL010683_transcript.json").read_text(encoding="utf-8"))
        assert data["_rev"] == 1
        assert data["segments"] == []


class TestHandleGetWhisperCheck:
    def test_okay(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        with (
            patch("clio.ui.routes.whisper_check.check_whisper", return_value=True),
            patch("ctranslate2.get_cuda_device_count", return_value=1),
        ):
            handle_get_whisper_check(handler, {})
            handler._send_json.assert_called_once()
            args = handler._send_json.call_args
            assert args[0][0]["installed"] is True
            assert args[0][0]["cuda"] is True

    def test_not_installed(self):
        handler = MagicMock()
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.whisper_check.check_whisper", return_value=False):
            handle_get_whisper_check(handler, {})
            args = handler._send_json.call_args
            assert args[0][0]["installed"] is False


class TestFormatBytes:
    def test_bytes(self):
        assert _format_bytes(500) == "500 B"

    def test_kb(self):
        assert _format_bytes(2048) == "2.0 KB"

    def test_mb(self):
        assert _format_bytes(5 * 1024 * 1024) == "5.0 MB"

    def test_gb(self):
        result = _format_bytes(3 * 1024 * 1024 * 1024)
        assert result == "3.00 GB"


class TestListCachedModels:
    def test_empty_dir(self, tmp_path: Path):
        assert _list_cached_models(tmp_path) == []

    def test_non_existent_dir(self, tmp_path: Path):
        assert _list_cached_models(tmp_path / "nonexistent") == []

    def test_finds_valid_model(self, tmp_path: Path):
        # Simulate huggingface cache structure
        model_dir = tmp_path / "models--Systran--faster-whisper-small"
        snapshots = model_dir / "snapshots" / "abc123"
        _write_complete_whisper_snapshot(snapshots)

        result = _list_cached_models(tmp_path)
        assert len(result) == 1
        assert result[0]["name"] == "small"
        assert result[0]["valid"] is True

    def test_skips_incomplete_model(self, tmp_path: Path):
        model_dir = tmp_path / "models--Systran--faster-whisper-large-v3"
        snapshots = model_dir / "snapshots" / "abc123"
        snapshots.mkdir(parents=True)
        # Small file < 100MB = incomplete
        (snapshots / "config.json").write_text("{}")

        result = _list_cached_models(tmp_path)
        assert len(result) == 1
        assert result[0]["valid"] is False

    def test_skips_non_whisper_dirs(self, tmp_path: Path):
        (tmp_path / "other-models").mkdir()
        (tmp_path / ".cache").mkdir()
        result = _list_cached_models(tmp_path)
        assert result == []


class TestHandleGetWhisperInstallStatus:
    def test_idle_when_no_file(self):
        handler = MagicMock()
        handler._get_project_output.return_value = Path("/nonexistent")
        handler._send_json = MagicMock()
        handle_get_whisper_install_status(handler, {})
        args = handler._send_json.call_args
        assert args[0][0]["status"] == "idle"

    def test_shows_progress(self, tmp_path: Path):
        progress = tmp_path / ".whisper_install.json"
        progress.write_text(json.dumps({"status": "downloading", "progress_pct": 42}), encoding="utf-8")
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._resolve_project_dir.return_value = tmp_path
        handler._send_json = MagicMock()
        alive = MagicMock()
        alive.is_alive.return_value = True
        fake_task = MagicMock()
        fake_task.thread = alive
        with patch.dict(
            "clio.ui.routes.whisper_download._PROJECT_TASKS",
            {str(tmp_path.resolve()): fake_task},
            clear=False,
        ):
            handle_get_whisper_install_status(handler, {})
        args = handler._send_json.call_args
        assert args[0][0]["progress_pct"] == 42

    def test_detects_stale_download(self, tmp_path: Path):
        progress = tmp_path / ".whisper_install.json"
        progress.write_text(json.dumps({"status": "downloading", "progress_pct": 42}), encoding="utf-8")
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._send_json = MagicMock()
        handle_get_whisper_install_status(handler, {})
        args = handler._send_json.call_args
        assert args[0][0]["status"] == "idle"
        assert "中断" in args[0][0]["message"]

    def test_shows_done(self, tmp_path: Path):
        progress = tmp_path / ".whisper_install.json"
        progress.write_text(json.dumps({"status": "done", "progress_pct": 100}), encoding="utf-8")
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._send_json = MagicMock()
        handle_get_whisper_install_status(handler, {})
        args = handler._send_json.call_args
        assert args[0][0]["status"] == "done"


class TestHandlePostWhisperInstall:
    def _make_handler(self, tmp_path):
        handler = MagicMock()
        handler._get_project_output.return_value = tmp_path
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.whisper.model_size = "small"
        cfg.whisper.hf_endpoint = ""
        cfg.proxy.enabled = False
        handler._get_config.return_value = cfg
        return handler

    def test_starts_download(self, tmp_path: Path):
        handler = self._make_handler(tmp_path)
        handler._send_json = MagicMock()
        with (
            patch.dict("clio.ui.routes.whisper_download._PROJECT_TASKS", {}, clear=True),
            patch("clio.ui.routes.whisper_download._run_install") as mock_run,
        ):
            handle_post_whisper_install(handler, {})
        handler._send_json.assert_called_once_with({"ok": True, "message": "whisper install started"})
        mock_run.wait(timeout=5)
        mock_run.assert_called_once()

    def test_rejects_concurrent(self, tmp_path: Path):
        handler = self._make_handler(tmp_path)
        handler._send_json = MagicMock()
        fake_task = MagicMock()
        fake_task.thread = MagicMock()
        fake_task.thread.is_alive.return_value = True
        with patch.dict(
            "clio.ui.routes.whisper_download._PROJECT_TASKS",
            {str(tmp_path.resolve()): fake_task},
            clear=True,
        ):
            handle_post_whisper_install(handler, {})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is False
        assert "already running" in args[0][0]["error"]


_DISK_USAGE = collections.namedtuple("Usage", "total used free")


def _write_complete_whisper_snapshot(snapshots: Path) -> None:
    snapshots.mkdir(parents=True, exist_ok=True)
    for filename in REQUIRED_MODEL_FILES:
        (snapshots / filename).write_bytes(b"data")


class TestHandleGetWhisperModels:
    def test_returns_available_models(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.whisper.model_size = "small"
        handler._get_config.return_value = cfg
        cache_dir = tmp_path / "models"
        cache_dir.mkdir()
        handler._send_json = MagicMock()

        with (
            patch("clio.ui.routes.whisper_models._resolve_cache_dir", return_value=cache_dir),
            patch(
                "clio.ui.routes.whisper_models.shutil.disk_usage",
                return_value=_DISK_USAGE(0, 0, 500 * 1024 * 1024),
            ),
        ):
            handle_get_whisper_models(handler, {})

        args = handler._send_json.call_args
        data = args[0][0]
        assert data["ok"] is True
        assert data["current_model"] == "small"
        assert len(data["available"]) >= 3  # small, medium, large-v3
        assert data["cached"] == []
        assert data["free_display"] is not None

    def test_with_cached_model(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        cfg.whisper.model_size = "medium"
        handler._get_config.return_value = cfg
        cache_dir = tmp_path / "models"
        cache_dir.mkdir()
        model_dir = cache_dir / "models--Systran--faster-whisper-medium"
        snapshots = model_dir / "snapshots" / "abc123"
        _write_complete_whisper_snapshot(snapshots)
        handler._send_json = MagicMock()

        with (
            patch("clio.ui.routes.whisper_models._resolve_cache_dir", return_value=cache_dir),
            patch(
                "clio.ui.routes.whisper_models.shutil.disk_usage",
                return_value=_DISK_USAGE(0, 0, 500 * 1024 * 1024),
            ),
        ):
            handle_get_whisper_models(handler, {})

        args = handler._send_json.call_args
        data = args[0][0]
        assert len(data["cached"]) == 1
        assert data["cached"][0]["name"] == "medium"
        assert data["cached"][0]["valid"] is True


class TestHandlePostWhisperModelDelete:
    def test_missing_name(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_post_whisper_model_delete(handler, {}, {})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing model name"}, 400)

    def test_deletes_model(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        handler._get_config.return_value = cfg
        cache_dir = tmp_path / "models"
        model_dir = cache_dir / "models--Systran--faster-whisper-small"
        model_dir.mkdir(parents=True)
        (model_dir / "model.bin").write_text("dummy")
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.whisper_models._resolve_cache_dir", return_value=cache_dir):
            handle_post_whisper_model_delete(handler, {}, {"name": "small"})

        assert not model_dir.exists()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True
        assert args[0][0]["deleted"] is True

    def test_returns_false_if_not_found(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        handler._get_config.return_value = cfg
        cache_dir = tmp_path / "models"
        cache_dir.mkdir()
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.whisper_models._resolve_cache_dir", return_value=cache_dir):
            handle_post_whisper_model_delete(handler, {}, {"name": "large-v3"})

        args = handler._send_json.call_args
        assert args[0][0]["deleted"] is False

    def test_rejects_name_not_in_enum(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        handler._get_config.return_value = cfg
        cache_dir = tmp_path / "models"
        cache_dir.mkdir()
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.whisper_models._resolve_cache_dir", return_value=cache_dir):
            handle_post_whisper_model_delete(handler, {}, {"name": "tiny"})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is False
        assert args[0][1] == 400

    def test_refuses_delete_while_model_loaded(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        handler._get_config.return_value = cfg
        cache_dir = tmp_path / "models"
        model_dir = cache_dir / "models--Systran--faster-whisper-medium"
        model_dir.mkdir(parents=True)
        handler._send_json = MagicMock()

        with (
            patch("clio.ui.routes.whisper_models._resolve_cache_dir", return_value=cache_dir),
            patch("clio.ui.routes.whisper_models.is_model_loaded", return_value=True),
        ):
            handle_post_whisper_model_delete(handler, {}, {"name": "medium"})

        assert model_dir.exists()
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is False
        assert args[0][1] == 409

    def test_does_not_delete_unrelated_model_by_substring(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        handler._get_config.return_value = cfg
        cache_dir = tmp_path / "models"
        small_dir = cache_dir / "models--Systran--faster-whisper-small"
        medium_dir = cache_dir / "models--Systran--faster-whisper-medium"
        small_dir.mkdir(parents=True)
        medium_dir.mkdir(parents=True)
        handler._send_json = MagicMock()

        with patch("clio.ui.routes.whisper_models._resolve_cache_dir", return_value=cache_dir):
            handle_post_whisper_model_delete(handler, {}, {"name": "small"})

        assert not small_dir.exists()
        assert medium_dir.exists()
        args = handler._send_json.call_args
        assert args[0][0]["deleted"] is True


class TestHandlePutWhisperModel:
    def test_missing_model_size(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_put_whisper_model(handler, {}, {})
        handler._send_json.assert_called_once_with({"ok": False, "error": "missing model_size"}, 400)

    def test_invalid_model_size(self):
        handler = MagicMock()
        handler._send_json = MagicMock()
        handle_put_whisper_model(handler, {}, {"model_size": "invalid"})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is False
        assert "invalid" in args[0][0]["error"].lower()

    def test_writes_to_project_yaml(self, tmp_path: Path):
        handler = MagicMock()
        handler._resolve_project_dir.return_value = tmp_path
        cfg = MagicMock()
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()
        handler.__class__._config_cache = MagicMock()

        proj_yaml = tmp_path / "project.yaml"
        proj_yaml.write_text("paths:\n  input_dir: ./videos\n", encoding="utf-8")

        handle_put_whisper_model(handler, {}, {"model_size": "medium"})
        assert proj_yaml.is_file()
        content = proj_yaml.read_text(encoding="utf-8")
        assert "model_size: medium" in content
        args = handler._send_json.call_args
        assert args[0][0]["model_size"] == "medium"

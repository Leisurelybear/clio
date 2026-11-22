"""Regression tests for waveform fixes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clio.tasks import waveform as wf


def _run_inline(fn):
    fn()


class TestFfmpegFailureCooldown:
    def test_second_ensure_does_not_restart_ffmpeg(self, tmp_path: Path):
        src = tmp_path / "broken.mp4"
        src.write_bytes(b"not a video")
        key = wf.cache_key(src)

        with (
            patch("clio.utils.probe_ffmpeg_deps", return_value={"ok": True, "detail": ""}),
            patch("clio.utils.resolve_binary", return_value="C:/fake/ffmpeg.exe"),
            patch("clio.tasks.waveform.has_audio_stream", return_value=True),
            patch("clio.tasks.waveform._spawn_job", side_effect=_run_inline),
            patch("clio.utils.run_ffmpeg", side_effect=RuntimeError("decode failed")) as run,
        ):
            first = wf.ensure_waveform(tmp_path, src, ffmpeg="", duration_sec=1.0)
            second = wf.ensure_waveform(tmp_path, src, ffmpeg="", duration_sec=1.0)

        assert first["status"] == "pending"
        assert second["status"] == "error"
        assert "decode failed" in second["error"]
        assert run.call_count == 1
        assert not wf.ready_path(tmp_path, key).exists()
        assert wf.error_path(tmp_path, key).is_file()

    def test_managed_task_fails_on_probe_error(self, tmp_path: Path):
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskStatus
        from clio.task_center.store import TaskStore

        src = tmp_path / "broken.mp4"
        src.write_bytes(b"x")
        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        payload = {"peaks": [], "bin_count": 400, "probe_error": True, "probe_error_msg": "bad"}
        with (
            patch("clio.utils.probe_ffmpeg_deps", return_value={"ok": True, "detail": ""}),
            patch("clio.tasks.waveform.extract_peaks_for_video", return_value=payload),
        ):
            pending = wf.ensure_waveform(tmp_path, src, ffmpeg="ffmpeg", task_manager=manager)
            task = manager.wait(pending["task_id"])

        assert task.status is TaskStatus.FAILED
        assert wf.read_peaks(tmp_path, pending["key"]) is None
        assert wf.recent_error(tmp_path, pending["key"]) == "bad"

"""Regression tests for waveform fixes."""

from __future__ import annotations

import random
import struct
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

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


class TestSubprocessTimeouts:
    def test_has_audio_stream_passes_a_timeout(self, tmp_path: Path):
        src = tmp_path / "v.mp4"
        src.write_bytes(b"v")

        class _R:
            stdout = "audio\n"

        with (
            patch("clio.utils.resolve_binary", return_value="C:/fake/ffprobe.exe"),
            patch("clio.utils.run_subprocess", return_value=_R()) as run,
        ):
            assert wf.has_audio_stream(src, ffprobe="") is True
        assert run.call_args.kwargs["timeout"] == wf._PROBE_TIMEOUT_SEC

    def test_deadline_event_expires_and_follows_parent(self):
        assert wf._DeadlineEvent(0).is_set() is True
        parent = threading.Event()
        event = wf._DeadlineEvent(60, parent)
        assert event.is_set() is False
        parent.set()
        assert event.is_set() is True

    def test_decode_gets_cancel_event_and_cancel_is_not_a_probe_error(self, tmp_path: Path):
        src = tmp_path / "v.mp4"
        src.write_bytes(b"v")
        cancel = threading.Event()
        seen = {}

        def fake_run(args, ffmpeg, **kwargs):
            seen["cancel_event"] = kwargs.get("cancel_event")
            cancel.set()
            raise InterruptedError("cancelled")

        with (
            patch("clio.utils.resolve_binary", return_value="C:/fake/ffmpeg.exe"),
            patch("clio.tasks.waveform.has_audio_stream", return_value=True),
            patch("clio.utils.run_ffmpeg", side_effect=fake_run),
        ):
            with pytest.raises(InterruptedError):
                wf.extract_peaks_for_video(src, ffmpeg="", duration_sec=1.0, cancel_event=cancel)
        assert seen["cancel_event"] is not None

    def test_waveform_tasks_are_cancellable(self, tmp_path: Path):
        from clio.task_center.manager import TaskManager
        from clio.task_center.models import TaskKind
        from clio.task_center.store import TaskStore

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        wf._ensure_waveform_handler(manager)
        assert manager.registry.require(TaskKind.WAVEFORM).cancellable is True


def _reference_peaks(pcm: bytes, bin_count: int) -> list[float]:
    """The original per-sample implementation, kept as an oracle."""
    n = len(pcm) // 2
    peaks = [0.0] * bin_count
    for i in range(n):
        s = struct.unpack_from("<h", pcm, i * 2)[0]
        b = min(bin_count - 1, int(i * bin_count / n))
        peaks[b] = max(peaks[b], abs(s) / 32768.0)
    m = max(peaks)
    return [p / m for p in peaks] if m > 0 else peaks


class TestPeaksMatchReference:
    @pytest.mark.parametrize(("samples", "bins"), [(1, 4), (7, 10), (399, 400), (401, 400), (5000, 400), (4321, 2000)])
    def test_same_bins_as_per_sample_loop(self, samples: int, bins: int):
        rng = random.Random(samples * 31 + bins)
        pcm = bytes(rng.getrandbits(8) for _ in range(samples * 2))
        assert wf.peaks_from_pcm_s16le(pcm, bin_count=bins) == _reference_peaks(pcm, bins)

    def test_min_sample_maps_to_full_scale(self):
        pcm = struct.pack("<h", -32768) + b"\x00\x00" * 9
        assert wf.peaks_from_pcm_s16le(pcm, bin_count=2) == [1.0, 0.0]


def test_key_locks_are_a_fixed_pool():
    locks = {id(wf._key_lock(f"key-{i}")) for i in range(1000)}
    assert len(locks) <= wf._KEY_LOCK_STRIPES
    assert wf._key_lock("same") is wf._key_lock("same")

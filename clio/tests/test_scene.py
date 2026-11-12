from __future__ import annotations

import io
import subprocess
import threading

import pytest

from clio.scene import (
    align_shots_to_boundaries,
    detect_scene_boundaries,
    format_scene_timestamp,
    normalize_scene_boundaries,
    parse_scene_timestamps,
    scene_ranges,
    slice_scene_boundaries,
)


class _FakeProcess:
    def __init__(self, stderr: str = "", returncode: int = 0, *, block: bool = False) -> None:
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode
        self._block = block
        self.terminated = False
        self.killed = False
        self.command: list[str] | None = None

    def wait(self, timeout: float | None = None) -> int:
        if self._block and not self.terminated:
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0.0)
        return self.returncode

    def poll(self) -> int | None:
        return None if self._block and not self.terminated else self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.terminated = True


def test_normalize_scene_boundaries_clamps_sorts_and_deduplicates():
    assert normalize_scene_boundaries([12, -2, "5.0", 5.0005, 99, "bad"], 20) == [0.0, 5.0, 12.0, 20.0]


def test_parse_scene_timestamps_uses_showinfo_pts():
    stderr = "pts_time:1.2\npts_time:1.2005\npts_time:-1\npts_time:25\n"

    assert parse_scene_timestamps(stderr, 20) == [0.0, 1.2, 20.0]


def test_slice_scene_boundaries_returns_window_local_times():
    assert slice_scene_boundaries([0, 2, 8, 12, 20], 8, 20) == [0.0, 4.0, 12.0]


def test_scene_ranges_excludes_duplicate_endpoints():
    assert scene_ranges([0, 0, 4.5, 10]) == [(0.0, 4.5), (4.5, 10.0)]


def test_format_scene_timestamp():
    assert format_scene_timestamp(65.25) == "01:05.25"
    assert format_scene_timestamp(3605) == "01:00:05"


def test_align_shots_to_boundaries_snaps_nearby_times_only():
    analysis = {
        "timeline": [
            {"start": "00:00.2", "end": "00:05.3"},
            {"start": 7, "end": 8.8},
        ]
    }

    result = align_shots_to_boundaries(analysis, [0, 5, 10])

    assert result["timeline"][0]["start"] == "00:00"
    assert result["timeline"][0]["end"] == "00:05"
    assert result["timeline"][1]["start"] == 7
    assert result["timeline"][1]["end"] == 8.8


def test_detect_scene_boundaries_invokes_ffmpeg_and_parses_output(monkeypatch):
    process = _FakeProcess("pts_time:3.5\npts_time:9.1\n")
    seen: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return process

    monkeypatch.setattr("clio.scene.resolve_binary", lambda configured, fallback: "/ffmpeg")
    monkeypatch.setattr("clio.scene.popen_subprocess", fake_popen)
    monkeypatch.setattr("clio.scene.register_process", lambda proc: None)
    monkeypatch.setattr("clio.scene.unregister_process", lambda proc: None)

    result = detect_scene_boundaries("clip.mp4", 12, "/ffmpeg")

    assert result == [0.0, 3.5, 9.1, 12.0]
    command = seen["command"]
    assert isinstance(command, list)
    assert "select='gt(scene,0.35)'" in command[command.index("-vf") + 1]


def test_detect_scene_boundaries_degrades_on_ffmpeg_failure(monkeypatch):
    process = _FakeProcess("fatal", returncode=1)
    monkeypatch.setattr("clio.scene.resolve_binary", lambda configured, fallback: "/ffmpeg")
    monkeypatch.setattr("clio.scene.popen_subprocess", lambda *args, **kwargs: process)
    monkeypatch.setattr("clio.scene.register_process", lambda proc: None)
    monkeypatch.setattr("clio.scene.unregister_process", lambda proc: None)

    assert detect_scene_boundaries("clip.mp4", 12, "/ffmpeg") == []


def test_detect_scene_boundaries_degrades_on_timeout(monkeypatch):
    process = _FakeProcess(block=True)
    monkeypatch.setattr("clio.scene.resolve_binary", lambda configured, fallback: "/ffmpeg")
    monkeypatch.setattr("clio.scene.popen_subprocess", lambda *args, **kwargs: process)
    monkeypatch.setattr("clio.scene.register_process", lambda proc: None)
    monkeypatch.setattr("clio.scene.unregister_process", lambda proc: None)

    assert detect_scene_boundaries("clip.mp4", 12, "/ffmpeg", timeout_sec=0.001) == []
    assert process.terminated


def test_detect_scene_boundaries_propagates_cancellation(monkeypatch):
    process = _FakeProcess(block=True)
    cancel_event = threading.Event()
    cancel_event.set()
    monkeypatch.setattr("clio.scene.resolve_binary", lambda configured, fallback: "/ffmpeg")
    monkeypatch.setattr("clio.scene.popen_subprocess", lambda *args, **kwargs: process)
    monkeypatch.setattr("clio.scene.register_process", lambda proc: None)
    monkeypatch.setattr("clio.scene.unregister_process", lambda proc: None)

    with pytest.raises(InterruptedError):
        detect_scene_boundaries("clip.mp4", 12, "/ffmpeg", cancel_event=cancel_event)

    assert process.terminated

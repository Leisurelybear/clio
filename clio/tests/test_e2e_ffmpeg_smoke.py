"""Real-ffmpeg smoke coverage for the production compress-to-analysis path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from clio.ai.base import AIResponse
from clio.config import load_config
from clio.pipeline import run_pipeline_steps
from clio.tasks._video_loader import save_selected_videos
from clio.utils import discover_ffmpeg_bin, get_duration_sec


def _make_video(ffmpeg: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=15",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


class _FakeVideoProvider:
    provider_id = "fake"

    def __init__(self) -> None:
        self.video_calls: list[Path] = []

    def analyze_video(self, video_path: str, *_args: Any, **_kwargs: Any) -> AIResponse:
        self.video_calls.append(Path(video_path))
        return AIResponse(
            json.dumps(
                {
                    "title": "Real ffmpeg smoke",
                    "summary": "A generated clip reached the fake provider.",
                    "location": "Test bench",
                    "timeline": [{"start": 0, "end": 1.5, "description": "Synthetic color bars move."}],
                }
            )
        )

    def generate_text(self, *_args: Any, **_kwargs: Any) -> AIResponse:
        raise AssertionError("single-window smoke test must not request a text summary")

    def close(self) -> None:
        pass


def test_real_ffmpeg_compresses_and_analyzes_with_fake_provider(
    tmp_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ffmpeg = discover_ffmpeg_bin("ffmpeg")
    ffprobe = discover_ffmpeg_bin("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg/ffprobe are not installed")

    raw = tmp_config / "raw" / "20261113_120000.mp4"
    _make_video(ffmpeg, raw)
    save_selected_videos(tmp_config, [raw])

    config = load_config(tmp_config / "config.yaml", project_dir=tmp_config)
    config.paths._global.ffmpeg = ffmpeg
    config.paths._global.ffprobe = ffprobe
    config.analyze.skip_existing = False
    config.analyze.max_workers = 1
    provider = _FakeVideoProvider()
    monkeypatch.setattr("clio.analyze.get_video_provider", lambda *_args: (provider, "fake-model"))

    summary = run_pipeline_steps(config, steps=["compress", "analyze"])

    compressed = sorted(config.compressed_dir.glob("*.mp4"))
    assert len(compressed) == 1
    assert compressed[0].stem == "001_20261113_120000"
    assert get_duration_sec(compressed[0], config.paths._global.ffprobe) > 0
    assert compressed[0].with_suffix(".vmeta").is_file()
    assert (config.compressed_dir / f"{raw.stem}.vindex").is_file()
    assert provider.video_calls == [compressed[0]]

    analyses = sorted(config.texts_dir.glob("*.json"))
    assert len(analyses) == 1
    payload = json.loads(analyses[0].read_text(encoding="utf-8"))
    assert payload["title"] == "Real ffmpeg smoke"
    assert payload["compressed_file"] == compressed[0].name
    assert payload["source_file"] == raw.name
    assert summary["steps"] == [
        {"name": "compress", "processed": 1, "failed": 0},
        {"name": "analyze", "processed": 1, "failed": 0},
    ]

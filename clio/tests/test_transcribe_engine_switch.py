from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.tasks.transcribe import run_transcribe_all


def _write_transcript(out_path: Path, engine: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"engine": engine, "segments": []}), encoding="utf-8")


def _make_config(tmp_path: Path, engine: str, transcript_engine: str | None = None):
    compressed_dir = tmp_path / "compressed"
    compressed_dir.mkdir(parents=True, exist_ok=True)
    (compressed_dir / "001_test.mp4").touch()
    transcripts_dir = tmp_path / "transcripts"
    _write_transcript(
        transcripts_dir / "001_test_transcript.json",
        transcript_engine if transcript_engine is not None else engine,
    )

    cfg = MagicMock()
    cfg.whisper.enabled = True
    cfg.whisper.engine = engine if engine == "local" else "aliyun"
    cfg.whisper.language = "zh"
    cfg.whisper.model_size = "medium"
    cfg.analyze.skip_existing = True
    cfg.analyze.compressed_subdir = "compressed"
    cfg.project_dir = tmp_path
    cfg.paths.output_dir = tmp_path
    cfg.compress.remove_audio = False
    cfg.transcripts_dir = transcripts_dir
    return cfg


@pytest.fixture(autouse=True)
def _skip_whisper_check():
    with patch("clio.tasks.transcribe.check_whisper", return_value=True):
        yield


def test_engine_mismatch_skips_with_hint(tmp_path: Path, capsys):
    cfg = _make_config(tmp_path, engine="aliyun", transcript_engine="local")
    video = tmp_path / "compressed" / "001_test.mp4"

    with (
        patch("clio.tasks.transcribe.find_videos", return_value=[video]),
        patch("clio.tasks.transcribe._build_original_stem_map", return_value={"test": video.parent}),
        patch("clio.tasks.transcribe.resolve_identity"),
        patch("clio.tasks.transcribe._identity_to_dict", return_value={}),
        patch("clio.tasks.transcribe.add_schema_version"),
        patch("clio.tasks.transcribe.enrich_matching_analysis_files", return_value=[]),
        patch("clio.tasks.transcribe.transcribe_audio") as mock_ta,
        patch("clio.tasks.transcribe._extract_audio", return_value=tmp_path / "fake.wav"),
        patch("clio.tasks.transcribe.resolve_binary", return_value="ffmpeg"),
        patch("clio.tasks.transcribe._get_video_duration", return_value=10.0),
        patch("clio.tasks.transcribe.write_json_atomic"),
    ):
        run_transcribe_all(cfg)

    mock_ta.assert_not_called()
    output = capsys.readouterr().out
    assert "engine=local" in output or "local" in output
    assert "aliyun" in output


def test_legacy_transcript_without_engine_defaults_to_local(tmp_path: Path, capsys):
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir(parents=True)
    (transcripts_dir / "001_test_transcript.json").write_text(json.dumps({"segments": []}), encoding="utf-8")

    cfg = _make_config(tmp_path, engine="local")
    cfg.transcripts_dir = transcripts_dir
    video = tmp_path / "compressed" / "001_test.mp4"

    with (
        patch("clio.tasks.transcribe.find_videos", return_value=[video]),
        patch("clio.tasks.transcribe.transcribe_audio") as mock_ta,
    ):
        run_transcribe_all(cfg)

    mock_ta.assert_not_called()


def test_engine_match_skips_retranscription(tmp_path: Path):
    cfg = _make_config(tmp_path, "local")
    video = tmp_path / "compressed" / "001_test.mp4"

    with (
        patch("clio.tasks.transcribe.find_videos", return_value=[video]),
        patch("clio.tasks.transcribe.transcribe_audio") as mock_ta,
    ):
        run_transcribe_all(cfg)

    mock_ta.assert_not_called()

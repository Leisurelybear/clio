from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.tasks.transcribe import run_transcribe_all


def _write_transcript(out_path: Path, **metadata) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"segments": [], **metadata}), encoding="utf-8")


def _make_config(
    tmp_path: Path,
    engine: str = "local",
    transcript_metadata: dict | None = None,
):
    compressed_dir = tmp_path / "compressed"
    compressed_dir.mkdir(parents=True, exist_ok=True)
    video = compressed_dir / "001_test.mp4"
    video.touch()
    transcripts_dir = tmp_path / "transcripts"
    _write_transcript(
        transcripts_dir / "001_test_transcript.json",
        **(transcript_metadata or {"engine": engine, "model_size": "medium", "language": "zh"}),
    )

    cfg = MagicMock()
    cfg.whisper.enabled = True
    cfg.whisper.engine = engine
    cfg.whisper.language = "zh"
    cfg.whisper.model_size = "medium"
    cfg.analyze.skip_existing = True
    cfg.analyze.compressed_subdir = "compressed"
    cfg.project_dir = tmp_path
    cfg.paths.output_dir = tmp_path
    cfg.compress.remove_audio = False
    cfg.transcripts_dir = transcripts_dir
    return cfg, video


def _run_with_mocks(cfg, video, tmp_path: Path):
    fake_wav = tmp_path / "fake.wav"
    fake_wav.touch()
    with (
        patch("clio.tasks.transcribe.find_videos", return_value=[video]),
        patch("clio.transcribe.check_whisper", return_value=True),
        patch("clio.tasks.transcribe._build_original_stem_map", return_value={"test": video.parent}),
        patch("clio.tasks.transcribe.resolve_identity"),
        patch("clio.tasks.transcribe._identity_to_dict", return_value={}),
        patch("clio.tasks.transcribe.add_schema_version"),
        patch("clio.tasks.transcribe.enrich_matching_analysis_files", return_value=[]),
        patch("clio.tasks.transcribe.transcribe_audio", return_value=[]) as mock_transcribe,
        patch("clio.tasks.transcribe._extract_audio", return_value=fake_wav),
        patch("clio.tasks.transcribe.resolve_binary", return_value="ffmpeg"),
        patch("clio.tasks.transcribe._get_video_duration", return_value=10.0),
        patch("clio.tasks.transcribe.write_json_atomic"),
    ):
        result = run_transcribe_all(cfg)
    return result, mock_transcribe


@pytest.mark.parametrize(
    ("changed", "existing", "current"),
    [
        ("engine", "aliyun", "local"),
        ("model_size", "small", "medium"),
        ("language", "en", "zh"),
    ],
)
def test_metadata_mismatch_retranscribes(tmp_path: Path, changed, existing, current, capsys):
    metadata = {"engine": "local", "model_size": "medium", "language": "zh"}
    metadata[changed] = existing
    cfg, video = _make_config(tmp_path, transcript_metadata=metadata)
    if changed == "engine":
        cfg.whisper.engine = current
    elif changed == "model_size":
        cfg.whisper.model_size = current
    else:
        cfg.whisper.language = current

    result, mock_transcribe = _run_with_mocks(cfg, video, tmp_path)

    assert result == 0
    mock_transcribe.assert_called_once()
    assert "重新转录" in capsys.readouterr().out


@pytest.mark.parametrize("missing", ["engine", "model_size", "language"])
def test_legacy_transcript_missing_metadata_retranscribes(tmp_path: Path, missing):
    metadata = {"engine": "local", "model_size": "medium", "language": "zh"}
    metadata.pop(missing)
    cfg, video = _make_config(tmp_path, transcript_metadata=metadata)

    result, mock_transcribe = _run_with_mocks(cfg, video, tmp_path)

    assert result == 0
    mock_transcribe.assert_called_once()


def test_complete_metadata_match_skips_retranscription(tmp_path: Path):
    cfg, video = _make_config(tmp_path)

    result, mock_transcribe = _run_with_mocks(cfg, video, tmp_path)

    assert result == 0
    mock_transcribe.assert_not_called()


def test_non_object_transcript_retranscribes(tmp_path: Path):
    cfg, video = _make_config(tmp_path)
    (cfg.transcripts_dir / "001_test_transcript.json").write_text("[]", encoding="utf-8")

    result, mock_transcribe = _run_with_mocks(cfg, video, tmp_path)

    assert result == 0
    mock_transcribe.assert_called_once()

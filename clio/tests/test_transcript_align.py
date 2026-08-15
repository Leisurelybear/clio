"""Tests for clio/tasks/transcript_align.py — attach Whisper snippets to analysis."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from clio.tasks.transcript_align import (
    _load_transcript,
    _overlap_sec,
    _parse_time_sec,
    _transcript_path_for_analysis,
    attach_transcript_data,
    attach_transcript_to_analysis,
    enrich_matching_analysis_files,
)


def _cfg(transcripts_dir: Path, texts_dir: Path | None = None, remove_audio: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        transcripts_dir=transcripts_dir,
        texts_dir=texts_dir,
        compress=SimpleNamespace(remove_audio=remove_audio),
        whisper=SimpleNamespace(max_segments_per_clip=5),
    )


def _identity(compressed_stem: str, offset: float = 0.0, segment_index: int | None = None) -> dict:
    return {
        "media_identity": {
            "original_stem": "GL010683",
            "original_path": "GL010683.mp4",
            "compressed_stem": compressed_stem,
            "compressed_path": f"{compressed_stem}.mp4",
            "index": "001",
            "segment_index": segment_index,
            "segment_offset_sec": offset,
        }
    }


class TestParseTimeSec:
    def test_mm_ss(self) -> None:
        assert _parse_time_sec("01:30") == 90.0

    def test_hh_mm_ss(self) -> None:
        assert _parse_time_sec("01:02:03") == 3723.0

    def test_float_seconds(self) -> None:
        assert _parse_time_sec("00:01.5") == 1.5

    def test_invalid(self) -> None:
        assert _parse_time_sec("not-a-time") is None

    def test_invalid_numeric_part(self) -> None:
        assert _parse_time_sec("ab:cd") is None

    def test_empty(self) -> None:
        assert _parse_time_sec("") is None

    def test_none(self) -> None:
        assert _parse_time_sec(None) is None


class TestOverlapSec:
    def test_overlapping(self) -> None:
        assert _overlap_sec(0, 10, 5, 15) == 5.0

    def test_contained(self) -> None:
        assert _overlap_sec(0, 20, 5, 10) == 5.0

    def test_disjoint(self) -> None:
        assert _overlap_sec(0, 5, 10, 15) == 0.0

    def test_negative_results_clamped(self) -> None:
        assert _overlap_sec(10, 15, 0, 5) == 0.0


class TestTranscriptPathForAnalysis:
    def test_identity_match(self, tmp_path: Path) -> None:
        transcripts = tmp_path / "t"
        transcripts.mkdir()
        (transcripts / "001_GL010683_transcript.json").write_text("{}", encoding="utf-8")
        analysis = {**_identity("001_GL010683"), "compressed_file": "001_GL010683.mp4"}
        cfg = _cfg(transcripts)
        assert _transcript_path_for_analysis(cfg, analysis) == transcripts / "001_GL010683_transcript.json"

    def test_fallback_by_compressed_stem(self, tmp_path: Path) -> None:
        transcripts = tmp_path / "t"
        transcripts.mkdir()
        (transcripts / "001_GL010683_transcript.json").write_text("{}", encoding="utf-8")
        analysis = {"compressed_file": "001_GL010683.mp4"}
        cfg = _cfg(transcripts)
        assert _transcript_path_for_analysis(cfg, analysis) == transcripts / "001_GL010683_transcript.json"

    def test_no_transcripts_dir(self) -> None:
        cfg = SimpleNamespace(transcripts_dir=None)
        assert _transcript_path_for_analysis(cfg, {}) is None

    def test_no_match(self, tmp_path: Path) -> None:
        transcripts = tmp_path / "t"
        transcripts.mkdir()
        cfg = _cfg(transcripts)
        assert _transcript_path_for_analysis(cfg, {"compressed_file": "nope.mp4"}) is None


class TestLoadTranscript:
    def test_valid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "t.json"
        p.write_text('{"segments": []}', encoding="utf-8")
        assert _load_transcript(p) == {"segments": []}

    def test_invalid_json(self, tmp_path: Path) -> None:
        p = tmp_path / "t.json"
        p.write_text("{not json", encoding="utf-8")
        assert _load_transcript(p) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        assert _load_transcript(tmp_path / "nope.json") is None

    def test_none_path(self) -> None:
        assert _load_transcript(None) is None

    def test_non_dict_json(self, tmp_path: Path) -> None:
        p = tmp_path / "t.json"
        p.write_text("[1,2,3]", encoding="utf-8")
        assert _load_transcript(p) is None


class TestAttachTranscriptData:
    def test_no_timeline_returns_false(self) -> None:
        cfg = _cfg(Path("."))
        assert attach_transcript_data(cfg, {"timeline": None}, {"segments": []}) is False

    def test_empty_segments_returns_false(self) -> None:
        cfg = _cfg(Path("."))
        assert attach_transcript_data(cfg, {"timeline": []}, {"segments": []}) is False

    def test_attaches_matching_snippet(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {
            "timeline": [{"start": "00:05", "end": "00:15", "description": "clip"}],
            **_identity("001_GL010683"),
        }
        transcript = {"segments": [{"start": 6.0, "end": 10.0, "text": "hello world", "avg_logprob": -0.5}]}
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is True
        item = analysis["timeline"][0]
        assert item["transcript"] == "hello world"
        assert item["transcript_segments"][0]["start"] == 6.0
        assert item["transcript_segments"][0]["overlap_sec"] > 0

    def test_invalid_time_range_skipped(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {"timeline": [{"start": "00:20", "end": "00:10", "description": "reversed"}]}
        changed = attach_transcript_data(cfg, analysis, {"segments": [{"start": 0, "end": 30, "text": "x"}]})
        assert changed is False

    def test_non_dict_timeline_items_skipped(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {"timeline": ["not-a-dict"]}
        assert attach_transcript_data(cfg, analysis, {"segments": [{"start": 0, "end": 10, "text": "x"}]}) is False

    def test_stale_transcript_removed_when_no_match(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {
            "timeline": [
                {"start": "00:50", "end": "00:60", "description": "d", "transcript": "old", "transcript_segments": [{}]}
            ]
        }
        transcript = {"segments": [{"start": 0, "end": 10, "text": "early"}]}
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is True
        item = analysis["timeline"][0]
        assert "transcript" not in item
        assert "transcript_segments" not in item

    def test_segment_offset_applied_when_split_and_no_audio(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, remove_audio=True)
        analysis = {
            "timeline": [{"start": "00:00", "end": "00:10", "description": "d"}],
            **_identity("001_GL010683_seg01", offset=30.0, segment_index=1),
        }
        transcript = {"segments": [{"start": 35.0, "end": 38.0, "text": "in-segment"}]}
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is True
        assert analysis["timeline"][0]["transcript"] == "in-segment"

    def test_no_offset_when_audio_present(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path, remove_audio=False)
        analysis = {
            "timeline": [{"start": "00:00", "end": "00:10", "description": "d"}],
            **_identity("001_GL010683_seg01", offset=30.0, segment_index=1),
        }
        transcript = {"segments": [{"start": 5.0, "end": 7.0, "text": "early-audio"}]}
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is True
        assert analysis["timeline"][0]["transcript"] == "early-audio"

    def test_max_segments_caps_kept(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        cfg.whisper = SimpleNamespace(max_segments_per_clip=2)
        analysis = {
            "timeline": [{"start": "00:00", "end": "00:60", "description": "d"}],
            **_identity("001_GL010683"),
        }
        transcript = {
            "segments": [
                {"start": 1, "end": 2, "text": "a"},
                {"start": 3, "end": 4, "text": "b"},
                {"start": 5, "end": 6, "text": "c"},
            ]
        }
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is True
        assert len(analysis["timeline"][0]["transcript_segments"]) == 2
        assert analysis["timeline"][0]["transcript"] == "a b"

    def test_empty_text_segments_excluded(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {
            "timeline": [{"start": "00:00", "end": "00:60", "description": "d"}],
            **_identity("001_GL010683"),
        }
        transcript = {"segments": [{"start": 1, "end": 2, "text": " "}, {"start": 3, "end": 4, "text": "real"}]}
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is True
        assert analysis["timeline"][0]["transcript"] == "real"

    def test_non_numeric_segment_times_skipped(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {
            "timeline": [{"start": "00:00", "end": "00:60", "description": "d"}],
            **_identity("001_GL010683"),
        }
        transcript = {"segments": [{"start": "bad", "end": "also-bad", "text": "x"}]}
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is False

    def test_non_dict_segments_skipped(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {
            "timeline": [{"start": "00:00", "end": "00:60", "description": "d"}],
            **_identity("001_GL010683"),
        }
        transcript = {"segments": ["not-a-dict"]}
        assert attach_transcript_data(cfg, analysis, transcript) is False

    def test_all_empty_text_segments_removes_stale(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {
            "timeline": [
                {"start": "00:00", "end": "00:60", "description": "d", "transcript": "old", "transcript_segments": [{}]}
            ],
            **_identity("001_GL010683"),
        }
        transcript = {"segments": [{"start": 1, "end": 2, "text": "   "}]}
        changed = attach_transcript_data(cfg, analysis, transcript)
        assert changed is True
        assert "transcript" not in analysis["timeline"][0]


class TestAttachTranscriptToAnalysis:
    def test_no_transcript_file_returns_false(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path)
        analysis = {"timeline": [], **_identity("001_GL010683")}
        assert attach_transcript_to_analysis(cfg, analysis) is False

    def test_file_found_and_attached(self, tmp_path: Path) -> None:
        transcripts = tmp_path / "t"
        transcripts.mkdir()
        transcript_file = transcripts / "001_GL010683_transcript.json"
        transcript_file.write_text(json.dumps({"segments": [{"start": 1, "end": 2, "text": "hi"}]}), encoding="utf-8")
        cfg = _cfg(transcripts)
        analysis = {"timeline": [{"start": "00:00", "end": "00:10", "description": "d"}], **_identity("001_GL010683")}
        assert attach_transcript_to_analysis(cfg, analysis) is True
        assert analysis["timeline"][0]["transcript"] == "hi"


class TestEnrichMatchingAnalysisFiles:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        transcripts = tmp_path / "t"
        texts = tmp_path / "x"
        transcripts.mkdir()
        texts.mkdir()
        return transcripts, texts

    def test_no_identity_no_source_stem_returns_zero(self, tmp_path: Path) -> None:
        transcripts, texts = self._setup(tmp_path)
        cfg = _cfg(transcripts, texts_dir=texts)
        assert enrich_matching_analysis_files(cfg, {"segments": []}) == 0

    def test_updates_matching_analysis_files(self, tmp_path: Path) -> None:
        transcripts, texts = self._setup(tmp_path)
        cfg = _cfg(transcripts, texts_dir=texts)
        analysis = {
            "title": "T",
            "summary": "S",
            "timeline": [{"start": "00:00", "end": "00:10", "description": "d"}],
            "source_file": "GL010683.mp4",
            "compressed_file": "001_GL010683.mp4",
            **_identity("001_GL010683"),
        }
        (texts / "001_GL010683.json").write_text(json.dumps(analysis), encoding="utf-8")
        (texts / "other.json").write_text(json.dumps({**analysis, **_identity("002_OTHER")}), encoding="utf-8")
        transcript = {"segments": [{"start": 1, "end": 2, "text": "hello"}], **_identity("001_GL010683")}
        assert enrich_matching_analysis_files(cfg, transcript) == 1
        updated = json.loads((texts / "001_GL010683.json").read_text(encoding="utf-8"))
        assert updated["timeline"][0]["transcript"] == "hello"
        other = json.loads((texts / "other.json").read_text(encoding="utf-8"))
        assert "transcript" not in other["timeline"][0]
        assert (texts / "001_GL010683.txt").is_file()

    def test_legacy_analysis_without_identity_matches(self, tmp_path: Path) -> None:
        transcripts, texts = self._setup(tmp_path)
        cfg = _cfg(transcripts, texts_dir=texts)
        analysis = {
            "title": "T",
            "summary": "S",
            "timeline": [{"start": "00:00", "end": "00:10", "description": "d"}],
            "source_file": "GL010683.mp4",
            "compressed_file": "001_GL010683.mp4",
        }
        (texts / "001_GL010683.json").write_text(json.dumps(analysis), encoding="utf-8")
        transcript = {"segments": [{"start": 1, "end": 2, "text": "hello"}], **_identity("001_GL010683")}
        assert enrich_matching_analysis_files(cfg, transcript) == 1
        updated = json.loads((texts / "001_GL010683.json").read_text(encoding="utf-8"))
        assert updated["timeline"][0]["transcript"] == "hello"

    def test_corrupt_json_skipped(self, tmp_path: Path) -> None:
        transcripts, texts = self._setup(tmp_path)
        cfg = _cfg(transcripts, texts_dir=texts)
        (texts / "001_GL010683.json").write_text("{corrupt", encoding="utf-8")
        transcript = {"segments": [{"start": 1, "end": 2, "text": "hello"}], **_identity("001_GL010683")}
        assert enrich_matching_analysis_files(cfg, transcript) == 0

    def test_missing_texts_dir_returns_zero(self, tmp_path: Path) -> None:
        cfg = _cfg(tmp_path / "t", texts_dir=tmp_path / "nope")
        transcript = {"segments": [], **_identity("001_GL010683")}
        assert enrich_matching_analysis_files(cfg, transcript) == 0

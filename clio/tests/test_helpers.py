"""Tests for clio/tasks/_helpers.py and related pure logic."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from clio.tasks._helpers import (
    ClipRecord,
    _build_stem,
    _eta_line,
    _get_video_info,
    _matches_selected_artifact,
    _matches_selected_stem,
    _next_index,
    _rewrite_script_md,
    _rewrite_text_file,
    _write_csv,
    _write_text_file,
)
from clio.vmeta import VideoMeta


def _fake_config(index_width: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        naming=SimpleNamespace(index_width=index_width),
        paths=SimpleNamespace(ffprobe=""),
    )


class TestNextIndex:
    def test_nonexistent_dir_returns_one(self):
        assert _next_index(Path("/nonexistent")) == 1

    def test_empty_dir_returns_one(self, tmp_path: Path):
        assert _next_index(tmp_path) == 1

    def test_finds_max_index(self, tmp_path: Path):
        (tmp_path / "001_foo.mp4").write_bytes(b"")
        (tmp_path / "003_bar.mp4").write_bytes(b"")
        (tmp_path / "002_baz.mp4").write_bytes(b"")
        assert _next_index(tmp_path) == 4

    def test_ignores_no_underscore(self, tmp_path: Path):
        (tmp_path / "foo.mp4").write_bytes(b"")
        assert _next_index(tmp_path) == 1

    def test_ignores_non_digit_prefix(self, tmp_path: Path):
        (tmp_path / "abc_foo.mp4").write_bytes(b"")
        assert _next_index(tmp_path) == 1

    def test_mixed_valid_and_invalid(self, tmp_path: Path):
        (tmp_path / "001_foo.mp4").write_bytes(b"")
        (tmp_path / "bar.mp4").write_bytes(b"")
        (tmp_path / "abc_baz.mp4").write_bytes(b"")
        assert _next_index(tmp_path) == 2

    def test_large_index(self, tmp_path: Path):
        (tmp_path / "999_foo.mp4").write_bytes(b"")
        assert _next_index(tmp_path) == 1000

    def test_custom_width(self, tmp_path: Path):
        (tmp_path / "0001_foo.mp4").write_bytes(b"")
        (tmp_path / "0003_bar.mp4").write_bytes(b"")
        assert _next_index(tmp_path, 4) == 4


class TestBuildStem:
    def test_basic(self):
        result = _build_stem(1, "Hello World", _fake_config())
        assert result == "001_Hello_World"

    def test_sanitizes_title(self):
        result = _build_stem(42, "Great: View!", _fake_config())
        assert result == "042_Great_View!"


class TestEtaLine:
    def test_first_item_no_eta(self):
        line = _eta_line("分析", 1, 5, "test.mp4", 0, 0.0)
        assert line == "[分析 1/5] test.mp4"

    def test_with_eta(self):
        line = _eta_line("压缩", 3, 10, "vid.mp4", 2, 10.0)
        assert "剩余" in line
        assert "平均" in line
        assert "[压缩 3/10]" in line


class TestWriteTextFile:
    def test_writes_basic_structure(self, tmp_path: Path):
        out = tmp_path / "001_test.txt"
        analysis = {
            "title": "My Video",
            "summary": "A nice clip",
            "location": "Paris",
            "mood": "happy",
            "suggested_use": "opening",
            "timeline": [{"start": "0:00", "end": "1:30", "description": "intro"}],
            "highlights": ["great shot"],
        }
        _write_text_file(out, analysis, tmp_path / "source.mp4", tmp_path / "compressed.mp4")
        text = out.read_text(encoding="utf-8")
        assert "My Video" in text
        assert "A nice clip" in text
        assert "Paris" in text
        assert "0:00 - 1:30" in text
        assert "great shot" in text

    def test_empty_analysis(self, tmp_path: Path):
        out = tmp_path / "001_test.txt"
        _write_text_file(out, {}, tmp_path / "source.mp4", tmp_path / "compressed.mp4")
        text = out.read_text(encoding="utf-8")
        assert "未命名" in text

    def test_renders_dict_highlights(self, tmp_path: Path):
        out = tmp_path / "001_test.txt"
        analysis = {
            "title": "T",
            "highlights": [
                "plain shot",
                {"start": "0:10", "description": "sunset", "reason": "golden light"},
                {"start": "1:20", "description": "wave"},
            ],
        }
        _write_text_file(out, analysis, tmp_path / "source.mp4", tmp_path / "compressed.mp4")
        text = out.read_text(encoding="utf-8")
        assert "- plain shot" in text
        assert "- [0:10] sunset" in text
        assert "golden light" in text
        assert "- [1:20] wave" in text


class TestRewriteTextFile:
    def test_rewrites_without_changelog(self, tmp_path: Path):
        out = tmp_path / "001_test.txt"
        analysis = {
            "title": "Updated",
            "summary": "New summary",
            "location": "Tokyo",
            "mood": "calm",
            "suggested_use": "b-roll",
            "source_file": "source.mp4",
        }
        _rewrite_text_file(out, analysis)
        text = out.read_text(encoding="utf-8")
        assert "Updated" in text
        assert "New summary" in text

    def test_rewrites_with_changelog(self, tmp_path: Path):
        out = tmp_path / "001_test.txt"
        analysis = {
            "title": "Fixed",
            "summary": "x",
            "location": "?",
            "mood": "",
            "suggested_use": "",
            "source_file": "s.mp4",
            "_changelog": ["fixed typo", "updated title"],
        }
        _rewrite_text_file(out, analysis)
        text = out.read_text(encoding="utf-8")
        assert "本次 refine 改动" in text
        assert "fixed typo" in text
        assert "updated title" in text

    def test_rewrite_renders_dict_highlights(self, tmp_path: Path):
        out = tmp_path / "001_test.txt"
        analysis = {
            "title": "T",
            "source_file": "s.mp4",
            "highlights": [
                {"start": "0:05", "description": "closeup", "reason": "detail"},
                {"start": "2:00", "description": "pan"},
            ],
        }
        _rewrite_text_file(out, analysis)
        text = out.read_text(encoding="utf-8")
        assert "- [0:05] closeup" in text
        assert "detail" in text
        assert "- [2:00] pan" in text


class TestRewriteScriptMd:
    def test_basic_rewrite(self, tmp_path: Path):
        out = tmp_path / "001_test.md"
        script = {"title": "Script Title", "voiceover": "Hello world", "edit_tip": "cut at 1:00"}
        _rewrite_script_md(out, script)
        text = out.read_text(encoding="utf-8")
        assert "Script Title" in text
        assert "Hello world" in text
        assert "cut at 1:00" in text

    def test_rewrite_with_changelog(self, tmp_path: Path):
        out = tmp_path / "001_test.md"
        script = {"title": "Fixed", "voiceover": "Hello", "edit_tip": "tip", "_changelog": ["corrected date"]}
        _rewrite_script_md(out, script)
        text = out.read_text(encoding="utf-8")
        assert "本次 refine 改动" in text
        assert "corrected date" in text


class TestWriteCsv:
    def test_writes_csv_with_records(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clio.tasks._helpers.resolve_binary", lambda *a: "ffprobe")
        monkeypatch.setattr(
            "clio.tasks._helpers.probe_video_info", lambda *a, **kw: {"duration_sec": 120, "size_mb": 50.0}
        )
        out = tmp_path / "summary.csv"
        src = tmp_path / "s.mp4"
        src.write_bytes(b"x")
        records = [
            ClipRecord(
                index=1,
                stem="001_test",
                source_path=src,
                compressed_path=tmp_path / "c.mp4",
                text_path=tmp_path / "t.txt",
                analysis={"title": "Test", "summary": "A vid", "location": "Paris"},
            ),
        ]
        _write_csv(out, records, _fake_config())
        assert out.exists()
        text = out.read_text(encoding="utf-8-sig")
        assert "001" in text
        assert "Test" in text
        assert "Paris" in text

    def test_atomic_write_leaves_no_tmp(self, tmp_path: Path, monkeypatch):
        """P2-P2-01: summary.csv must be committed via exclusive temp + replace."""
        monkeypatch.setattr("clio.tasks._helpers.resolve_binary", lambda *a: "ffprobe")
        monkeypatch.setattr("clio.tasks._helpers.probe_video_info", lambda *a, **kw: {})
        out = tmp_path / "summary.csv"
        out.write_text("old", encoding="utf-8-sig")
        _write_csv(out, [], _fake_config())
        assert out.read_text(encoding="utf-8-sig").strip() != ""
        assert list(tmp_path.glob(".summary.csv.*.tmp")) == []

    def test_empty_records(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clio.tasks._helpers.resolve_binary", lambda *a: "ffprobe")
        monkeypatch.setattr("clio.tasks._helpers.probe_video_info", lambda *a, **kw: {})
        out = tmp_path / "empty.csv"
        _write_csv(out, [], _fake_config())
        assert out.exists()
        assert out.read_text(encoding="utf-8-sig").strip() != ""


class TestMergeSummaryRecords:
    def test_keeps_existing_rows_not_in_new_batch(self):
        """P2-P2-01: partial success must not drop untouched / failed rows."""
        from clio.tasks._helpers import _merge_summary_records

        existing = [
            ClipRecord(index=1, stem="a", source_path=Path("a.mp4"), analysis={"title": "keep"}),
            ClipRecord(index=2, stem="b", source_path=Path("b.mp4"), analysis={"title": "old"}),
        ]
        new = [
            ClipRecord(index=2, stem="b", source_path=Path("b.mp4"), analysis={"title": "new"}),
        ]
        merged = _merge_summary_records(existing, new)
        assert [r.index for r in merged] == [1, 2]
        assert merged[0].analysis["title"] == "keep"
        assert merged[1].analysis["title"] == "new"


class TestGetVideoInfo:
    def test_uses_meta_when_available(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 2_000_000)
        tgt = tmp_path / "001_src.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=100.0, target_duration=99.0)
        rec = ClipRecord(index=1, stem="001_src", source_path=src, compressed_path=tgt, meta=meta)
        result = _get_video_info(rec, "ffprobe")
        assert result["duration_sec"] == 100.0
        assert result["size_mb"] > 1.0

    def test_uses_disk_meta_when_record_has_no_meta(self, tmp_path: Path):
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 2_000_000)
        tgt = tmp_path / "001_src.mp4"
        tgt.write_bytes(b"\x00" * 500)
        meta = VideoMeta.build(source=src, target=tgt, source_duration=100.0, target_duration=99.0)
        meta.write(tgt)
        rec = ClipRecord(index=1, stem="001_src", source_path=src, compressed_path=tgt)
        result = _get_video_info(rec, "ffprobe")
        assert result["duration_sec"] == 100.0

    def test_falls_back_to_probe(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "clio.tasks._helpers.probe_video_info", lambda *a, **kw: {"duration_sec": 50.0, "size_mb": 10.0}
        )
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 100)
        rec = ClipRecord(index=1, stem="001_src", source_path=src)
        result = _get_video_info(rec, "ffprobe")
        assert result["duration_sec"] == 50.0


class TestMatchesSelected:
    def test_matches_media_identity_stems(self, tmp_path: Path):
        p = tmp_path / "unrelated_name.json"
        p.write_text(
            json.dumps(
                {
                    "media_identity": {
                        "compressed_stem": "001_GL010684",
                        "original_stem": "GL010684",
                    }
                }
            ),
            encoding="utf-8",
        )
        assert _matches_selected_artifact(p, {"001_gl010684"}) is True
        assert _matches_selected_artifact(p, {"gl010684"}) is True
        assert _matches_selected_artifact(p, {"other"}) is False

    def test_matches_source_file_only(self, tmp_path: Path):
        p = tmp_path / "001_street.json"
        p.write_text(json.dumps({"source_file": "GL010684.MP4"}), encoding="utf-8")
        assert _matches_selected_artifact(p, {"gl010684"}) is True

    def test_corrupt_json_false(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("{", encoding="utf-8")
        assert _matches_selected_artifact(p, {"x"}) is False

    def test_stem_direct_match(self, tmp_path: Path):
        p = tmp_path / "001_foo.json"
        p.write_text("{}", encoding="utf-8")
        assert _matches_selected_stem(p, {"001_foo"}) is True

"""Tests for clio/tasks/compress.py — run_compress_all."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from clio.tasks.compress import run_compress_all


def _cfg(tmp_path: Path, **overrides) -> SimpleNamespace:
    cfg = SimpleNamespace(
        paths=SimpleNamespace(
            output_dir=tmp_path / "output",
            ffmpeg="ffmpeg",
            ffprobe="ffprobe",
        ),
        project_dir=tmp_path / "project",
        compressed_dir=tmp_path / "output" / "compressed",
        compress=SimpleNamespace(
            target_size_mb=5,
            max_width=640,
            fps=15,
            codec="libx264",
            remove_audio=True,
            crf=23,
        ),
        analyze=SimpleNamespace(skip_existing=False),
        naming=SimpleNamespace(index_width=3),
    )
    Path(cfg.project_dir).mkdir(parents=True, exist_ok=True)
    cfg.compressed_dir.mkdir(parents=True, exist_ok=True)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _add_video(cfg, name: str, data: bytes = b"\x00" * 1000):
    from clio.tasks._video_loader import load_selected_videos, save_selected_videos

    if cfg.project_dir is None:
        cfg.project_dir = cfg.paths.output_dir.parent / "project"
    cfg.project_dir.mkdir(parents=True, exist_ok=True)
    src = cfg.project_dir / name
    src.write_bytes(data)
    existing = load_selected_videos(cfg.project_dir)
    if src.resolve() not in {p.resolve() for p in existing}:
        existing.append(src)
    save_selected_videos(cfg.project_dir, existing)
    return src


class TestRunCompressAll:
    def test_uses_videos_json_when_project_dir_set(self, monkeypatch, tmp_path: Path):
        """Must use load_selected_videos (not find_videos) when project_dir is set."""
        cfg = _cfg(tmp_path)
        cfg.project_dir = tmp_path / "input"
        proj_dir = tmp_path / "input"
        proj_dir.mkdir(parents=True, exist_ok=True)

        video_dir = tmp_path / "external_sources"
        video_dir.mkdir(parents=True, exist_ok=True)
        video_path = video_dir / "gopro_clip.mp4"
        video_path.write_bytes(b"\x00" * 1000)

        videos_json = proj_dir / "videos.json"
        videos_json.write_text(json.dumps([str(video_path)]), encoding="utf-8")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 300)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        records = run_compress_all(cfg)
        assert len(records) == 1
        assert records[0].stem == "001_gopro_clip"

    def test_compress_single_file(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        _add_video(cfg, "test.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 100)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        records = run_compress_all(cfg)
        assert len(records) == 1
        assert records[0].stem == "001_test"
        assert records[0].compressed_path == cfg.compressed_dir / "001_test.mp4"

    def test_skip_existing(self, monkeypatch, tmp_path: Path):
        """Compress once, then verify second call skips the existing file."""
        cfg = _cfg(tmp_path)
        _add_video(cfg, "test.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        call_count = 0

        def _mock_compress(inp, outp, c, **kw):
            nonlocal call_count
            call_count += 1
            outp.write_bytes(b"\x00" * 60_000)  # > MIN_VALID_SIZE (50KB)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        # First call — compresses
        cfg.analyze.skip_existing = False
        records1 = run_compress_all(cfg)
        assert len(records1) == 1
        assert call_count == 1

        # Second call — should skip since output exists
        cfg.analyze.skip_existing = True
        monkeypatch.setattr("clio.tasks.compress._next_index", lambda *a: 1)
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)
        records2 = run_compress_all(cfg)
        assert len(records2) == 1
        assert call_count == 1  # still 1 — no new compress calls

    def test_recompress_when_source_changes(self, monkeypatch, tmp_path: Path):
        """Changing the source file (mtime/size) must re-compress, not reuse the old output."""
        cfg = _cfg(tmp_path)
        src = _add_video(cfg, "test.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)
        monkeypatch.setattr("clio.tasks.compress._safe_duration", lambda *a, **k: 10.0)

        call_count = [0]

        def _counting_compress(inp, outp, c, **kw):
            call_count[0] += 1
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _counting_compress)
        cfg.analyze.skip_existing = False
        run_compress_all(cfg)
        assert call_count[0] == 1

        # Modify the source file so vmeta is_stale() triggers.
        time.sleep(1.1)
        src.write_bytes(b"\x00" * 2000)
        os.utime(src, (src.stat().st_atime, src.stat().st_mtime + 2))

        cfg.analyze.skip_existing = True
        monkeypatch.setattr("clio.tasks.compress._next_index", lambda *a: 1)
        run_compress_all(cfg)
        assert call_count[0] == 2, "source changed -> must re-compress"

    def test_recompress_when_settings_change(self, monkeypatch, tmp_path: Path):
        """Changing compress settings must re-compress, not reuse the old output."""
        cfg = _cfg(tmp_path)
        _add_video(cfg, "test.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)
        monkeypatch.setattr("clio.tasks.compress._safe_duration", lambda *a, **k: 10.0)

        call_count = [0]

        def _counting_compress(inp, outp, c, **kw):
            call_count[0] += 1
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _counting_compress)
        cfg.analyze.skip_existing = False
        run_compress_all(cfg)
        assert call_count[0] == 1

        # Change a setting the fingerprint tracks.
        cfg.compress.max_width = 480
        cfg.analyze.skip_existing = True
        monkeypatch.setattr("clio.tasks.compress._next_index", lambda *a: 1)
        run_compress_all(cfg)
        assert call_count[0] == 2, "settings changed -> must re-compress"

    def test_same_basename_videos_do_not_collide(self, monkeypatch, tmp_path: Path):
        """Two sources sharing a basename in different dirs must each keep their
        own compressed file when skipping existing."""
        cfg = _cfg(tmp_path)
        dir1 = tmp_path / "cam1"
        dir2 = tmp_path / "cam2"
        dir1.mkdir(parents=True, exist_ok=True)
        dir2.mkdir(parents=True, exist_ok=True)
        src1 = dir1 / "IMG_0001.MP4"
        src2 = dir2 / "IMG_0001.MP4"
        src1.write_bytes(b"\x00" * 1000)
        src2.write_bytes(b"\x00" * 1000)

        from clio.tasks._video_loader import load_selected_videos, save_selected_videos

        proj = cfg.project_dir
        proj.mkdir(parents=True, exist_ok=True)
        existing = load_selected_videos(proj)
        for s in (src1, src2):
            if s.resolve() not in {p.resolve() for p in existing}:
                existing.append(s)
        save_selected_videos(proj, existing)

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)
        monkeypatch.setattr("clio.tasks.compress._safe_duration", lambda *a, **k: 10.0)

        cfg.analyze.skip_existing = False
        records1 = run_compress_all(cfg)
        assert len(records1) == 2
        stems1 = {r.stem for r in records1}
        assert len(stems1) == 2

        # Second run must reuse BOTH distinct outputs (not map both to one).
        cfg.analyze.skip_existing = True
        monkeypatch.setattr("clio.tasks.compress._next_index", lambda *a: 1)
        records2 = run_compress_all(cfg)
        assert len(records2) == 2
        stems2 = {r.stem for r in records2}
        assert stems2 == stems1, f"each same-basename source must reuse its own output: {stems2}"

    def test_skip_existing_does_not_treat_segs_as_whole_file(self, monkeypatch, tmp_path: Path):
        """Leftover _segNN files must not block creating a whole-file compress."""
        cfg = _cfg(tmp_path)
        _add_video(cfg, "test.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        # Create compressed segment files (legacy leftovers)
        seg1 = cfg.compressed_dir / "001_test_seg01.mp4"
        seg1.write_bytes(b"\x00" * 60_000)
        seg2 = cfg.compressed_dir / "002_test_seg02.mp4"
        seg2.write_bytes(b"\x00" * 60_000)

        call_count = 0

        def _mock_compress(inp, outp, c, **kw):
            nonlocal call_count
            call_count += 1
            outp.write_bytes(b"\x00" * 300)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)
        monkeypatch.setattr("clio.tasks.compress._safe_duration", lambda *a, **k: 10.0)

        cfg.analyze.skip_existing = True
        records = run_compress_all(cfg)
        assert call_count == 1
        assert len(records) == 1
        assert records[0].compressed_path is not None
        assert "_seg" not in records[0].compressed_path.name

    def test_single_file_param(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        src = _add_video(cfg, "custom.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 300)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        records = run_compress_all(cfg, single_file=src)
        assert len(records) == 1
        assert records[0].stem == "001_custom"

    def test_files_filter(self, monkeypatch, tmp_path: Path):
        cfg = _cfg(tmp_path)
        for name in ("a.mp4", "b.mp4", "c.mp4"):
            _add_video(cfg, name)

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        compressed = []

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 300)
            compressed.append(outp.name)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        records = run_compress_all(cfg, files=["a"])
        assert len(records) == 1
        assert "a" in records[0].stem

    def test_cancel_unlinks_partial_output(self, monkeypatch, tmp_path: Path):
        """InterruptedError during encode must remove the partial output file."""
        cfg = _cfg(tmp_path)
        _add_video(cfg, "clip.mp4")
        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        written: list[Path] = []

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"partial")
            written.append(outp)
            raise InterruptedError("ffmpeg 被用户取消")

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        with pytest.raises(InterruptedError):
            run_compress_all(cfg)

        assert written
        assert not written[0].exists()

    def test_keyboard_interrupt_does_not_unlink_finished_output(self, monkeypatch, tmp_path: Path):
        """KeyboardInterrupt after a successful write must not delete the good file.

        encode try only catches Exception; BaseException (KeyboardInterrupt/SystemExit)
        must not run the partial-cleanup unlink.
        """
        cfg = _cfg(tmp_path)
        _add_video(cfg, "clip.mp4")
        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"good-content")
            # Simulate interrupt after encode completed (file is valid).
            raise KeyboardInterrupt()

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        with pytest.raises(KeyboardInterrupt):
            run_compress_all(cfg)

        outs = list(cfg.compressed_dir.glob("*.mp4"))
        assert len(outs) == 1
        assert outs[0].read_bytes() == b"good-content"

    def test_recompress_prunes_stale_sibling(self, monkeypatch, tmp_path: Path):
        """When a source change forces a re-compress, the stale sibling and its
        .vmeta must be removed so the compressed dir never accumulates dead files."""
        cfg = _cfg(tmp_path)
        src = _add_video(cfg, "test.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        call_count = [0]

        def _counting_compress(inp, outp, c, **kw):
            call_count[0] += 1
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _counting_compress)
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)
        monkeypatch.setattr("clio.tasks.compress._safe_duration", lambda *a, **k: 10.0)

        cfg.analyze.skip_existing = False
        run_compress_all(cfg)
        assert call_count[0] == 1
        first = cfg.compressed_dir / "001_test.mp4"
        assert first.exists()
        assert first.with_suffix(".vmeta").exists()

        # Modify the source so its recorded meta goes stale.
        time.sleep(1.1)
        src.write_bytes(b"\x00" * 2000)
        os.utime(src, (src.stat().st_atime, src.stat().st_mtime + 2))

        cfg.analyze.skip_existing = True
        run_compress_all(cfg)
        assert call_count[0] == 2, "source changed -> must re-compress"

        assert (cfg.compressed_dir / "002_test.mp4").exists()
        assert not first.exists(), "stale sibling must be pruned after fresh commit"
        assert not first.with_suffix(".vmeta").exists(), "stale sibling vmeta must be pruned"

        # Next run must re-use the single fresh file, not grow the dir again.
        run_compress_all(cfg)
        assert call_count[0] == 2
        mp4s = sorted(p.name for p in cfg.compressed_dir.glob("*.mp4"))
        assert mp4s == ["002_test.mp4"], mp4s

    def test_overwrite_reuses_same_slot_and_prunes_siblings(self, monkeypatch, tmp_path: Path):
        """overwrite:true must re-compress IN PLACE (same index/name), not grow the
        compressed dir with new index files each run, and must not leave stale
        siblings behind."""
        cfg = _cfg(tmp_path)
        _add_video(cfg, "test.mp4")

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)

        call_count = [0]

        def _mock_compress(inp, outp, c, **kw):
            call_count[0] += 1
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        cfg.analyze.skip_existing = True
        run_compress_all(cfg)
        assert call_count[0] == 1
        first = cfg.compressed_dir / "001_test.mp4"
        assert first.exists()
        assert first.with_suffix(".vmeta").exists()

        run_compress_all(cfg, overwrite=True)
        assert call_count[0] == 2, "overwrite must force re-compress"

        single = sorted(p.name for p in cfg.compressed_dir.glob("*.mp4"))
        assert single == ["001_test.mp4"], f"must overwrite in place, got {single}"
        assert (cfg.compressed_dir / "001_test.vmeta").exists()

    def test_overwrite_only_prunes_its_own_source(self, monkeypatch, tmp_path: Path):
        """overwrite of one same-basename source must not delete the compressed
        output belonging to a different source sharing the basename."""
        cfg = _cfg(tmp_path)
        dir1 = tmp_path / "cam1"
        dir2 = tmp_path / "cam2"
        dir1.mkdir(parents=True, exist_ok=True)
        dir2.mkdir(parents=True, exist_ok=True)
        src1 = dir1 / "IMG_0001.MP4"
        src2 = dir2 / "IMG_0001.MP4"
        for s in (src1, src2):
            s.write_bytes(b"\x00" * 1000)
        from clio.tasks._video_loader import load_selected_videos, save_selected_videos

        cfg.project_dir.mkdir(parents=True, exist_ok=True)
        existing = load_selected_videos(cfg.project_dir)
        for s in (src1, src2):
            if s.resolve() not in {p.resolve() for p in existing}:
                existing.append(s)
        save_selected_videos(cfg.project_dir, existing)

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")
        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)

        cfg.analyze.skip_existing = True
        run_compress_all(cfg)
        mp4s = sorted(p.name for p in cfg.compressed_dir.glob("*.mp4"))
        assert len(mp4s) == 2, mp4s

        # Overwrite a single stale source: the OTHER same-basename source's
        # compressed output must survive.
        run_compress_all(cfg, single_file=src1, overwrite=True)
        still = sorted(p.name for p in cfg.compressed_dir.glob("*.mp4"))
        assert len(still) == 2, f"other source output must survive overwrite: {still}"

    def test_find_reusable_prefers_fresh_over_stale(self, monkeypatch, tmp_path: Path):
        """A stale candidate scanned before a fresh one sharing the key must not
        win: _find_reusable must return the fresh output regardless of order."""
        from clio.tasks.compress import _find_reusable
        from clio.vmeta import VideoMeta

        cfg = _cfg(tmp_path)
        src = tmp_path / "cam" / "clip.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"\x00" * 1000)

        stale_path = cfg.compressed_dir / "001_clip.mp4"
        fresh_path = cfg.compressed_dir / "002_clip.mp4"
        for p in (stale_path, fresh_path):
            p.write_bytes(b"\x00" * 60_000)

        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10.0)

        stale_meta = VideoMeta.build(src, stale_path, 10.0, 10.0)
        stale_meta.source_modifyTime = stale_meta.source_modifyTime - 10
        stale_meta.source_size = stale_meta.source_size - 1
        fresh_meta = VideoMeta.build(src, fresh_path, 10.0, 10.0)

        existing = {f"src:{src.resolve()}": [(1, stale_path, stale_meta), (2, fresh_path, fresh_meta)]}
        idx, path, meta, reason = _find_reusable(src, existing, cfg, "ffprobe")
        assert path == fresh_path
        assert idx == 2
        assert meta == fresh_meta
        assert reason is None

    def test_scan_probe_failure_keeps_file_until_fresh_commit(self, monkeypatch, tmp_path: Path):
        """A probe timeout must not delete the existing compressed file; it is
        only removed after a fresh replacement commits (GAP-P1-07)."""
        from clio.vmeta import VideoMeta

        cfg = _cfg(tmp_path)
        src = _add_video(cfg, "clip.mp4")
        cfg.analyze.skip_existing = True

        old = cfg.compressed_dir / "001_clip.mp4"
        old.write_bytes(b"\x00" * 60_000)
        VideoMeta.build(src, old, 10.0, 10.0).write(old)

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _mock_compress(inp, outp, c, **kw):
            outp.write_bytes(b"\x00" * 60_000)
            return outp

        monkeypatch.setattr("clio.tasks.compress.compress_video", _mock_compress)
        monkeypatch.setattr("clio.tasks.compress._safe_duration", lambda *a, **k: 10.0)

        def _probe(path, *a, **kw):
            if Path(path).name == old.name:
                raise RuntimeError("ffprobe 超时")
            return 10.0

        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", _probe)

        records = run_compress_all(cfg)

        fresh = cfg.compressed_dir / "002_clip.mp4"
        assert fresh.exists()
        assert not old.exists(), "unverifiable old file pruned after fresh commit"
        assert not old.with_suffix(".vmeta").exists()
        assert any(r.compressed_path == fresh for r in records)

    def test_scan_probe_failure_keeps_file_when_recompress_fails(self, monkeypatch, tmp_path: Path):
        """If the replacement encode fails, the unverifiable old file must remain
        so the last artifact is never lost (GAP-P1-07)."""
        from clio.vmeta import VideoMeta

        cfg = _cfg(tmp_path)
        src = _add_video(cfg, "clip.mp4")
        cfg.analyze.skip_existing = True

        old = cfg.compressed_dir / "001_clip.mp4"
        old.write_bytes(b"\x00" * 60_000)
        VideoMeta.build(src, old, 10.0, 10.0).write(old)

        monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffmpeg")

        def _boom(inp, outp, c, **kw):
            raise RuntimeError("磁盘满")

        monkeypatch.setattr("clio.tasks.compress.compress_video", _boom)

        def _probe(path, *a, **kw):
            raise RuntimeError("ffprobe 超时")

        monkeypatch.setattr("clio.tasks.compress.get_duration_sec", _probe)

        with pytest.raises(RuntimeError, match="磁盘满"):
            run_compress_all(cfg)

        assert old.exists(), "old artifact retained when replacement fails"
        assert old.with_suffix(".vmeta").exists()

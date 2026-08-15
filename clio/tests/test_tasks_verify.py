"""Tests for clio/tasks/verify.py — compressed integrity verification."""

from __future__ import annotations

import os
import time
from pathlib import Path

from clio.config import AppConfig
from clio.config.models import AnalyzeConfig, GlobalConfig, ProjectConfig, ProjectPathsConfig
from clio.tasks.verify import run_verify
from clio.vmeta import SegmentEntry, VideoIndex, VideoMeta


def _mk_vindex(compressed_dir: Path, *, segments: list[SegmentEntry], source: Path) -> VideoIndex:
    index = VideoIndex.build(source=source, source_duration=60.0, segments=segments)
    index.write(compressed_dir)
    return index


def run_verify_cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        global_cfg=GlobalConfig(),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=tmp_path),
            analyze=AnalyzeConfig(compressed_subdir="compressed"),
        ),
    )


def _seg(filename: str = "001_src.mp4") -> SegmentEntry:
    return SegmentEntry(
        index="001",
        filename=filename,
        offset_sec=0.0,
        duration_sec=60.0,
        segment_number=1,
        total_segments=1,
    )


def _write_ok_segment(comp: Path, src: Path, filename: str = "001_src.mp4") -> Path:
    seg = comp / filename
    seg.write_bytes(b"\x00" * 100)
    VideoMeta.build(src, seg, 60.0, 60.0).write(seg)
    return seg


class TestVerifySafety:
    def test_declared_missing_segment_reports_failure(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        seg = SegmentEntry(
            index="001", filename="001_src.mp4", offset_sec=0.0, duration_sec=60.0, segment_number=1, total_segments=1
        )
        _mk_vindex(comp, segments=[seg], source=src)
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1

    def test_no_segments_is_not_silent_success(self, tmp_path: Path):
        source = tmp_path / "src.mp4"
        source.write_bytes(b"\x00" * 1000)
        comp = tmp_path / "compressed"
        comp.mkdir()
        _mk_vindex(comp, segments=[], source=source)
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1

    def test_missing_compressed_dir_fails(self, tmp_path: Path):
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1

    def test_no_vindex_returns_zero(self, tmp_path: Path):
        comp = tmp_path / "compressed"
        comp.mkdir()
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 0


class TestVerifyBranches:
    def _setup(self, tmp_path: Path) -> tuple[Path, Path]:
        comp = tmp_path / "compressed"
        comp.mkdir()
        src = tmp_path / "src.mp4"
        src.write_bytes(b"\x00" * 1000)
        return comp, src

    def test_corrupt_vindex_counts_stale(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        (comp / "src.vindex").write_text("{corrupt", encoding="utf-8")
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        assert "VINDEX_READ_ERROR" in capsys.readouterr().out

    def test_missing_source_reports_missing(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg()], source=src)
        src.unlink()
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        assert "SOURCE_MISSING" in capsys.readouterr().out

    def test_stale_source_reports_stale(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg()], source=src)
        time.sleep(0.05)
        os.utime(src, (time.time() + 10, time.time() + 10))
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        assert "STALE" in capsys.readouterr().out

    def test_segment_missing_reports_missing(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg()], source=src)
        _write_ok_segment(comp, src)
        (comp / "001_src.vmeta").unlink()
        (comp / "001_src.mp4").unlink()
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        assert "SEGMENT_MISSING" in capsys.readouterr().out

    def test_all_ok_returns_zero(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg()], source=src)
        _write_ok_segment(comp, src)
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 0
        assert "OK" in capsys.readouterr().out

    def test_missing_vmeta_counts_stale(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg()], source=src)
        (comp / "001_src.mp4").write_bytes(b"\x00" * 100)
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        assert "VMETA_MISSING" in capsys.readouterr().out

    def test_hash_mismatch_reports_hash_fail(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg()], source=src)
        seg = _write_ok_segment(comp, src)
        meta = VideoMeta.read(seg)
        assert meta is not None
        meta.verify = "deadbeef"
        meta.write(seg)
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        assert "HASH_MISMATCH" in capsys.readouterr().out

    def test_stale_target_reports_vmeta_stale(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg()], source=src)
        seg = _write_ok_segment(comp, src)
        meta = VideoMeta.read(seg)
        assert meta is not None
        meta.verify = ""
        meta.write(seg)
        time.sleep(0.05)
        os.utime(seg, (time.time() + 10, time.time() + 10))
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        assert "VMETA_STALE" in capsys.readouterr().out

    def test_mixed_ok_and_missing_segment(self, tmp_path: Path, capsys):
        comp, src = self._setup(tmp_path)
        _mk_vindex(comp, segments=[_seg("001_src.mp4"), _seg("002_src.mp4")], source=src)
        _write_ok_segment(comp, src, "001_src.mp4")
        cfg = run_verify_cfg(tmp_path)
        assert run_verify(cfg) == 1
        out = capsys.readouterr().out
        assert "SEGMENT_MISSING" in out
        assert "OK" in out

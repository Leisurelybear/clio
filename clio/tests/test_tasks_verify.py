"""Tests for clio/tasks/verify.py — compressed integrity verification."""

from __future__ import annotations

from pathlib import Path

from clio.config import AppConfig
from clio.config.models import AnalyzeConfig, GlobalConfig, ProjectConfig, ProjectPathsConfig
from clio.tasks.verify import run_verify
from clio.vmeta import SegmentEntry, VideoIndex


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

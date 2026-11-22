"""Regression test for B-03b: reindex writes one .vindex per same-stem original."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from clio.config import AppConfig
from clio.config.models import (
    AnalyzeConfig,
    GlobalConfig,
    GlobalPathsConfig,
    ProjectConfig,
    ProjectPathsConfig,
)
from clio.tasks.reindex import auto_reindex_if_needed, run_reindex
from clio.vmeta import VideoIndex, VideoMeta


def _two_sources(root: Path) -> tuple[Path, Path]:
    src_a = root / "a" / "GL010683.mp4"
    src_b = root / "b" / "GL010683.mp4"
    for src in (src_a, src_b):
        src.parent.mkdir(parents=True)
        src.write_text(f"source {src.parent.name}")
    return src_a, src_b


class TestReindexSameStem:
    def test_both_sources_indexed(self, tmp_path: Path) -> None:
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        src_a, src_b = _two_sources(input_dir)
        comp = tmp_path / "compressed"
        comp.mkdir()
        cfg = AppConfig(
            global_cfg=GlobalConfig(paths=GlobalPathsConfig(ffprobe="ffprobe")),
            project_cfg=ProjectConfig(
                paths=ProjectPathsConfig(output_dir=tmp_path),
                analyze=AnalyzeConfig(compressed_subdir=comp.name),
            ),
            project_dir=input_dir,
        )
        comp_a = comp / "001_GL010683.mp4"
        comp_b = comp / "002_GL010683.mp4"
        comp_a.write_text("comp a")
        comp_b.write_text("comp b")
        VideoMeta.build(source=src_a, target=comp_a, source_duration=10.0, target_duration=10.0).write(comp_a)
        VideoMeta.build(source=src_b, target=comp_b, source_duration=20.0, target_duration=20.0).write(comp_b)

        with (
            patch("clio.tasks.reindex.get_duration_sec", return_value=10.0),
            patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"),
        ):
            assert run_reindex(cfg) == 2
            # Startup check sees both sources as indexed and does not rebuild again.
            assert auto_reindex_if_needed(cfg, "ffprobe") is False

        indexes = VideoIndex.read_all("GL010683", comp)
        by_source = {Path(v.source_path): [s.filename for s in v.segments] for v in indexes}
        assert by_source == {src_a.resolve(): [comp_a.name], src_b.resolve(): [comp_b.name]}

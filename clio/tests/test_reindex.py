"""Tests for clio.tasks.reindex."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.config import AppConfig
from clio.config.models import (
    AnalyzeConfig,
    GlobalConfig,
    GlobalPathsConfig,
    ProjectConfig,
    ProjectPathsConfig,
)
from clio.tasks.reindex import (
    _find_original_for_stem,
    auto_reindex_if_needed,
    run_reindex,
)
from clio.vmeta import SplitInfo, VideoIndex


@pytest.fixture
def compressed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "compressed"
    d.mkdir()
    return d


@pytest.fixture
def input_dir(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    return d


@pytest.fixture
def config(compressed_dir: Path, input_dir: Path) -> AppConfig:
    return AppConfig(
        global_cfg=GlobalConfig(
            paths=GlobalPathsConfig(ffprobe="ffprobe"),
        ),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=compressed_dir.parent),
            analyze=AnalyzeConfig(compressed_subdir=compressed_dir.name),
        ),
        project_dir=input_dir,
    )


class TestFindOriginalForStem:
    def test_exact_match(self, input_dir: Path) -> None:
        f = input_dir / "GL010683.mp4"
        f.write_text("fake")
        assert _find_original_for_stem("GL010683", input_dir) == f

    def test_recursive_match(self, input_dir: Path) -> None:
        sub = input_dir / "sub"
        sub.mkdir()
        f = sub / "GL010683.mp4"
        f.write_text("fake")
        assert _find_original_for_stem("GL010683", input_dir) == f

    def test_no_match(self, input_dir: Path) -> None:
        assert _find_original_for_stem("NONEXISTENT", input_dir) is None


class TestAutoReindexIfNeeded:
    def test_missing_compressed_dir(self, tmp_path: Path) -> None:
        cfg = AppConfig(
            global_cfg=GlobalConfig(),
            project_cfg=ProjectConfig(
                paths=ProjectPathsConfig(output_dir=tmp_path / "nonexistent"),
            ),
        )
        assert auto_reindex_if_needed(cfg) is False

    def test_all_indexed(self, config: AppConfig, compressed_dir: Path) -> None:
        for name in ["001_GL010683.mp4", "002_GL010683.mp4", "001_GL010695.mp4"]:
            (compressed_dir / name).write_text("fake")

        with patch("clio.tasks.reindex.VideoIndex.read", return_value=MagicMock()) as mock_read:
            result = auto_reindex_if_needed(config)

        assert result is False
        assert mock_read.call_count >= 2


class TestRunReindex:
    def test_missing_dir(self, config: AppConfig, capsys: pytest.CaptureFixture[str]) -> None:
        config.project_cfg.paths.output_dir = Path("/nonexistent_path_xyz")
        with patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"):
            result = run_reindex(config)
        assert result == 0
        captured = capsys.readouterr()
        assert "compressed_dir 不存在" in captured.out

    def test_writes_vindex_and_vmeta(self, config: AppConfig, compressed_dir: Path, input_dir: Path) -> None:
        for name in ["001_GL010683.mp4", "002_GL010683.mp4", "001_GL010695.mp4"]:
            (compressed_dir / name).write_text("fake")
        for name in ["GL010683.mp4", "GL010695.mp4"]:
            (input_dir / name).write_text("fake")

        with (
            patch("clio.tasks.reindex.VideoMeta.read", return_value=None),
            patch("clio.tasks.reindex.VideoMeta.build") as mock_build,
            patch("clio.tasks.reindex.get_duration_sec", return_value=60.0),
            patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"),
        ):
            mock_meta = MagicMock()
            mock_meta.target_duration_sec = 30.0
            mock_meta.split_info = None
            mock_build.return_value = mock_meta

            result = run_reindex(config)

        assert result == 2
        assert mock_build.call_count == 3
        assert (compressed_dir / "GL010683.vindex").exists()
        assert (compressed_dir / "GL010695.vindex").exists()

    def test_fallback_to_find_original(self, config: AppConfig, compressed_dir: Path, input_dir: Path) -> None:
        (compressed_dir / "001_GL010683.mp4").write_text("fake")
        (input_dir / "GL010683.mp4").write_text("fake")

        with (
            patch("clio.tasks.reindex.VideoMeta.read", return_value=None),
            patch("clio.tasks.reindex.VideoMeta.build") as mock_build,
            patch("clio.tasks.reindex._find_original_for_stem") as mock_find,
            patch("clio.tasks.reindex.get_duration_sec", return_value=60.0),
            patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"),
        ):
            mock_meta = MagicMock()
            mock_meta.target_duration_sec = 30.0
            mock_meta.split_info = None
            mock_build.return_value = mock_meta
            mock_find.return_value = input_dir / "GL010683.mp4"

            result = run_reindex(config)

        assert result == 1
        mock_find.assert_called_once_with("GL010683", config.project_dir)

    def test_whole_files_do_not_get_average_offsets(
        self, config: AppConfig, compressed_dir: Path, input_dir: Path
    ) -> None:
        """Same-stem whole-file clips must not be spread across a fabricated source timeline."""
        for name in ["001_GL010683.mp4", "002_GL010683.mp4"]:
            (compressed_dir / name).write_text("fake")
        (input_dir / "GL010683.mp4").write_text("fake")

        with (
            patch("clio.tasks.reindex.VideoMeta.read", return_value=None),
            patch("clio.tasks.reindex.VideoMeta.build") as mock_build,
            patch("clio.tasks.reindex.get_duration_sec", return_value=60.0),
            patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"),
        ):
            mock_meta = MagicMock()
            mock_meta.target_duration_sec = 30.0
            mock_meta.split_info = None
            mock_build.return_value = mock_meta

            run_reindex(config)

        index = VideoIndex.read("GL010683", compressed_dir)
        assert index is not None
        assert [s.offset_sec for s in index.segments] == [0.0, 0.0]
        assert [s.segment_number for s in index.segments] == [1, 1]

    def test_stale_split_meta_not_trusted(self, config: AppConfig, compressed_dir: Path, input_dir: Path) -> None:
        """A stale .vmeta declaring split offsets must be rebuilt, not trusted."""
        (compressed_dir / "001_GL010683.mp4").write_text("fake0")
        src = input_dir / "GL010683.mp4"
        src.write_text("fake")

        stale = MagicMock()
        stale.source_path_obj.return_value = src
        stale.is_stale.return_value = True  # source/target changed since it was stamped
        stale.target_duration_sec = 30.0
        stale.split_info = SplitInfo("GL010683", 1, 2, 0.0, 30.0)

        with (
            patch("clio.tasks.reindex.VideoMeta.read", return_value=stale),
            patch("clio.tasks.reindex.VideoMeta.build") as mock_build,
            patch("clio.tasks.reindex.get_duration_sec", return_value=60.0),
            patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"),
        ):
            mock_meta = MagicMock()
            mock_meta.target_duration_sec = 30.0
            mock_meta.split_info = None
            mock_build.return_value = mock_meta

            run_reindex(config)

        # Stale meta was replaced (build called) and its offsets were NOT reused.
        assert mock_build.call_count == 1
        index = VideoIndex.read("GL010683", compressed_dir)
        assert index is not None
        assert index.segments[0].offset_sec == 0.0

    def test_fresh_split_meta_offsets_kept(self, config: AppConfig, compressed_dir: Path, input_dir: Path) -> None:
        """A fresh .vmeta with split offsets is kept verbatim (no averaging)."""
        (compressed_dir / "001_GL010683.mp4").write_text("fake0")
        src = input_dir / "GL010683.mp4"
        src.write_text("fake")

        fresh = MagicMock()
        fresh.source_path_obj.return_value = src
        fresh.is_stale.return_value = False
        fresh.target_duration_sec = 10.0
        fresh.split_info = SplitInfo("GL010683", 1, 2, 10.0, 10.0)

        with (
            patch("clio.tasks.reindex.VideoMeta.read", return_value=fresh),
            patch("clio.tasks.reindex.VideoMeta.build") as mock_build,
            patch("clio.tasks.reindex.get_duration_sec", return_value=60.0),
            patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"),
        ):
            run_reindex(config)

        assert mock_build.call_count == 0
        index = VideoIndex.read("GL010683", compressed_dir)
        assert index is not None
        assert index.segments[0].offset_sec == 10.0

    def test_strips_seg_suffix(self, config: AppConfig, compressed_dir: Path, input_dir: Path) -> None:
        (compressed_dir / "001_GL010683_seg01.mp4").write_text("fake")
        (compressed_dir / "002_GL010683_seg02.mp4").write_text("fake")
        (input_dir / "GL010683.mp4").write_text("fake")

        with (
            patch("clio.tasks.reindex.VideoMeta.read", return_value=None),
            patch("clio.tasks.reindex.VideoMeta.build") as mock_build,
            patch("clio.tasks.reindex.get_duration_sec", return_value=60.0),
            patch("clio.tasks.reindex.resolve_binary", return_value="ffprobe"),
        ):
            mock_meta = MagicMock()
            mock_meta.target_duration_sec = 30.0
            mock_meta.split_info = None
            mock_build.return_value = mock_meta

            result = run_reindex(config)

        assert result == 1
        assert (compressed_dir / "GL010683.vindex").exists()

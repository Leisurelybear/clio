from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.config import AppConfig
from clio.config.models import (
    AnalyzeConfig,
    GlobalConfig,
    GlobalPathsConfig,
    NamingConfig,
    PlanConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ScriptConfig,
)


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    texts = tmp_path / "texts"
    compressed = tmp_path / "compressed"
    texts.mkdir()
    compressed.mkdir()
    project_cfg = ProjectConfig(
        paths=ProjectPathsConfig(
            output_dir=tmp_path,
        ),
        analyze=AnalyzeConfig(
            skip_existing=True,
            texts_subdir="texts",
            compressed_subdir="compressed",
        ),
        script=ScriptConfig(scripts_subdir="scripts"),
        plan=PlanConfig(plans_subdir="plans"),
    )
    global_cfg = GlobalConfig(
        naming=NamingConfig(index_width=3),
        paths=GlobalPathsConfig(
            ffmpeg="ffmpeg_path",
            ffprobe="",
        ),
    )
    return AppConfig(global_cfg=global_cfg, project_cfg=project_cfg)


class TestDrawtextHelpers:
    def test_escape_drawtext_path_escapes_drive_colon(self, tmp_path):
        from clio.tasks.label import escape_drawtext_path

        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        escaped = escape_drawtext_path(font)
        assert ":" not in escaped or r"\:" in escaped
        assert escaped.endswith("arial.ttf")
        # Windows drive path becomes C\:/... form
        if len(str(font.resolve().as_posix())) >= 2 and font.resolve().as_posix()[1] == ":":
            assert escaped.startswith(font.resolve().as_posix()[0] + r"\:")

    def test_build_label_drawtext_vf_includes_fontfile(self, tmp_path):
        from clio.tasks.label import build_label_drawtext_vf

        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        vf = build_label_drawtext_vf("001", font)
        assert vf.startswith("drawtext=fontfile='")
        assert "text='001'" in vf
        assert "fontsize=36" in vf
        assert "arial.ttf" in vf


class TestRunLabelVideos:
    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_skips_if_no_compressed_file(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001_test.json").write_text('{"index": 1}')
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg)
        mock_ffmpeg.assert_not_called()
        mock_font.assert_not_called()

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_labels_video(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001_test.json").write_text('{"index": 1}')
        compressed = cfg.compressed_dir / "001_original.mp4"
        compressed.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg)
        labeled_dir = cfg.paths.output_dir / "labeled"
        out = labeled_dir / "001_test_labeled.mp4"
        assert out.name == "001_test_labeled.mp4"
        mock_ffmpeg.assert_called_once()

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_missing_font_warns_and_skips_all_without_failing(self, mock_resolve, mock_ffmpeg, mock_font, cfg, capsys):
        mock_font.side_effect = FileNotFoundError("font unavailable")
        for idx in (1, 2):
            (cfg.texts_dir / f"{idx:03d}_clip.json").write_text(json.dumps({"index": idx}), encoding="utf-8")
            (cfg.compressed_dir / f"{idx:03d}_clip.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"
        tracker = MagicMock()

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg, tracker=tracker)

        mock_font.assert_called_once()
        mock_ffmpeg.assert_not_called()
        assert "font unavailable" in capsys.readouterr().out
        assert tracker.log.call_count == 1
        assert tracker.next.call_count == 2

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_skip_existing(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001.json").write_text('{"index": 1}')
        compressed = cfg.compressed_dir / "001_original.mp4"
        compressed.write_bytes(b"\x00")
        labeled_dir = cfg.paths.output_dir / "labeled"
        labeled_dir.mkdir()
        out = labeled_dir / "001_labeled.mp4"
        out.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg)
        mock_ffmpeg.assert_not_called()

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_uses_index_from_data(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001_test.json").write_text('{"index": 42}')
        compressed = cfg.compressed_dir / "042_src.mp4"
        compressed.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg)
        labeled_dir = cfg.paths.output_dir / "labeled"
        out = labeled_dir / "001_test_labeled.mp4"
        assert out.name == "001_test_labeled.mp4"
        args = mock_ffmpeg.call_args.args[0]
        assert any("042_src.mp4" in str(a) for a in args)

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_passes_drawtext_filter(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001_test.json").write_text('{"index": 1}')
        compressed = cfg.compressed_dir / "001_src.mp4"
        compressed.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg)
        args = mock_ffmpeg.call_args.args[0]
        vf_idx = args.index("-vf") + 1 if "-vf" in args else -1
        assert vf_idx > 0
        vf = args[vf_idx]
        assert "drawtext" in vf
        assert "001" in vf
        assert "fontfile=" in vf
        assert "arial.ttf" in vf

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_tracker_next_called(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001.json").write_text('{"index": 1}')
        compressed = cfg.compressed_dir / "001_src.mp4"
        compressed.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"
        tracker = MagicMock()

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg, tracker=tracker)
        tracker.update.assert_called_once()
        tracker.next.assert_called_once()
        tracker.log.assert_called_once()

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_files_filter(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001_A.json").write_text('{"index": 1}')
        (cfg.texts_dir / "002_B.json").write_text('{"index": 2}')
        (cfg.texts_dir / "003_C.json").write_text('{"index": 3}')
        for idx in (1, 2, 3):
            f = cfg.compressed_dir / f"00{idx}_src.mp4"
            f.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg, files=["002_B"])
        assert mock_ffmpeg.call_count == 1
        args = mock_ffmpeg.call_args.args[0]
        assert any("002_src.mp4" in str(a) for a in args)

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_overwrite_flag(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001.json").write_text('{"index": 1}')
        compressed = cfg.compressed_dir / "001_src.mp4"
        compressed.write_bytes(b"\x00")
        labeled_dir = cfg.paths.output_dir / "labeled"
        labeled_dir.mkdir()
        out = labeled_dir / "001_labeled.mp4"
        out.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.label import run_label_videos

        run_label_videos(cfg, overwrite=True)
        mock_ffmpeg.assert_called_once()

    @patch("clio.tasks.label.resolve_drawtext_font")
    @patch("clio.tasks.label.run_ffmpeg")
    @patch("clio.tasks.label.resolve_binary")
    def test_cancel_unlinks_partial_output(self, mock_resolve, mock_ffmpeg, mock_font, cfg, tmp_path):
        """InterruptedError during label encode must remove partial labeled mp4."""
        font = tmp_path / "arial.ttf"
        font.write_bytes(b"\x00")
        mock_font.return_value = font
        (cfg.texts_dir / "001_clip.json").write_text(
            json.dumps({"index": 1, "source_file": "clip.mp4"}),
            encoding="utf-8",
        )
        (cfg.compressed_dir / "001_clip.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        def _ffmpeg(args, *a, **kw):
            # last arg is output path with -y
            out = Path(args[-1])
            out.write_bytes(b"partial")
            raise InterruptedError("ffmpeg 被用户取消")

        mock_ffmpeg.side_effect = _ffmpeg

        from clio.tasks.label import run_label_videos

        with pytest.raises(InterruptedError):
            run_label_videos(cfg)

        labeled = list((cfg.paths.output_dir / "labeled").glob("*_labeled.mp4"))
        assert labeled == []

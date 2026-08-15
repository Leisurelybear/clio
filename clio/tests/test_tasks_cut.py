from __future__ import annotations

import json
import threading
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
from clio.tasks._video_loader import save_selected_videos
from clio.vmeta import VideoMeta


@pytest.fixture
def cfg(tmp_path) -> AppConfig:
    (tmp_path / "plans").mkdir()
    (tmp_path / "texts").mkdir()
    (tmp_path / "compressed").mkdir()
    (tmp_path / "videos").mkdir()
    return AppConfig(
        global_cfg=GlobalConfig(
            paths=GlobalPathsConfig(ffmpeg="ffmpeg", ffprobe="ffprobe"),
            naming=NamingConfig(index_width=3),
        ),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=tmp_path),
            analyze=AnalyzeConfig(
                skip_existing=True,
                texts_subdir="texts",
                compressed_subdir="compressed",
            ),
            script=ScriptConfig(scripts_subdir="scripts"),
            plan=PlanConfig(plans_subdir="plans"),
        ),
        project_dir=tmp_path / "videos",
    )


def _write_plan(cfg: AppConfig, day_label: str = "day1", seq: list | None = None):
    if seq is None:
        seq = [
            {"index": "001", "title": "Intro", "use_timeline": "00:00-00:30"},
            {"index": "002", "title": "Main", "use_timeline": "01:00-02:00"},
        ]
    plan = {"day_title": "Day 1", "theme": "Paris", "total_estimated_sec": 120, "sequence": seq}
    plan_path = cfg.plans_dir / f"{day_label}_plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")


class TestOrphanedCutBackups:
    def test_list_and_restore_when_target_absent(self, tmp_path):
        from clio.tasks.cut import (
            CUT_BAK_SUFFIX,
            list_orphaned_cut_backups,
            restore_orphaned_cut_backups,
        )

        day = tmp_path / "cuts" / "day1"
        day.mkdir(parents=True)
        target = day / "clip.mp4"
        bak = day / f"clip.mp4{CUT_BAK_SUFFIX}"
        # only backup of old remains; new file never completed
        bak.write_bytes(b"oldgood")

        items = list_orphaned_cut_backups(tmp_path)
        assert len(items) == 1
        assert items[0]["name"] == "clip.mp4"
        assert items[0]["day"] == "day1"
        assert items[0]["conflict"] == "false"

        result = restore_orphaned_cut_backups(tmp_path)
        assert result["count"] == 1
        assert target.read_bytes() == b"oldgood"
        assert not bak.exists()
        assert list_orphaned_cut_backups(tmp_path) == []

    def test_list_and_restore_coexist_requires_decision(self, tmp_path):
        from clio.tasks.cut import (
            CUT_BAK_SUFFIX,
            list_orphaned_cut_backups,
            restore_orphaned_cut_backups,
        )

        day = tmp_path / "cuts" / "day1"
        day.mkdir(parents=True)
        target = day / "clip.mp4"
        bak = day / f"clip.mp4{CUT_BAK_SUFFIX}"
        # completed new cut + leftover backup of old
        target.write_bytes(b"newcut")
        bak.write_bytes(b"oldgood")

        items = list_orphaned_cut_backups(tmp_path)
        assert items[0]["conflict"] == "true"

        # no explicit decision -> conflict reported, nothing changed
        result = restore_orphaned_cut_backups(tmp_path)
        assert result["count"] == 0
        assert len(result["conflicts"]) == 1
        assert target.read_bytes() == b"newcut"
        assert bak.exists()

        # keep new target -> backup deleted
        result = restore_orphaned_cut_backups(tmp_path, keep_target=True)
        assert result["count"] == 1
        assert result["restored"][0]["kept"] == "target"
        assert target.read_bytes() == b"newcut"
        assert not bak.exists()

    def test_restore_when_only_bak(self, tmp_path):
        from clio.tasks.cut import CUT_BAK_SUFFIX, restore_orphaned_cut_backup

        day = tmp_path / "cuts" / "day1"
        day.mkdir(parents=True)
        bak = day / f"only.mp4{CUT_BAK_SUFFIX}"
        bak.write_bytes(b"old")
        restore_orphaned_cut_backup(bak)
        assert (day / "only.mp4").read_bytes() == b"old"
        assert not bak.exists()

    def test_restore_explicit_restore_old_when_coexist(self, tmp_path):
        from clio.tasks.cut import CUT_BAK_SUFFIX, restore_orphaned_cut_backup

        day = tmp_path / "cuts" / "day2"
        day.mkdir(parents=True)
        target = day / "clip.mp4"
        bak = day / f"clip.mp4{CUT_BAK_SUFFIX}"
        target.write_bytes(b"newcut")
        bak.write_bytes(b"oldgood")

        restore_orphaned_cut_backup(bak, keep_target=False)
        assert target.read_bytes() == b"oldgood"
        assert not bak.exists()


class TestReplaceFileSafely:
    def test_writes_new_file(self, tmp_path):
        from clio.tasks.cut import replace_file_safely

        dest = tmp_path / "out.mp4"

        def write(p):
            p.write_bytes(b"new")

        replace_file_safely(dest, write)
        assert dest.read_bytes() == b"new"
        assert not (tmp_path / "out.mp4.clio_bak").exists()

    def test_restores_backup_on_write_failure(self, tmp_path):
        from clio.tasks.cut import replace_file_safely

        dest = tmp_path / "out.mp4"
        dest.write_bytes(b"old")

        def boom(p):
            raise RuntimeError("ffmpeg failed")

        with pytest.raises(RuntimeError, match="ffmpeg failed"):
            replace_file_safely(dest, boom)
        assert dest.read_bytes() == b"old"
        assert not (tmp_path / "out.mp4.clio_bak").exists()

    def test_deletes_backup_after_success(self, tmp_path):
        from clio.tasks.cut import replace_file_safely

        dest = tmp_path / "out.mp4"
        dest.write_bytes(b"old")

        def write(p):
            p.write_bytes(b"new")

        replace_file_safely(dest, write)
        assert dest.read_bytes() == b"new"
        assert not (tmp_path / "out.mp4.clio_bak").exists()

    def test_unlinks_partial_without_bak(self, tmp_path):
        from clio.tasks.cut import replace_file_safely

        dest = tmp_path / "out.mp4"

        def boom(p):
            p.write_bytes(b"partial")
            raise InterruptedError("cancel")

        with pytest.raises(InterruptedError):
            replace_file_safely(dest, boom)
        assert not dest.exists()


class TestListExistingCutVideos:
    def test_lists_video_basenames(self, tmp_path):
        from clio.tasks.cut import list_existing_cut_videos

        (tmp_path / "a.mp4").write_bytes(b"")
        (tmp_path / "b.json").write_bytes(b"")
        (tmp_path / "c.mov").write_bytes(b"")
        assert list_existing_cut_videos(tmp_path) == ["a.mp4", "c.mov"]

    def test_empty_or_missing_dir(self, tmp_path):
        from clio.tasks.cut import list_existing_cut_videos

        assert list_existing_cut_videos(tmp_path / "nope") == []
        assert list_existing_cut_videos(tmp_path) == []


class TestRunCutAllOverwrite:
    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_refuses_existing_without_overwrite(self, mock_resolve, mock_cut, cfg):
        _write_plan(cfg, seq=[{"index": "001", "title": "A", "use_timeline": "00:00-00:10"}])
        (cfg.compressed_dir / "001_src.mp4").write_bytes(b"\x00")
        out = cfg.paths.output_dir / "cuts" / "day1"
        out.mkdir(parents=True)
        (out / "old_clip.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"
        from clio.tasks.cut import run_cut_all

        with pytest.raises(FileExistsError):
            run_cut_all(cfg, "day1", overwrite=False)
        mock_cut.assert_not_called()


class TestComputeSegmentOffset:
    def test_no_seg_suffix_returns_zero(self, cfg):
        from clio.tasks.cut import _compute_segment_offset

        result = _compute_segment_offset("001_src", cfg.compressed_dir, Path("/dummy.mp4"), "ffprobe")
        assert result == 0.0

    def test_only_one_segment_returns_zero(self, cfg):
        (cfg.compressed_dir / "001_src_seg1.mp4").write_bytes(b"\x00")
        from clio.tasks.cut import _compute_segment_offset

        result = _compute_segment_offset("001_src_seg1", cfg.compressed_dir, Path("/dummy.mp4"), "ffprobe")
        assert result == 0.0

    def test_computes_offset_for_seg2(self, cfg):
        for s in ["001_src_seg1.mp4", "001_src_seg2.mp4"]:
            (cfg.compressed_dir / s).write_bytes(b"\x00")
        with patch("clio.tasks.cut.get_duration_sec", return_value=120.0):
            from clio.tasks.cut import _compute_segment_offset

            result = _compute_segment_offset("001_src_seg2", cfg.compressed_dir, Path("/dummy.mp4"), "ffprobe")
            assert result == 60.0


class TestResolveVideoPath:
    def test_resolve_video_path_reads_vmeta(self, cfg):
        """When .vmeta exists, _resolve_video_path returns the original path from vmeta."""
        compressed = cfg.compressed_dir / "001_src.mp4"
        compressed.write_bytes(b"\x00" * 100)
        original = cfg.project_dir / "src.mp4"
        original.write_bytes(b"\x00" * 1000)
        save_selected_videos(cfg.project_dir, [original])
        meta = VideoMeta.build(source=original, target=compressed, source_duration=10, target_duration=10)
        meta.write(compressed)

        # We need to test _resolve_video_path's "original" path logic.
        # Create a wrapper that exposes _resolve_video_path with source="original".
        from clio.tasks.cut import run_cut_all

        seq = [{"index": "001", "title": "A", "use_timeline": "00:00-00:10"}]
        _write_plan(cfg, seq=seq)
        with patch("clio.tasks.cut.cut_one"), patch("clio.tasks.cut.resolve_binary", return_value="ffmpeg"):
            result = run_cut_all(cfg, "day1", source="original")
        assert len(result) == 1

    def test_resolve_video_path_falls_back_to_rglob(self, cfg):
        """Without .vmeta, _resolve_video_path falls back to rglob matching."""
        compressed = cfg.compressed_dir / "001_src.mp4"
        compressed.write_bytes(b"\x00" * 100)
        original = cfg.project_dir / "src.mp4"
        original.write_bytes(b"\x00" * 1000)
        save_selected_videos(cfg.project_dir, [original])

        seq = [{"index": "001", "title": "A", "use_timeline": "00:00-00:10"}]
        _write_plan(cfg, seq=seq)
        with patch("clio.tasks.cut.cut_one"), patch("clio.tasks.cut.resolve_binary", return_value="ffmpeg"):
            from clio.tasks.cut import run_cut_all

            result = run_cut_all(cfg, "day1", source="original")
        assert len(result) == 1


class TestRunCutAll:
    def test_plan_not_found(self, cfg):
        from clio.tasks.cut import run_cut_all

        with pytest.raises(FileNotFoundError, match="规划文件不存在"):
            run_cut_all(cfg, "day1")

    def test_empty_sequence(self, cfg):
        _write_plan(cfg, seq=[])
        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")
        assert result == []

    @patch("clio.tasks.cut.resolve_binary")
    def test_skips_segment_without_timeline(self, mock_resolve, cfg):
        _write_plan(cfg, seq=[{"index": "001", "title": "Intro", "use_timeline": ""}])
        mock_resolve.return_value = "ffmpeg"
        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")
        assert result == []

    @patch("clio.tasks.cut.resolve_binary")
    def test_skips_segment_without_index(self, mock_resolve, cfg):
        _write_plan(cfg, seq=[{"title": "Intro", "use_timeline": "00:00-00:30"}])
        mock_resolve.return_value = "ffmpeg"
        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")
        assert result == []

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_cuts_compressed_source(self, mock_resolve, mock_cut, cfg):
        _write_plan(cfg)
        (cfg.compressed_dir / "001_src.mp4").write_bytes(b"\x00")
        (cfg.compressed_dir / "002_src.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")
        assert len(result) == 2
        assert result[0]["video_index"] == "001"
        assert mock_cut.call_count == 2

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_reports_segment_progress(self, mock_resolve, mock_cut, cfg):
        _write_plan(cfg)
        (cfg.compressed_dir / "001_src.mp4").write_bytes(b"\x00")
        (cfg.compressed_dir / "002_src.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"
        tracker = MagicMock()

        from clio.tasks.cut import run_cut_all

        run_cut_all(cfg, "day1", tracker=tracker)

        tracker.update.assert_called_once_with(phase="cut", current=0, total=2, message="准备裁剪 2 个片段")
        assert tracker.next.call_count == 2

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_skips_missing_video(self, mock_resolve, mock_cut, cfg):
        _write_plan(cfg)
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")
        assert result == []
        mock_cut.assert_not_called()

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_resolves_unpadded_plan_index_with_custom_width(self, mock_resolve, mock_cut, cfg):
        cfg.global_cfg.naming.index_width = 4
        _write_plan(cfg, seq=[{"index": "1", "title": "Intro", "use_timeline": "00:00-00:10"}])
        video = cfg.compressed_dir / "0001_src.mp4"
        video.write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")

        assert len(result) == 1
        assert mock_cut.call_args.args[0] == video

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_skips_invalid_timeline(self, mock_resolve, mock_cut, cfg):
        _write_plan(cfg, seq=[{"index": "001", "title": "X", "use_timeline": "invalid"}])
        (cfg.compressed_dir / "001_src.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")
        assert result == []
        mock_cut.assert_not_called()

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_cancel_event_stops_processing(self, mock_resolve, mock_cut, cfg):
        _write_plan(
            cfg,
            seq=[
                {"index": "001", "title": "A", "use_timeline": "00:00-00:10"},
                {"index": "002", "title": "B", "use_timeline": "00:00-00:10"},
            ],
        )
        (cfg.compressed_dir / "001_src.mp4").write_bytes(b"\x00")
        (cfg.compressed_dir / "002_src.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"
        cancel = threading.Event()

        def _cancel_after_first(*_, **__):
            cancel.set()

        mock_cut.side_effect = _cancel_after_first

        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1", cancel_event=cancel)
        assert len(result) == 1

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_creates_manifest(self, mock_resolve, mock_cut, cfg):
        _write_plan(cfg)
        (cfg.compressed_dir / "001_src.mp4").write_bytes(b"\x00")
        (cfg.compressed_dir / "002_src.mp4").write_bytes(b"\x00")
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.cut import run_cut_all

        out_dir = cfg.paths.output_dir / "cuts" / "day1"
        run_cut_all(cfg, "day1")
        manifest = out_dir / "manifest.md"
        assert manifest.exists()
        content = manifest.read_text(encoding="utf-8")
        assert "Day 1" in content
        assert "Paris" in content

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_copies_text_json(self, mock_resolve, mock_cut, cfg):
        _write_plan(cfg)
        (cfg.compressed_dir / "001_src.mp4").write_bytes(b"\x00")
        (cfg.compressed_dir / "002_src.mp4").write_bytes(b"\x00")
        (cfg.texts_dir / "001_test.json").write_text('{"title": "Intro clip"}')
        mock_resolve.return_value = "ffmpeg"

        from clio.tasks.cut import run_cut_all

        result = run_cut_all(cfg, "day1")
        assert result[0]["text_file"] != ""
        clip_texts = cfg.paths.output_dir / "cuts" / "day1" / result[0]["text_file"]
        assert clip_texts.exists()
        data = json.loads(clip_texts.read_text(encoding="utf-8"))
        assert "_cut_info" in data
        assert data["title"] == "Intro clip"

    @patch("clio.tasks.cut.cut_one")
    @patch("clio.tasks.cut.resolve_binary")
    def test_original_source_applies_offset(self, mock_resolve, mock_cut, cfg):
        seq = [{"index": "001", "title": "A", "use_timeline": "00:00-00:10"}]
        _write_plan(cfg, seq=seq)
        (cfg.compressed_dir / "001_src_seg1.mp4").write_bytes(b"\x00")
        (cfg.compressed_dir / "001_src_seg2.mp4").write_bytes(b"\x00")
        (cfg.project_dir / "src.mp4").write_bytes(b"\x00")
        save_selected_videos(cfg.project_dir, [cfg.project_dir / "src.mp4"])
        mock_resolve.return_value = "ffmpeg"
        with patch("clio.tasks.cut.get_duration_sec", return_value=120.0):
            from clio.tasks.cut import run_cut_all

            result = run_cut_all(cfg, "day1", source="original")
            assert len(result) == 1


class TestResolveCutOutputDir:
    def test_default_under_output(self, cfg):
        from clio.tasks.cut import resolve_cut_output_dir

        out = resolve_cut_output_dir(cfg, "day1")
        assert out == (cfg.paths.output_dir / "cuts" / "day1").resolve()

    def test_rejects_outside(self, cfg, tmp_path):
        from clio.tasks.cut import resolve_cut_output_dir

        outside = tmp_path.parent / "elsewhere_cuts"
        outside.mkdir(exist_ok=True)
        with pytest.raises(ValueError, match="output_dir"):
            resolve_cut_output_dir(cfg, "day1", outside)

    def test_accepts_under_output(self, cfg):
        from clio.tasks.cut import resolve_cut_output_dir

        sub = cfg.paths.output_dir / "custom_cuts"
        sub.mkdir(parents=True, exist_ok=True)
        out = resolve_cut_output_dir(cfg, "day1", sub)
        assert out == sub.resolve()

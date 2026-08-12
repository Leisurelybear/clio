"""Tests for clio/ui/routes/videos.py — _parse_segment_info + handler mocks."""

from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from clio.ui.routes.videos import (
    _VIDEOS_CACHE,
    _cover_for_text_json,
    _cover_map,
    _parse_segment_info,
    handle_get_video,
    handle_get_videos,
    handle_put_videos_relink,
    handle_put_videos_selected,
)
from clio.vmeta import VideoMeta


def _clear_videos_cache() -> None:
    _VIDEOS_CACHE.clear()


class TestParseSegmentInfo:
    def test_no_underscore(self):
        assert _parse_segment_info("NOUNDERSCORE") == (None, None)

    def test_no_seg_suffix(self):
        """Plain compressed file with no _segNN suffix."""
        assert _parse_segment_info("001_GL010683") == (None, None)

    def test_basic_segment(self):
        gk, sn = _parse_segment_info("001_GL010683_seg01")
        assert gk == "GL010683"
        assert sn == 1

    def test_multi_digit_segment(self):
        gk, sn = _parse_segment_info("001_GL010683_seg12")
        assert gk == "GL010683"
        assert sn == 12

    def test_stem_before_underscore_matches(self):
        gk, sn = _parse_segment_info("002_GX010456_seg07")
        assert gk == "GX010456"
        assert sn == 7

    def test_seg_prefix_not_false_match(self):
        """_seg without digits should not match."""
        assert _parse_segment_info("001_GL010683_segment") == (None, None)

    def test_seg_with_trailing_text(self):
        """_seg01a should not match (non-digit after seg)."""
        assert _parse_segment_info("001_GL010683_seg01a") == (None, None)

    def test_only_seg_prefix(self):
        assert _parse_segment_info("_seg01") == (None, None)  # no underscore prefix → stem split gives "_seg01"

    def test_part_pattern(self):
        gk, sn = _parse_segment_info("001_GX010456_part07")
        assert gk == "GX010456"
        assert sn == 7

    def test_pt_pattern(self):
        gk, sn = _parse_segment_info("001_GX010456_pt03")
        assert gk == "GX010456"
        assert sn == 3

    def test_chunk_pattern_case_insensitive(self):
        gk, sn = _parse_segment_info("001_GX010456_Chunk02")
        assert gk == "GX010456"
        assert sn == 2

    def test_seg_pattern_uppercase(self):
        gk, sn = _parse_segment_info("001_GX010456_SEG05")
        assert gk == "GX010456"
        assert sn == 5

    def test_empty_string(self):
        assert _parse_segment_info("") == (None, None)


class TestHandleGetVideos:
    def setup_method(self):
        _clear_videos_cache()

    def test_source_compressed(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_GL010695.mp4").write_bytes(b"")

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["compressed"]})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert payload["source"] == "compressed"
        assert len(payload["videos"]) == 1
        assert payload["videos"][0]["file"] == "001_GL010695.mp4"
        assert payload["videos"][0]["index"] == "001"

    def test_compressed_prefers_canonical_and_preserves_natural_part_suffix(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        source = proj_dir / "holiday_part01.mov"
        source.write_bytes(b"source")
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        segment = comp_dir / "001_holiday_part01_seg01.mp4"
        whole = comp_dir / "003_holiday_part01.mp4"
        segment.write_bytes(b"segment")
        whole.write_bytes(b"whole")
        VideoMeta.build(source, whole, 2400.0, 2400.0, split_info=None).write(whole)

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["compressed"]})

        payload = handler._send_json.call_args[0][0]
        assert [video["file"] for video in payload["videos"]] == [whole.name]
        assert payload["videos"][0]["group_key"] is None
        assert payload["videos"][0]["segment_label"] is None

    def test_source_original(self, tmp_path: Path):
        import json

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        original = proj_dir / "GL010695.MP4"
        original.write_bytes(b"")
        (proj_dir / "videos.json").write_text(json.dumps([str(original.resolve())]), encoding="utf-8")
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_GL010695.mp4").write_bytes(b"")

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["original"]})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert payload["source"] == "original"
        assert len(payload["videos"]) == 1
        assert payload["videos"][0]["match"]["file"] == "001_GL010695.mp4"

    def test_source_original_uses_videos_json_for_nested(self, tmp_path: Path):
        import json

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        nested = proj_dir / "day1"
        nested.mkdir(parents=True)
        original = nested / "GL010695.MP4"
        original.write_bytes(b"")
        (proj_dir / "videos.json").write_text(json.dumps([str(original.resolve())]), encoding="utf-8")
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        (proj_out / "compressed").mkdir()

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._get_config.return_value = SimpleNamespace(
            paths=SimpleNamespace(ffprobe=""),
            whisper=SimpleNamespace(transcripts_subdir="transcripts"),
        )
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["original"]})

        payload = handler._send_json.call_args[0][0]
        assert len(payload["videos"]) == 1
        assert payload["videos"][0]["file"] in {"day1/GL010695.MP4", "GL010695.MP4"}
        assert payload["videos"][0]["match"] is None

    def test_invalid_source(self, tmp_path: Path):
        handler = MagicMock()
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["invalid"]})

        handler._send_json.assert_called_once_with({"ok": False, "error": "source must be compressed|original"}, 400)

    def test_groups_populated_for_segments(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        (proj_dir / "GL010695.MP4").write_bytes(b"")
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_GL010695_seg01.mp4").write_bytes(b"")
        (comp_dir / "002_GL010695_seg02.mp4").write_bytes(b"")

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["compressed"]})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert "GL010695" in payload["groups"]
        group = payload["groups"]["GL010695"]
        assert group["total"] == 2
        vid0 = payload["videos"][0]
        vid1 = payload["videos"][1]
        assert vid0["group_key"] == "GL010695"
        assert vid0["segment_label"] == "1/2"
        assert vid1["segment_label"] == "2/2"

    def test_repeated_request_uses_cache(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_GL010695.mp4").write_bytes(b"")

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        with patch(
            "clio.ui.routes.videos._build_videos_payload",
            wraps=__import__("clio.ui.routes.videos", fromlist=["_build_videos_payload"])._build_videos_payload,
        ) as mock_build:
            handle_get_videos(handler, {"source": ["compressed"]})
            handle_get_videos(handler, {"source": ["compressed"]})

        assert mock_build.call_count == 1
        assert handler._send_json.call_count == 2

    def test_disk_cache_serves_after_memory_cleared(self, tmp_path: Path):
        """A fresh process (empty in-memory cache) must load the payload from a
        persisted disk cache instead of rebuilding, as long as the signature is
        unchanged. Eliminates the cold-start rebuild cost per app launch."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_GL010695.mp4").write_bytes(b"")

        def _make_handler():
            handler = MagicMock()
            handler._resolve_project_dir.return_value = proj_dir
            handler._get_project_output.return_value = proj_out
            handler._get_config.return_value = SimpleNamespace(
                whisper=SimpleNamespace(transcripts_subdir="transcripts"),
                paths=SimpleNamespace(ffprobe="ffprobe"),
            )
            handler._send_json = MagicMock()
            return handler

        # First build populates both in-memory and persisted caches.
        handle_get_videos(_make_handler(), {"source": ["compressed"]})

        # Simulate an app restart: in-memory cache is gone, only disk cache remains.
        _clear_videos_cache()
        handler2 = _make_handler()

        with patch(
            "clio.ui.routes.videos._build_videos_payload",
            wraps=__import__("clio.ui.routes.videos", fromlist=["_build_videos_payload"])._build_videos_payload,
        ) as mock_build2:
            handle_get_videos(handler2, {"source": ["compressed"]})

        assert mock_build2.call_count == 0, "should serve from persisted disk cache"
        assert handler2._send_json.call_count == 1
        payload = handler2._send_json.call_args[0][0]
        assert payload["videos"][0]["file"] == "001_GL010695.mp4"

    def test_disk_cache_rebuilds_when_signature_changes(self, tmp_path: Path):
        """Adding/removing a file changes the signature, so the stale persisted
        cache must not be served; the payload is rebuilt."""
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_GL010695.mp4").write_bytes(b"")

        def _make_handler():
            handler = MagicMock()
            handler._resolve_project_dir.return_value = proj_dir
            handler._get_project_output.return_value = proj_out
            handler._get_config.return_value = SimpleNamespace(
                whisper=SimpleNamespace(transcripts_subdir="transcripts"),
                paths=SimpleNamespace(ffprobe="ffprobe"),
            )
            handler._send_json = MagicMock()
            return handler

        handle_get_videos(_make_handler(), {"source": ["compressed"]})

        # Change a file so the signature no longer matches the persisted cache.
        (comp_dir / "001_GL010695.mp4").write_bytes(b"changed")

        _clear_videos_cache()
        handler2 = _make_handler()
        with patch(
            "clio.ui.routes.videos._build_videos_payload",
            wraps=__import__("clio.ui.routes.videos", fromlist=["_build_videos_payload"])._build_videos_payload,
        ) as mock_build2:
            handle_get_videos(handler2, {"source": ["compressed"]})
        assert mock_build2.call_count == 1, "signature change must force rebuild"

    def test_disk_cache_rebuilds_when_external_original_returns(self, tmp_path: Path):
        """An external source changing from offline to online must refresh the payload."""
        import json

        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        original = tmp_path / "external" / "GL010695.MP4"
        original.parent.mkdir()
        (proj_dir / "videos.json").write_text(json.dumps([str(original)]), encoding="utf-8")
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        (proj_out / "compressed").mkdir()

        def _make_handler():
            handler = MagicMock()
            handler._resolve_project_dir.return_value = proj_dir
            handler._get_project_output.return_value = proj_out
            handler._get_config.return_value = SimpleNamespace(
                whisper=SimpleNamespace(transcripts_subdir="transcripts"),
                paths=SimpleNamespace(ffprobe="ffprobe"),
            )
            handler._send_json = MagicMock()
            return handler

        handle_get_videos(_make_handler(), {"source": ["original"]})
        _clear_videos_cache()

        original.write_bytes(b"restored")
        handler2 = _make_handler()
        with patch(
            "clio.ui.routes.videos._build_videos_payload",
            wraps=__import__("clio.ui.routes.videos", fromlist=["_build_videos_payload"])._build_videos_payload,
        ) as mock_build2:
            handle_get_videos(handler2, {"source": ["original"]})

        assert mock_build2.call_count == 1
        assert handler2._send_json.call_args[0][0]["videos"][0].get("missing") is not True

    def test_selected_set_includes_video_with_vmeta(self, tmp_path: Path):
        """Compressed view + selected_set: include video whose .vmeta.source_path
        matches a path in videos.json (original outside proj_dir)."""
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()

        # Original video at a path OUTSIDE proj_dir (simulates videos.json)
        orig_root = tmp_path / "external"
        orig_root.mkdir()
        orig_video = orig_root / "GL010695.MP4"
        orig_video.write_bytes(b"original data")

        # Create compressed video + .vmeta pointing to the external original
        compressed = comp_dir / "001_GL010695.mp4"
        compressed.write_bytes(b"compressed data")
        from clio.vmeta import VideoMeta

        meta = VideoMeta.build(
            source=orig_video,
            target=compressed,
            source_duration=10.0,
            target_duration=5.0,
        )
        meta.write(compressed)

        # videos.json with the external path
        import json

        (proj_dir / "videos.json").write_text(json.dumps([str(orig_video.resolve())]))

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._get_config.return_value = SimpleNamespace(
            paths=SimpleNamespace(ffprobe=""),
            whisper=SimpleNamespace(transcripts_subdir="transcripts"),
        )
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["compressed"]})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args[0][0]
        assert payload["source"] == "compressed"
        assert len(payload["videos"]) == 1
        assert payload["videos"][0]["file"] == "001_GL010695.mp4"

    def test_selected_set_excludes_unmatched_video(self, tmp_path: Path):
        """Compressed view + selected_set: exclude video whose .vmeta.source_path
        is NOT in selected_set."""
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()

        # External original — this one IS selected
        orig_root = tmp_path / "external"
        orig_root.mkdir()
        selected_orig = orig_root / "GL010695.MP4"
        selected_orig.write_bytes(b"original data")

        # Another external original — this one is NOT selected
        non_selected_orig = orig_root / "GX010456.MP4"
        non_selected_orig.write_bytes(b"other original")

        # Compressed video with vmeta pointing to NON-selected original
        compressed = comp_dir / "001_GX010456.mp4"
        compressed.write_bytes(b"compressed data")
        from clio.vmeta import VideoMeta

        meta = VideoMeta.build(
            source=non_selected_orig,
            target=compressed,
            source_duration=10.0,
            target_duration=5.0,
        )
        meta.write(compressed)

        # videos.json only contains selected_orig
        import json

        (proj_dir / "videos.json").write_text(json.dumps([str(selected_orig.resolve())]))

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._get_config.return_value = SimpleNamespace(
            paths=SimpleNamespace(ffprobe=""),
            whisper=SimpleNamespace(transcripts_subdir="transcripts"),
        )
        handler._send_json = MagicMock()

        handle_get_videos(handler, {"source": ["compressed"]})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args[0][0]
        assert len(payload["videos"]) == 0

    def test_sidecar_change_invalidates_cache(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        texts_dir = proj_out / "texts"
        texts_dir.mkdir()
        (comp_dir / "001_GL010695.mp4").write_bytes(b"")

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        import clio.ui.routes.videos as videos_route

        with patch(
            "clio.ui.routes.videos._build_videos_payload", wraps=videos_route._build_videos_payload
        ) as mock_build:
            handle_get_videos(handler, {"source": ["compressed"]})
            (texts_dir / "001_GL010695.json").write_text('{"title":"新标题","index":"001"}', encoding="utf-8")
            handle_get_videos(handler, {"source": ["compressed"]})

        assert mock_build.call_count == 2
        payload = handler._send_json.call_args[0][0]
        assert payload["videos"][0]["title"] == "新标题"


class TestHandleGetVideo:
    def test_sends_video(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_test.mp4").write_bytes(b"video data")
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_video_range = MagicMock()

        handle_get_video(handler, {"file": ["001_test.mp4"], "source": ["compressed"]})

        handler._send_video_range.assert_called_once()

    def test_sends_nested_original_video(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        nested = proj_dir / "day1"
        nested.mkdir(parents=True)
        video = nested / "clip.mp4"
        video.write_bytes(b"video data")
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_video_range = MagicMock()

        handle_get_video(handler, {"file": ["day1/clip.mp4"], "source": ["original"]})

        handler._send_video_range.assert_called_once_with(video)

    def test_forbidden_basename(self, tmp_path: Path):
        handler = MagicMock()
        handler.send_error = MagicMock()

        handle_get_video(handler, {"file": ["../secret.txt"], "source": ["compressed"]})

        handler.send_error.assert_called_once()

    def test_symlink_escape_outside_root_forbidden(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        (proj_out / "compressed").mkdir()
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"secret")
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler.send_error = MagicMock()

        with patch(
            "clio.ui.routes.videos.validate_within_root",
            side_effect=ValueError("symlink not allowed"),
        ):
            handle_get_video(handler, {"file": ["001_test.mp4"], "source": ["compressed"]})

        handler.send_error.assert_called_once_with(HTTPStatus.FORBIDDEN)

    def test_forbidden_original_relative_traversal(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler.send_error = MagicMock()

        handle_get_video(handler, {"file": ["../secret.mp4"], "source": ["original"]})

        handler.send_error.assert_called_once_with(HTTPStatus.FORBIDDEN)

    def test_not_found(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        (proj_out / "compressed").mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler.send_error = MagicMock()

        handle_get_video(handler, {"file": ["nonexistent.mp4"], "source": ["compressed"]})

        handler.send_error.assert_called_once()

    def test_abspath_not_in_selected_set_returns_forbidden(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler.send_error = MagicMock()

        video = tmp_path / "secret.mp4"
        video.write_bytes(b"secret")
        handle_get_video(handler, {"file": [""], "source": ["original"], "abspath": [str(video)]})
        handler.send_error.assert_called_once_with(HTTPStatus.FORBIDDEN)

    def test_abspath_in_selected_set_sends_video(self, tmp_path: Path):
        import json

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_video_range = MagicMock()

        video = tmp_path / "external" / "GL010695.MP4"
        video.parent.mkdir()
        video.write_bytes(b"video data")
        (proj_dir / "videos.json").write_text(json.dumps([str(video.resolve())]), encoding="utf-8")

        handle_get_video(handler, {"file": ["GL010695.MP4"], "source": ["original"], "abspath": [str(video)]})
        handler._send_video_range.assert_called_once_with(video)

    def test_abspath_nonexistent_returns_not_found(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler.send_error = MagicMock()

        handle_get_video(handler, {"file": [""], "source": ["original"], "abspath": [str(tmp_path / "missing.mp4")]})
        handler.send_error.assert_called_once_with(HTTPStatus.NOT_FOUND)

    def test_indexed_original_request_resolves_external_selected_video(self, tmp_path: Path):
        import json

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        original = tmp_path / "nas" / "GX010681.MP4"
        original.parent.mkdir()
        original.write_bytes(b"video data")
        (proj_dir / "videos.json").write_text(json.dumps([str(original)]), encoding="utf-8")
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_video_range = MagicMock()

        handle_get_video(handler, {"file": ["002_GX010681.mp4"], "source": ["original"]})

        handler._send_video_range.assert_called_once_with(original)


class TestVideosJsonCacheInvalidation:
    def test_videos_json_change_invalidates_cache(self, tmp_path: Path):
        """Adding videos via PUT must not serve a stale empty list from cache."""

        _clear_videos_cache()
        handler = MagicMock()
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        proj_out = tmp_path / "out"
        (proj_out / "compressed").mkdir(parents=True)
        external = tmp_path / "ext"
        external.mkdir()
        video = external / "clip.mp4"
        video.write_bytes(b"x")

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._get_config.return_value = SimpleNamespace(
            paths=SimpleNamespace(ffprobe=""),
            whisper=SimpleNamespace(transcripts_subdir="transcripts"),
        )
        handler._send_json = MagicMock()

        # 1) empty list (no videos.json) — cache empty payload
        handle_get_videos(handler, {"source": ["original"]})
        payload1 = handler._send_json.call_args[0][0]
        assert payload1["videos"] == []

        # 2) PUT selection
        handler._send_json.reset_mock()
        handle_put_videos_selected(handler, {}, {"videos": [str(video.resolve())]})
        assert (proj_dir / "videos.json").is_file()

        # 3) GET again must reflect selection
        handler._send_json.reset_mock()
        handle_get_videos(handler, {"source": ["original"]})
        payload2 = handler._send_json.call_args[0][0]
        assert len(payload2["videos"]) == 1
        assert payload2["videos"][0].get("abs_path")


class TestOfflineOriginalListing:
    def test_offline_video_marked_missing(self, tmp_path: Path):
        _clear_videos_cache()
        handler = MagicMock()
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        proj_out = tmp_path / "out"
        (proj_out / "compressed").mkdir(parents=True)
        offline = tmp_path / "gone" / "clip.mp4"  # does not exist
        (proj_dir / "videos.json").write_text(__import__("json").dumps([str(offline)]), encoding="utf-8")
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._get_config.return_value = SimpleNamespace(
            paths=SimpleNamespace(ffprobe=""),
            whisper=SimpleNamespace(transcripts_subdir="transcripts"),
        )
        handler._send_json = MagicMock()
        handle_get_videos(handler, {"source": ["original"]})
        payload = handler._send_json.call_args[0][0]
        assert len(payload["videos"]) == 1
        assert payload["videos"][0]["missing"] is True
        assert payload["videos"][0]["file"] == "clip.mp4"


class TestPutVideosSelectedOffline:
    def test_preserves_offline_paths(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        online = tmp_path / "live.mp4"
        online.write_bytes(b"x")
        offline = tmp_path / "offline_drive" / "old.mp4"
        handle_put_videos_selected(
            handler,
            {},
            {"videos": [str(online), str(offline)]},
        )
        resp = handler._send_json.call_args[0][0]
        assert resp["ok"] is True
        assert resp["count"] == 2
        saved = __import__("json").loads((proj_dir / "videos.json").read_text(encoding="utf-8"))
        assert len(saved) == 2
        assert any("live.mp4" in s for s in saved)
        assert any("old.mp4" in s for s in saved)

    def test_rejects_bad_extension(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        txt = tmp_path / "notes.txt"
        txt.write_text("x")
        handle_put_videos_selected(handler, {}, {"videos": [str(txt)]})
        resp = handler._send_json.call_args[0][0]
        assert resp["ok"] is True
        assert resp["count"] == 0
        assert resp.get("rejected_count") == 1


class TestCompressedMatchesAfterRelink:
    def test_stem_match_keeps_compressed_visible(self, tmp_path: Path):
        """After relink, .vmeta may still point at old path; stem match must keep row."""
        _clear_videos_cache()
        from clio.vmeta import VideoMeta

        proj_dir = tmp_path / "proj"
        proj_dir.mkdir()
        proj_out = tmp_path / "out"
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir(parents=True)
        old = tmp_path / "old" / "GL010695.MP4"
        new = tmp_path / "new" / "GL010695.MP4"
        old.parent.mkdir()
        new.parent.mkdir()
        old.write_bytes(b"old")  # vmeta build needs source stat
        new.write_bytes(b"new")
        # compressed artifact + vmeta with OLD source
        comp = comp_dir / "001_GL010695.mp4"
        comp.write_bytes(b"c")
        meta = VideoMeta.build(old, comp, source_duration=1.0, target_duration=1.0)
        meta.write(comp)
        # remove old file to simulate offline after move; selection points at new
        old.unlink()
        (proj_dir / "videos.json").write_text(__import__("json").dumps([str(new.resolve())]), encoding="utf-8")

        handler = MagicMock()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._get_config.return_value = SimpleNamespace(
            paths=SimpleNamespace(ffprobe=""),
            whisper=SimpleNamespace(transcripts_subdir="transcripts"),
        )
        handler._send_json = MagicMock()
        handle_get_videos(handler, {"source": ["compressed"]})
        payload = handler._send_json.call_args[0][0]
        assert len(payload["videos"]) == 1
        assert payload["videos"][0]["file"] == "001_GL010695.mp4"


class TestHandlePutVideosRelink:
    """Tests for handle_put_videos_relink."""

    @pytest.fixture
    def handler(self):
        h = MagicMock()
        h._resolve_project_dir.return_value = Path("/nonexistent/project")
        return h

    def test_missing_params(self, handler):
        handle_put_videos_relink(handler, {}, {})
        handler._send_json.assert_called_once_with({"ok": False, "error": "需要提供 old_path 和 new_path"}, 400)

    def test_new_path_not_exist(self, handler):
        handle_put_videos_relink(handler, {}, {"old_path": "/old/v.mp4", "new_path": "/new/v.mp4"})
        resp = handler._send_json.call_args.args[0]
        assert resp.get("ok") is False
        assert "不是有效的视频文件" in resp.get("error", "")

    def test_new_path_not_video(self, handler):
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_file", return_value=True):
                with patch("pathlib.Path.suffix", ".txt"):
                    handle_put_videos_relink(handler, {}, {"old_path": "/old/v.mp4", "new_path": "/new/v.txt"})
        resp = handler._send_json.call_args.args[0]
        assert resp.get("ok") is False
        assert "不是有效的视频文件" in resp.get("error", "")

    def test_old_path_not_found(self, handler):
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.suffix", ".mp4"),
            patch("clio.ui.routes.videos.load_selected_videos", return_value=[]),
        ):
            handle_put_videos_relink(handler, {}, {"old_path": "/old/v.mp4", "new_path": "/new/v.mp4"})
        resp = handler._send_json.call_args.args[0]
        assert resp.get("ok") is False
        assert "未在项目视频列表中找到" in resp.get("error", "")

    def test_multiple_matches_by_name(self, handler):
        videos = [Path("/a/dup.MP4"), Path("/b/dup.MP4")]
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.is_file", return_value=True),
            patch("pathlib.Path.suffix", ".mp4"),
            patch("clio.ui.routes.videos.load_selected_videos", return_value=videos),
        ):
            handle_put_videos_relink(handler, {}, {"old_path": "/old/dup.MP4", "new_path": "/new/v.mp4"})
        resp = handler._send_json.call_args.args[0]
        assert resp.get("ok") is False
        assert "找到多个同名文件" in resp.get("error", "")

    def test_successful_relink_by_resolved_path(self, tmp_path, handler):
        old_file = tmp_path / "video.MP4"
        old_file.write_text("dummy")
        new_file = tmp_path / "renamed.MP4"
        new_file.write_text("dummy")
        videos = [old_file]
        with (
            patch("clio.ui.routes.videos.load_selected_videos", return_value=videos),
            patch("clio.ui.routes.videos.save_selected_videos") as mock_save,
            patch("clio.ui.routes.videos._invalidate_videos_cache") as mock_invalidate,
        ):
            handle_put_videos_relink(handler, {}, {"old_path": str(old_file), "new_path": str(new_file)})
        resp = handler._send_json.call_args.args[0]
        assert resp.get("ok") is True
        mock_save.assert_called_once()
        saved = mock_save.call_args.args[1]
        assert str(saved[0]) == str(new_file.resolve())
        mock_invalidate.assert_called_once()

    def test_successful_relink_by_fallback_filename(self, tmp_path, handler):
        new_file = tmp_path / "video.MP4"
        new_file.write_text("dummy")
        # old path is gone, videos.json still references it
        videos = [tmp_path / "video.MP4"]
        with (
            patch("clio.ui.routes.videos.load_selected_videos", return_value=videos),
            patch("clio.ui.routes.videos.save_selected_videos") as mock_save,
            patch("clio.ui.routes.videos._invalidate_videos_cache"),
        ):
            handle_put_videos_relink(handler, {}, {"old_path": str(tmp_path / "video.MP4"), "new_path": str(new_file)})
        resp = handler._send_json.call_args.args[0]
        assert resp.get("ok") is True
        saved = mock_save.call_args.args[1]
        assert str(saved[0]) == str(new_file.resolve())


class TestCoverMap:
    def test_maps_image_stems(self, tmp_path: Path):
        covers = tmp_path / "covers"
        covers.mkdir()
        (covers / "001_title.jpg").write_bytes(b"x")
        (covers / "002_other.png").write_bytes(b"x")
        (covers / "notes.txt").write_text("skip")
        mapping = _cover_map(tmp_path)
        assert mapping["001_title"] == "001_title.jpg"
        assert mapping["002_other"] == "002_other.png"
        assert "notes" not in mapping

    def test_missing_covers_dir(self, tmp_path: Path):
        assert _cover_map(tmp_path) == {}

    def test_cover_for_text_json_exact_stem(self):
        mapping = {"001_hello": "001_hello.jpg"}
        assert _cover_for_text_json("001_hello.json", mapping) == "001_hello.jpg"
        assert _cover_for_text_json(None, mapping, "001") == "001_hello.jpg"
        assert _cover_for_text_json(None, mapping, "999") is None


class TestVideosPayloadCoverFile:
    def setup_method(self):
        _clear_videos_cache()

    def test_cover_file_attached_for_compressed(self, tmp_path: Path):
        import json

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_GL010695.mp4").write_bytes(b"")
        texts = proj_out / "texts"
        texts.mkdir()
        (texts / "001_GL010695.json").write_text(json.dumps({"title": "t", "segments": []}), encoding="utf-8")
        covers = proj_out / "covers"
        covers.mkdir()
        (covers / "001_GL010695.jpg").write_bytes(b"img")

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()
        cfg = SimpleNamespace(
            paths=SimpleNamespace(ffprobe=""),
            whisper=SimpleNamespace(transcripts_subdir="transcripts"),
        )
        handler._get_config.return_value = cfg

        with patch("clio.ui.routes.videos.load_selected_videos", return_value=[]):
            # empty selected_set allows browsing all compressed artifacts
            handle_get_videos(handler, {"source": ["compressed"]})

        payload = handler._send_json.call_args[0][0]
        assert len(payload["videos"]) == 1
        v = payload["videos"][0]
        assert v.get("cover_file") == "001_GL010695.jpg"
        assert v.get("text_json") == "001_GL010695.json"


class TestHandleGetVmeta:
    def test_found_returns_meta(self, tmp_path: Path):
        from clio.ui.routes.videos import handle_get_vmeta

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_test.mp4").write_bytes(b"")
        import json as _json

        _vmeta = _json.dumps(
            {
                "version": 1,
                "data": {
                    "source_path": "/some/path",
                    "target_path": "001_test.mp4",
                    "is_original": False,
                    "is_split_segment": False,
                    "split_info": None,
                },
                "source_modifyTime": 1234567890,
                "source_size": 1000000,
                "target_modifyTime": 1234567890,
                "target_size": 500000,
                "source_duration_sec": 60.0,
                "target_duration_sec": 60.0,
                "compress_settings": {},
                "verify": "",
            }
        )
        (comp_dir / "001_test.vmeta").write_text(_vmeta)

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_vmeta(handler, {}, "001_test")

        handler._send_json.assert_called_once()
        call_args = handler._send_json.call_args[0][0]
        assert call_args.get("data", {}).get("source_path") == "/some/path"

    def test_path_traversal_blocked(self, tmp_path: Path):
        """GAP-P1-04: stem with path traversal must be rejected."""
        from clio.ui.routes.videos import handle_get_vmeta

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        # Create a file outside compressed/ that should NOT be accessible
        other = tmp_path / "other"
        other.mkdir()
        (other / "secret.vmeta").write_text('{"api_key": "SECRET123"}')

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        # Attempt path traversal via stem
        handle_get_vmeta(handler, {}, "../other/secret")

        # Should return forbidden, not leak the file
        call_args = handler._send_json.call_args[0][0]
        assert call_args.get("ok") is False
        assert "forbidden" in call_args.get("error", "").lower()

    def test_not_found_returns_404(self, tmp_path: Path):
        from clio.ui.routes.videos import handle_get_vmeta

        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()

        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        handler._send_json = MagicMock()

        handle_get_vmeta(handler, {}, "nonexistent")

        handler._send_json.assert_called_once()
        call_args = handler._send_json.call_args[0][0]
        assert call_args["ok"] is False
        assert call_args["error"] == "not found"

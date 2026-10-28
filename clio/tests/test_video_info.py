"""Tests for video metadata probe and GET /api/video/info."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import clio.utils as utils
from clio.tasks._video_loader import save_selected_videos
from clio.ui.routes.videos import handle_get_video_info
from clio.vmeta import VideoMeta

_PROBE_JSON = {
    "streams": [
        {"codec_type": "audio", "codec_name": "aac"},
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "avg_frame_rate": "30000/1001",
            "bit_rate": "5000000",
        },
    ],
    "format": {
        "duration": "123.456",
        "size": "1048576",
        "bit_rate": "5100000",
        "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
    },
}


@pytest.fixture(autouse=True)
def _clear_details_cache():
    utils._MEDIA_DETAILS_CACHE.clear()
    yield
    utils._MEDIA_DETAILS_CACHE.clear()


def _probe_result(payload: dict):
    return SimpleNamespace(stdout=json.dumps(payload), returncode=0)


class TestProbeMediaDetails:
    def test_parses_video_stream_and_format(self, tmp_path: Path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        with patch.object(utils, "run_probe", return_value=_probe_result(_PROBE_JSON)) as probe:
            info = utils.probe_media_details(video, "ffprobe")
        probe.assert_called_once()
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["codec"] == "h264"
        assert info["fps"] == 29.97
        assert info["bit_rate_bps"] == 5000000
        assert info["duration_sec"] == pytest.approx(123.456)
        assert info["size_bytes"] == 1048576
        assert info["format_name"].startswith("mov,mp4")

    def test_falls_back_to_r_frame_rate_and_format_bitrate(self, tmp_path: Path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        payload = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": "1280",
                    "height": "720",
                    "avg_frame_rate": "0/0",
                    "r_frame_rate": "25/1",
                }
            ],
            "format": {"duration": "10.0", "size": "2048", "bit_rate": "1500000"},
        }
        with patch.object(utils, "run_probe", return_value=_probe_result(payload)):
            info = utils.probe_media_details(video, "ffprobe")
        assert info["fps"] == 25.0
        assert info["bit_rate_bps"] == 1500000
        assert info["codec"] == "hevc"

    def test_caches_by_path_size_and_mtime(self, tmp_path: Path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        with patch.object(utils, "run_probe", return_value=_probe_result(_PROBE_JSON)) as probe:
            first = utils.probe_media_details(video, "ffprobe")
            second = utils.probe_media_details(video, "ffprobe")
        assert probe.call_count == 1
        assert first == second
        assert first is not second


def _handler(tmp_path: Path, *, ffprobe: str | None = "ffprobe") -> tuple[MagicMock, Path, Path]:
    proj_dir = tmp_path / "input"
    proj_dir.mkdir()
    proj_out = tmp_path / "output"
    proj_out.mkdir()
    handler = MagicMock()
    handler._resolve_project_dir.return_value = proj_dir
    handler._get_project_output.return_value = proj_out
    handler._send_json = MagicMock()
    if ffprobe is None:
        handler._get_config.side_effect = RuntimeError("no config")
    else:
        handler._get_config.return_value = SimpleNamespace(paths=SimpleNamespace(ffprobe=ffprobe))
    return handler, proj_dir, proj_out


def _payload(handler: MagicMock) -> dict:
    assert handler._send_json.call_count == 1
    return handler._send_json.call_args[0][0]


class TestHandleGetVideoInfo:
    def test_compressed_returns_stat_vmeta_and_probe(self, tmp_path: Path):
        handler, proj_dir, proj_out = _handler(tmp_path)
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        source = proj_dir / "GL010695.MP4"
        source.write_bytes(b"original-bytes")
        compressed = comp_dir / "001_GL010695.mp4"
        compressed.write_bytes(b"compressed")
        VideoMeta.build(
            source,
            compressed,
            source_duration=120.0,
            target_duration=119.5,
            compress_settings={"max_width": 640, "fps": 30, "target_size_mb": 5},
        ).write(compressed)

        probed = {"width": 640, "height": 360, "fps": 30.0, "codec": "h264", "duration_sec": 119.4}
        with (
            patch("clio.ui.routes.videos.resolve_binary", return_value="ffprobe"),
            patch("clio.ui.routes.videos.probe_media_details", return_value=probed),
        ):
            handle_get_video_info(handler, {"source": ["compressed"], "file": ["001_GL010695.mp4"]})

        payload = _payload(handler)
        assert payload["ok"] is True
        assert payload["source"] == "compressed"
        assert payload["exists"] is True
        assert payload["size_bytes"] == len(b"compressed")
        assert payload["abs_path"].endswith("001_GL010695.mp4")
        assert payload["width"] == 640
        assert payload["fps"] == 30.0
        compression = payload["compression"]
        assert compression["source_size_bytes"] == len(b"original-bytes")
        assert compression["target_size_bytes"] == len(b"compressed")
        assert compression["settings"]["max_width"] == 640

    def test_original_uses_allowlist_abspath(self, tmp_path: Path):
        handler, proj_dir, _proj_out = _handler(tmp_path)
        original = proj_dir / "GL010700.MP4"
        original.write_bytes(b"abc")
        save_selected_videos(proj_dir, [original])
        with (
            patch("clio.ui.routes.videos.resolve_binary", return_value="ffprobe"),
            patch("clio.ui.routes.videos.probe_media_details", return_value={"width": 3840, "height": 2160}),
        ):
            handle_get_video_info(
                handler,
                {
                    "source": ["original"],
                    "file": ["GL010700.MP4"],
                    "abspath": [str(original)],
                },
            )
        payload = _payload(handler)
        assert payload["source"] == "original"
        assert payload["exists"] is True
        assert payload["width"] == 3840

    def test_offline_original_reports_missing_without_probe(self, tmp_path: Path):
        handler, proj_dir, _proj_out = _handler(tmp_path)
        missing = tmp_path / "detached" / "GL010701.MP4"
        save_selected_videos(proj_dir, [missing])
        with patch("clio.ui.routes.videos.probe_media_details") as probe:
            handle_get_video_info(
                handler,
                {
                    "source": ["original"],
                    "file": ["GL010701.MP4"],
                    "abspath": [str(missing)],
                },
            )
        probe.assert_not_called()
        payload = _payload(handler)
        assert payload["exists"] is False
        assert payload["size_bytes"] is None
        assert payload["abs_path"].endswith("GL010701.MP4")

    def test_rejects_original_outside_allowlist(self, tmp_path: Path):
        handler, proj_dir, _proj_out = _handler(tmp_path)
        allowed = proj_dir / "ok.mp4"
        allowed.write_bytes(b"a")
        other = tmp_path / "other.mp4"
        other.write_bytes(b"b")
        save_selected_videos(proj_dir, [allowed])
        handle_get_video_info(
            handler,
            {"source": ["original"], "file": ["other.mp4"], "abspath": [str(other)]},
        )
        assert handler._send_json.call_args[0][1] == 404

    def test_rejects_unsafe_compressed_name(self, tmp_path: Path):
        handler, _proj_dir, _proj_out = _handler(tmp_path)
        handle_get_video_info(handler, {"source": ["compressed"], "file": ["../evil.mp4"]})
        assert handler._send_json.call_args[0][1] == 404

    def test_probe_failure_still_returns_basic_info(self, tmp_path: Path):
        handler, _proj_dir, proj_out = _handler(tmp_path)
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        compressed = comp_dir / "001_x.mp4"
        compressed.write_bytes(b"12345")
        with (
            patch("clio.ui.routes.videos.resolve_binary", return_value="ffprobe"),
            patch("clio.ui.routes.videos.probe_media_details", side_effect=RuntimeError("probe boom")),
        ):
            handle_get_video_info(handler, {"source": ["compressed"], "file": ["001_x.mp4"]})
        payload = _payload(handler)
        assert payload["ok"] is True
        assert payload["size_bytes"] == 5
        assert "probe boom" in payload["probe_error"]

    def test_missing_ffprobe_degrades(self, tmp_path: Path):
        handler, _proj_dir, proj_out = _handler(tmp_path, ffprobe=None)
        comp_dir = proj_out / "compressed"
        comp_dir.mkdir()
        (comp_dir / "001_x.mp4").write_bytes(b"12345")
        handle_get_video_info(handler, {"source": ["compressed"], "file": ["001_x.mp4"]})
        payload = _payload(handler)
        assert payload["ok"] is True
        assert payload["probe_error"] == "ffprobe unavailable"

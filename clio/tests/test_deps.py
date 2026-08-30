"""Tests for probe_ffmpeg_deps."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from clio.utils import media_dependencies_for_steps, preflight_media_deps, probe_ffmpeg_deps


class TestProbeFfmpegDeps:
    def test_both_found(self, tmp_path: Path):
        ff = tmp_path / "ffmpeg.exe"
        fp = tmp_path / "ffprobe.exe"
        ff.write_bytes(b"x")
        fp.write_bytes(b"x")
        out = probe_ffmpeg_deps(str(ff), str(fp))
        assert out["ok"] is True
        assert out["ffmpeg"] == str(ff)
        assert out["ffprobe"] == str(fp)
        assert out["missing"] == []
        assert out["detail"] == ""

    def test_neither_found_empty_config(self):
        with patch("clio.utils.discover_ffmpeg_bin", return_value=None):
            out = probe_ffmpeg_deps("", "")
        assert out["ok"] is False
        assert set(out["missing"]) == {"ffmpeg", "ffprobe"}
        assert out["ffmpeg"] is None and out["ffprobe"] is None
        assert "ffmpeg" in out["detail"] and "ffprobe" in out["detail"]

    def test_only_ffmpeg_found(self, tmp_path: Path):
        ff = tmp_path / "ffmpeg.exe"
        ff.write_bytes(b"x")

        def fake_resolve(configured, fallback):
            if fallback == "ffmpeg":
                return str(ff)
            raise FileNotFoundError(fallback)

        with patch("clio.utils.resolve_binary", side_effect=fake_resolve):
            out = probe_ffmpeg_deps("", "")
        assert out["ok"] is False
        assert out["missing"] == ["ffprobe"]
        assert out["ffmpeg"] == str(ff)
        assert "ffprobe" in out["detail"]

    def test_bad_configured_path(self, tmp_path: Path):
        out = probe_ffmpeg_deps(str(tmp_path / "nope.exe"), str(tmp_path / "nope2.exe"))
        assert out["ok"] is False
        assert "ffmpeg" in out["missing"]
        assert "ffprobe" in out["missing"]

    def test_empty_not_coerced_to_bare_name(self):
        calls = []

        def fake_resolve(configured, fallback):
            calls.append((configured, fallback))
            raise FileNotFoundError(fallback)

        with patch("clio.utils.resolve_binary", side_effect=fake_resolve):
            probe_ffmpeg_deps("", "")
        assert calls == [("", "ffmpeg"), ("", "ffprobe")]


class TestMediaPreflight:
    def test_step_dependencies_are_minimal(self):
        assert media_dependencies_for_steps(["label"]) == ("ffmpeg",)
        assert media_dependencies_for_steps(["analyze", "voiceover", "plan"]) == ()
        assert media_dependencies_for_steps(["compress", "transcribe", "label"]) == ("ffmpeg", "ffprobe")
        assert media_dependencies_for_steps(None) == ("ffmpeg", "ffprobe")

    def test_only_required_binaries_are_resolved(self, tmp_path: Path):
        ffmpeg = tmp_path / "ffmpeg.exe"
        ffmpeg.write_bytes(b"x")
        with patch("clio.utils.resolve_binary", return_value=str(ffmpeg)) as resolve:
            result = preflight_media_deps(str(ffmpeg), "", required=("ffmpeg",))

        assert result["ok"] is True
        assert result["required"] == ["ffmpeg"]
        assert result["missing"] == []
        resolve.assert_called_once_with(str(ffmpeg), "ffmpeg")

    def test_missing_required_binary_is_structured(self):
        with patch("clio.utils.resolve_binary", side_effect=FileNotFoundError("missing")):
            result = preflight_media_deps("", "", required=("ffprobe",))

        assert result["ok"] is False
        assert result["required"] == ["ffprobe"]
        assert result["ffmpeg"] is None and result["ffprobe"] is None
        assert result["missing"] == ["ffprobe"]
        assert result["detail"] == (
            f"未找到 ffprobe。请运行 {'setup.ps1' if os.name == 'nt' else 'setup.sh'}，"
            "或在 config.yaml 的 paths.ffmpeg / paths.ffprobe 中填写路径。"
        )

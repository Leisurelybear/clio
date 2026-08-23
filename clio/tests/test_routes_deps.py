"""Tests for clio/ui/routes/deps.py."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from clio.ui.routes.deps import handle_get_deps_ffmpeg, handle_get_deps_keys


def _handler(tmp_path: Path) -> MagicMock:
    h = MagicMock()
    proj = tmp_path / "proj"
    proj.mkdir()
    h._resolve_project_dir.return_value = proj
    h._get_config.return_value = SimpleNamespace(paths=SimpleNamespace(ffmpeg="", ffprobe=""))
    h._send_json = MagicMock()
    return h


def _cfg(providers: dict, tasks: dict) -> SimpleNamespace:
    return SimpleNamespace(
        paths=SimpleNamespace(ffmpeg="", ffprobe=""),
        ai=SimpleNamespace(providers=providers, tasks=tasks),
    )


class TestHandleGetDepsFfmpeg:
    def test_returns_probe_payload(self, tmp_path: Path):
        h = _handler(tmp_path)
        payload = {
            "ok": False,
            "ffmpeg": None,
            "ffprobe": None,
            "missing": ["ffmpeg", "ffprobe"],
            "detail": "未找到 ffmpeg、ffprobe。…",
        }
        with patch("clio.ui.routes.deps.probe_ffmpeg_deps", return_value=payload) as probe:
            handle_get_deps_ffmpeg(h, {})
        probe.assert_called_once_with("", "")
        h._send_json.assert_called_once_with(payload)

    def test_uses_config_paths(self, tmp_path: Path):
        h = _handler(tmp_path)
        ff = tmp_path / "ffmpeg.exe"
        fp = tmp_path / "ffprobe.exe"
        ff.write_bytes(b"x")
        fp.write_bytes(b"x")
        h._get_config.return_value = SimpleNamespace(paths=SimpleNamespace(ffmpeg=str(ff), ffprobe=str(fp)))
        with patch("clio.ui.routes.deps.probe_ffmpeg_deps") as probe:
            probe.return_value = {
                "ok": True,
                "ffmpeg": str(ff),
                "ffprobe": str(fp),
                "missing": [],
                "detail": "",
            }
            handle_get_deps_ffmpeg(h, {})
        probe.assert_called_once_with(str(ff), str(fp))


class TestHandleGetDepsKeys:
    """B-1: /api/deps/keys reports providers lacking a resolved API key (no values)."""

    def test_reports_missing_keys_without_env(self, tmp_path: Path, monkeypatch):
        h = _handler(tmp_path)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        h._get_config.return_value = _cfg(
            providers={
                "deepseek": SimpleNamespace(api_key="", api_key_env="DEEPSEEK_API_KEY"),
                "gemini": SimpleNamespace(api_key="", api_key_env="GEMINI_API_KEY"),
            },
            tasks={
                "voiceover": SimpleNamespace(provider="deepseek"),
                "vlog_plan": SimpleNamespace(provider="deepseek"),
                "video_analyze": SimpleNamespace(provider="gemini"),
            },
        )
        handle_get_deps_keys(h, {})
        payload = h._send_json.call_args[0][0]
        assert payload["ok"] is True
        names = {m["provider"] for m in payload["missing"]}
        assert names == {"deepseek", "gemini"}
        envs = {m["api_key_env"] for m in payload["missing"]}
        assert envs == {"DEEPSEEK_API_KEY", "GEMINI_API_KEY"}
        for m in payload["missing"]:
            assert "密钥" not in m.get("detail", "") or "缺少" in m["detail"]
            assert "sk-" not in str(m)

    def test_omits_provider_with_resolved_env_key(self, tmp_path: Path, monkeypatch):
        h = _handler(tmp_path)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-resolved")
        h._get_config.return_value = _cfg(
            providers={"deepseek": SimpleNamespace(api_key="", api_key_env="DEEPSEEK_API_KEY")},
            tasks={"voiceover": SimpleNamespace(provider="deepseek")},
        )
        handle_get_deps_keys(h, {})
        payload = h._send_json.call_args[0][0]
        assert payload["missing"] == []

    def test_reports_inline_api_key_as_present(self, tmp_path: Path):
        h = _handler(tmp_path)
        h._get_config.return_value = _cfg(
            providers={"deepseek": SimpleNamespace(api_key="sk-inline", api_key_env="")},
            tasks={"voiceover": SimpleNamespace(provider="deepseek")},
        )
        handle_get_deps_keys(h, {})
        payload = h._send_json.call_args[0][0]
        assert payload["missing"] == []

    def test_reports_referenced_but_undeclared_provider(self, tmp_path: Path):
        h = _handler(tmp_path)
        h._get_config.return_value = _cfg(
            providers={},
            tasks={"voiceover": SimpleNamespace(provider="nope")},
        )
        handle_get_deps_keys(h, {})
        payload = h._send_json.call_args[0][0]
        assert any(m["provider"] == "nope" for m in payload["missing"])

    def test_falls_back_to_global_providers_when_no_tasks(self, tmp_path: Path, monkeypatch):
        """B-1 first-launch: with no project/tasks, surface globally declared providers."""
        h = _handler(tmp_path)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        h._get_config.return_value = _cfg(
            providers={
                "deepseek": SimpleNamespace(api_key="", api_key_env="DEEPSEEK_API_KEY"),
                "gemini": SimpleNamespace(api_key="", api_key_env="GEMINI_API_KEY"),
            },
            tasks={},
        )
        handle_get_deps_keys(h, {})
        payload = h._send_json.call_args[0][0]
        names = {m["provider"] for m in payload["missing"]}
        assert names == {"deepseek", "gemini"}

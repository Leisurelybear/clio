"""Tests for clio/ui/routes/config_routes.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from clio.ui.routes.config_routes import (
    _merge_project_with_defaults,
    handle_get_config,
    handle_get_config_global,
    handle_get_config_project,
    handle_get_config_raw,
    handle_post_config_init,
    handle_put_config_global,
    handle_put_config_project,
    handle_put_config_raw,
)


class TestHandleGetConfig:
    def test_basic(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        proj_out.mkdir()
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        cfg = MagicMock()
        cfg.compressed_dir = proj_out / "compressed"
        cfg.texts_dir = proj_out / "texts"
        cfg.scripts_dir = proj_out / "scripts"
        cfg.plans_dir = proj_out / "plans"
        cfg.analyze = MagicMock(texts_subdir="texts")
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()

        handle_get_config(handler, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert "project_dir" in payload
        assert "input_dir" not in payload
        assert "output_dir" in payload
        assert "compressed_dir" in payload
        assert payload["scripts_dir"] == str(cfg.scripts_dir)
        assert payload["plans_dir"] == str(cfg.plans_dir)

    def test_honors_custom_subdirs(self, tmp_path: Path):
        handler = MagicMock()
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()
        proj_out = tmp_path / "output"
        custom_texts = proj_out / "notes"
        custom_texts.mkdir(parents=True)
        handler._resolve_project_dir.return_value = proj_dir
        handler._get_project_output.return_value = proj_out
        cfg = MagicMock()
        cfg.compressed_dir = proj_out / "comp"
        cfg.texts_dir = custom_texts
        cfg.scripts_dir = proj_out / "vo"
        cfg.plans_dir = proj_out / "storyboard"
        cfg.analyze = MagicMock(texts_subdir="notes")
        handler._get_config.return_value = cfg
        handler._send_json = MagicMock()

        handle_get_config(handler, {})

        payload = handler._send_json.call_args[0][0]
        assert payload["compressed_dir"] == str(cfg.compressed_dir)
        assert payload["scripts_dir"] == str(cfg.scripts_dir)
        assert payload["plans_dir"] == str(cfg.plans_dir)
        assert str(custom_texts) in payload["texts_dirs"]


class TestHandleGetConfigRaw:
    def test_needs_init(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"key: val\n")
        # Without project.yaml in a non-default project dir
        proj_dir = tmp_path / "custom_project"
        proj_dir.mkdir()
        default_input = tmp_path / "input"
        default_input.mkdir()

        handler.config_path = cfg
        handler.project_dir = default_input
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()

        handle_get_config_raw(handler, {})

        handler._send_json.assert_called_once_with({"needs_init": True})

    def test_returns_merged_config(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"compress": {"target_size_mb": 5, "max_width": 640}}), encoding="utf-8")
        proj_dir = tmp_path / "default"
        proj_dir.mkdir()

        handler.config_path = cfg
        handler.project_dir = proj_dir
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()

        handle_get_config_raw(handler, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert payload["compress"]["target_size_mb"] == 5
        assert payload.get("_config_source") == "global_fallback"
        # Default ai.context should be set
        assert payload.get("ai", {}).get("context") == ""


class TestPreviewSectionRoundTrip:
    def test_merge_preserves_preview(self):
        merged = _merge_project_with_defaults(
            {
                "preview": {"subtitles": {"mode": "multi", "pos_x": 20}},
            }
        )
        assert merged.get("preview", {}).get("subtitles", {}).get("mode") == "multi"
        assert merged["preview"]["subtitles"]["pos_x"] == 20

    def test_get_config_project_includes_preview_defaults(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        proj_dir = tmp_path / "default"
        proj_dir.mkdir()
        (proj_dir / "project.yaml").write_text(
            yaml.dump({"preview": {"subtitles": {"font_size": 18}}}), encoding="utf-8"
        )
        handler.config_path = cfg
        handler.project_dir = proj_dir
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()

        handle_get_config_project(handler, {})

        payload = handler._send_json.call_args[0][0]
        assert payload["preview"]["subtitles"]["font_size"] == 18
        # defaults merged for absent fields
        assert payload["preview"]["subtitles"]["mode"] == "auto"


class TestHandlePostConfigInit:
    def test_default_project_no_init_needed(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        proj_dir = tmp_path / "input"

        handler.config_path = cfg
        handler.project_dir = proj_dir
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        handler.__class__._config_cache = MagicMock()

        handle_post_config_init(handler, {}, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 400


class TestHandlePutConfigRaw:
    def test_no_config_path(self):
        handler = MagicMock()
        handler.config_path = None
        handler._send_json = MagicMock()
        handle_put_config_raw(handler, {}, {"test": True})
        handler._send_json.assert_called_once_with({"ok": False, "error": "config_path not available"}, 500)

    def test_put_project_config(self, tmp_path: Path):
        """Writing to a non-default project directory stores in project.yaml."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text(yaml.dump({"paths": {"input_dir": "./input", "output_dir": "./output"}}), encoding="utf-8")
        proj_dir = tmp_path / "custom"
        proj_dir.mkdir()

        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        handle_put_config_raw(handler, {}, {"compress": {"target_size_mb": 10}})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True
        proj_yaml = proj_dir / "project.yaml"
        assert proj_yaml.is_file()
        data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
        assert data["compress"]["target_size_mb"] == 10

    def test_put_global_validation_failure_backup(self, tmp_path: Path):
        """Invalid config (proxy.enabled without url) restores original."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.dump(
                {
                    "paths": {"input_dir": "./in", "output_dir": "./out"},
                    "proxy": {"enabled": False, "url": ""},
                }
            ),
            encoding="utf-8",
        )

        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = tmp_path
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        # Send config with proxy.enabled=true but no url (fails validation)
        handle_put_config_raw(handler, {}, {"proxy": {"enabled": True, "url": ""}})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is False
        # Original content preserved
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["proxy"]["enabled"] is False

    def test_put_config_coerces_types(self, tmp_path: Path):
        """Values are coerced to match reference config types."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.dump(
                {
                    "proxy": {"enabled": False},
                    "compress": {"fps": 15, "codec": "libx264"},
                }
            ),
            encoding="utf-8",
        )

        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = tmp_path
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        # Send string "30" that should be coerced to int via ref_raw type
        handle_put_config_raw(handler, {}, {"compress": {"fps": "30"}})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert isinstance(data["compress"]["fps"], int)

    def test_put_config_includes_descriptions_in_raw_response(self, tmp_path: Path):
        """handle_get_config_raw response includes _descriptions field."""
        from clio.config.descriptions import CONFIG_DESCRIPTIONS

        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"compress": {"target_size_mb": 5}}), encoding="utf-8")
        proj_dir = tmp_path / "default"
        proj_dir.mkdir()

        handler.config_path = cfg
        handler.project_dir = proj_dir
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()

        handle_get_config_raw(handler, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        payload = args[0][0]
        assert "_descriptions" in payload
        assert payload["_descriptions"]["compress.target_size_mb"] == CONFIG_DESCRIPTIONS["compress.target_size_mb"]

    def test_post_config_init_creates_project_yaml(self, tmp_path: Path):
        """handle_post_config_init creates valid project.yaml for non-default project."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.dump({"paths": {"input_dir": "./input", "output_dir": "./output"}, "compress": {"target_size_mb": 5}}),
            encoding="utf-8",
        )
        proj_dir = tmp_path / "custom"
        proj_dir.mkdir()

        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        handler.__class__._config_cache = MagicMock()

        handle_post_config_init(handler, {}, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True
        proj_yaml = proj_dir / "project.yaml"
        assert proj_yaml.is_file()
        data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
        assert data["compress"]["target_size_mb"] == 5

    def test_post_config_init_rejects_default(self, tmp_path: Path):
        """Default project cannot be initialized with project.yaml."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        proj_dir = tmp_path / "input"
        proj_dir.mkdir()

        handler.config_path = cfg
        handler.project_dir = proj_dir
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        handler.__class__._config_cache = MagicMock()

        handle_post_config_init(handler, {}, {})

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 400


# ===================== Per-layer GET/PUT tests =====================


class TestHandleGetConfigGlobal:
    """GET /api/config/global — returns global-only fields."""

    def test_no_config_file(self):
        handler = MagicMock()
        handler.config_path = None
        handler._send_json = MagicMock()
        handle_get_config_global(handler, {})
        handler._send_json.assert_called_once_with({"error": "config file not available"}, 500)

    def test_strips_project_only_split_fields(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            yaml.dump(
                {
                    "paths": {
                        "ffmpeg": "ffmpeg",
                        "ffprobe": "ffprobe",
                        "logs_dir": "./logs",
                        "input_dir": "./input",
                        "output_dir": "./output",
                    },
                    "ai": {
                        "providers": {"test": {"type": "openai"}},
                        "debug_print_prompt": True,
                        "tasks": {"analyze": {"provider": "test", "model": "gpt-4"}},
                    },
                    "compress": {"codec": "libx264", "fps": 15, "target_size_mb": 5},
                    "whisper": {"cache_dir": "/cache", "hf_endpoint": "", "model_size": "large", "enabled": True},
                    "proxy": {"enabled": False, "url": ""},
                    "naming": {"index_width": 2},
                }
            ),
            encoding="utf-8",
        )
        handler.config_path = cfg
        handler._send_json = MagicMock()
        handle_get_config_global(handler, {})
        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args[0][0]
        assert "proxy" in payload
        assert payload["proxy"]["enabled"] is False
        assert "naming" in payload
        assert payload["paths"]["ffmpeg"] == "ffmpeg"
        assert payload["paths"]["logs_dir"] == "./logs"
        assert "providers" in payload["ai"]
        assert payload["ai"]["debug_print_prompt"] is True
        assert payload["compress"]["codec"] == "libx264"
        assert payload["compress"]["fps"] == 15
        assert payload["whisper"]["cache_dir"] == "/cache"
        assert payload["whisper"]["hf_endpoint"] == ""
        assert "input_dir" not in payload["paths"]
        assert "output_dir" not in payload["paths"]
        assert "tasks" not in payload["ai"]
        assert "target_size_mb" not in payload["compress"]
        assert "model_size" not in payload["whisper"]
        assert "enabled" not in payload["whisper"]

    def test_pure_global_sections_unchanged(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"proxy": {"enabled": True, "url": "socks5://localhost:1080"}}), encoding="utf-8")
        handler.config_path = cfg
        handler._send_json = MagicMock()
        handle_get_config_global(handler, {})
        payload = handler._send_json.call_args[0][0]
        assert payload["proxy"]["enabled"] is True
        assert payload["proxy"]["url"] == "socks5://localhost:1080"


class TestHandleGetConfigProject:
    """GET /api/config/project — returns project-only fields."""

    def test_no_config_path(self):
        handler = MagicMock()
        handler.config_path = None
        handler._send_json = MagicMock()
        handle_get_config_project(handler, {})
        handler._send_json.assert_called_once_with({"error": "config file not available"}, 500)

    def test_needs_init(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        proj_dir = tmp_path / "custom"
        proj_dir.mkdir()
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        handle_get_config_project(handler, {})
        handler._send_json.assert_called_once_with({"needs_init": True})

    def test_no_project_yaml_returns_empty(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = tmp_path
        handler._send_json = MagicMock()
        handle_get_config_project(handler, {})
        handler._send_json.assert_called_once_with({})

    def test_returns_project_only_fields(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        proj_yaml = proj_dir / "project.yaml"
        proj_yaml.write_text(
            yaml.dump(
                {
                    "paths": {
                        "input_dir": "./in",
                        "output_dir": "./out",
                        "ffmpeg": "C:/ffmpeg.exe",
                        "logs_dir": "./logs",
                    },
                    "ai": {
                        "tasks": {"analyze": {"provider": "gemini"}},
                        "context": "my trip",
                        "providers": {"gemini": {"type": "gemini"}},
                    },
                    "compress": {"target_size_mb": 10, "max_width": 1280, "codec": "libx265", "fps": 30},
                    "whisper": {
                        "enabled": True,
                        "model_size": "large",
                        "cache_dir": "/cache",
                        "hf_endpoint": "https://hf-mirror.com",
                    },
                    "analyze": {"skip_existing": True},
                    "script": {"target_words": 300},
                    "plan": {"max_clips_per_day": 5},
                    "export": {"canvas_ratio": "9:16"},
                }
            ),
            encoding="utf-8",
        )
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        handle_get_config_project(handler, {})
        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args[0][0]
        assert "input_dir" not in payload.get("paths", {})
        assert "recursive" not in payload.get("paths", {})
        assert payload["paths"]["output_dir"] == "./out"
        assert payload["ai"]["tasks"]["analyze"]["provider"] == "gemini"
        assert payload["ai"]["context"] == "my trip"
        assert payload["compress"]["target_size_mb"] == 10
        assert payload["compress"]["max_width"] == 1280
        assert payload["whisper"]["enabled"] is True
        assert payload["whisper"]["model_size"] == "large"
        assert payload["analyze"]["skip_existing"] is True
        assert payload["script"]["target_words"] == 300
        assert payload["plan"]["max_clips_per_day"] == 5
        assert payload["export"]["canvas_ratio"] == "9:16"
        assert "ffmpeg" not in payload["paths"]
        assert "logs_dir" not in payload["paths"]
        assert "providers" not in payload["ai"]
        assert "codec" not in payload["compress"]
        assert "fps" not in payload["compress"]
        assert "cache_dir" not in payload["whisper"]
        assert "hf_endpoint" not in payload["whisper"]

    def test_fills_missing_plan_defaults(self, tmp_path: Path):
        """project.yaml without plan section still returns plan defaults for UI."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        (proj_dir / "project.yaml").write_text(
            yaml.dump(
                {
                    "paths": {"output_dir": "./out"},
                    "ai": {"tasks": {}, "context": ""},
                }
            ),
            encoding="utf-8",
        )
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler._send_json = MagicMock()
        handle_get_config_project(handler, {})
        payload = handler._send_json.call_args[0][0]
        assert "plan" in payload
        assert payload["plan"]["max_clips_per_day"] == 12
        assert payload["plan"]["target_duration_sec"] == 180
        assert payload["plan"]["use_transcripts"] is True
        assert "analyze" in payload
        assert "script" in payload
        assert "export" in payload
        assert payload["paths"]["output_dir"] == "./out"


class TestHandlePutConfigGlobal:
    """PUT /api/config/global — writes to config.yaml, rejects project fields."""

    def test_no_config_path(self):
        handler = MagicMock()
        handler.config_path = None
        handler._send_json = MagicMock()
        handle_put_config_global(handler, {}, {"proxy": {"enabled": True}})
        handler._send_json.assert_called_once_with({"ok": False, "error": "config_path not available"}, 500)

    def test_rejects_project_fields(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"proxy": {"enabled": False}}), encoding="utf-8")
        handler.config_path = cfg
        handler._send_json = MagicMock()
        handle_put_config_global(handler, {}, {"proxy": {"enabled": True}, "analyze": {"skip_existing": True}})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 400

    def test_writes_to_config_yaml(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"proxy": {"enabled": False, "url": ""}}), encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_put_config_global(handler, {}, {"proxy": {"enabled": True, "url": "socks5://localhost:1080"}})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["proxy"]["enabled"] is True
        assert data["proxy"]["url"] == "socks5://localhost:1080"

    def test_preserves_existing_fields(self, tmp_path: Path):
        """PUT with only proxy section — naming is replaced since payload is the full write."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"proxy": {"enabled": False}, "naming": {"index_width": 3}}), encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_put_config_global(handler, {}, {"proxy": {"enabled": True, "url": "socks5://localhost:1080"}})
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert data["proxy"]["enabled"] is True
        assert data["proxy"]["url"] == "socks5://localhost:1080"

    def test_strips_masked_api_key_on_write(self, tmp_path: Path):
        """UI round-trip: GET masks keys as ********; PUT must not persist that mask (R-033b)."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        on_disk = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {
                "providers": {
                    "gemini": {
                        "type": "gemini",
                        "api_key": "sk-live-real-secret",
                        "api_key_env": "GEMINI_API_KEY",
                        "models": ["gemini-2.5-flash"],
                    }
                }
            },
        }
        cfg.write_text(yaml.dump(on_disk), encoding="utf-8")
        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        # Body as returned by masked GET (and what editor-plan.js re-PUTs)
        body = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {
                "providers": {
                    "gemini": {
                        "type": "gemini",
                        "api_key": "********",
                        "api_key_env": "GEMINI_API_KEY",
                        "models": ["gemini-2.5-flash"],
                    }
                }
            },
        }
        handle_put_config_global(handler, {}, body)

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0].get("ok") is True, args
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        stored = data["ai"]["providers"]["gemini"].get("api_key", "")
        assert stored != "********"

    def test_global_write_breaking_registered_project_returns_409(self, tmp_path: Path):
        """A global change that invalidates a registered project's task binding
        must be rejected with 409 and the affected project listed (GAP-P1-08)."""
        import json

        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        on_disk = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {
                "providers": {
                    "gemini": {"type": "gemini", "api_key_env": "GEMINI_API_KEY"},
                    "deepseek": {"type": "openai", "api_key_env": "DEEPSEEK_API_KEY"},
                }
            },
        }
        cfg.write_text(yaml.dump(on_disk), encoding="utf-8")

        # Registered project binding voiceover to deepseek
        proj = tmp_path / "paris"
        proj.mkdir()
        (proj / "project.yaml").write_text(
            "ai:\n  tasks:\n    voiceover:\n      provider: deepseek\n      model: deepseek-chat\n",
            encoding="utf-8",
        )
        (tmp_path / "projects.json").write_text(json.dumps({"projects": [str(proj.resolve())]}), encoding="utf-8")

        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        # New global config drops the deepseek provider
        body = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {"providers": {"gemini": {"type": "gemini", "api_key_env": "GEMINI_API_KEY"}}},
        }
        handle_put_config_global(handler, {}, body)

        args = handler._send_json.call_args
        assert args[0][1] == 409
        assert args[0][0]["ok"] is False
        assert len(args[0][0]["affected"]) == 1
        assert args[0][0]["affected"][0]["project"] == str(proj.resolve())
        # Global config must NOT be modified
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        assert "deepseek" in data["ai"]["providers"]

    def test_global_write_keeps_provider_used_by_registered_project(self, tmp_path: Path):
        """Keeping the provider referenced by a registered project succeeds."""
        import json

        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        on_disk = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {
                "providers": {
                    "gemini": {"type": "gemini", "api_key_env": "GEMINI_API_KEY"},
                    "deepseek": {"type": "openai", "api_key_env": "DEEPSEEK_API_KEY"},
                }
            },
        }
        cfg.write_text(yaml.dump(on_disk), encoding="utf-8")

        proj = tmp_path / "paris"
        proj.mkdir()
        (proj / "project.yaml").write_text(
            "ai:\n  tasks:\n    voiceover:\n      provider: deepseek\n      model: deepseek-chat\n",
            encoding="utf-8",
        )
        (tmp_path / "projects.json").write_text(json.dumps({"projects": [str(proj.resolve())]}), encoding="utf-8")

        handler.config_path = cfg
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        body = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {
                "providers": {
                    "gemini": {"type": "gemini", "api_key_env": "GEMINI_API_KEY"},
                    "deepseek": {"type": "openai", "api_key_env": "DEEPSEEK_API_KEY"},
                }
            },
        }
        handle_put_config_global(handler, {}, body)

        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True, args


class TestHandlePutConfigRawApiKey:
    def test_put_raw_global_strips_masked_api_key(self, tmp_path: Path):
        """PUT /api/config/raw global branch must also strip masked api_key (R-033b)."""
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        on_disk = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {
                "providers": {
                    "gemini": {
                        "type": "gemini",
                        "api_key": "sk-live-real-secret",
                        "api_key_env": "GEMINI_API_KEY",
                        "models": ["gemini-2.5-flash"],
                    }
                }
            },
        }
        cfg.write_text(yaml.dump(on_disk), encoding="utf-8")
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = tmp_path
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()

        body = {
            "proxy": {"enabled": False, "url": ""},
            "ai": {
                "providers": {
                    "gemini": {
                        "type": "gemini",
                        "api_key": "********",
                        "api_key_env": "GEMINI_API_KEY",
                        "models": ["gemini-2.5-flash"],
                    }
                }
            },
        }
        handle_put_config_raw(handler, {}, body)

        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0].get("ok") is True, args
        data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        stored = data["ai"]["providers"]["gemini"].get("api_key", "")
        assert stored != "********"
        assert stored != "sk-live-real-secret"
        assert stored in ("", None)


class TestHandlePutConfigProject:
    """PUT /api/config/project — writes to project.yaml, rejects global fields."""

    def test_no_config_path(self):
        handler = MagicMock()
        handler.config_path = None
        handler._send_json = MagicMock()
        handle_put_config_project(handler, {}, {"paths": {"input_dir": "./in"}})
        handler._send_json.assert_called_once_with({"ok": False, "error": "config_path not available"}, 500)

    def test_rejects_global_fields(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_bytes(b"")
        proj_dir = tmp_path / "project"
        proj_dir.mkdir()
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_put_config_project(handler, {}, {"paths": {"input_dir": "./in"}, "proxy": {"enabled": True}})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][1] == 400

    def test_creates_project_yaml_when_missing(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"paths": {"input_dir": "./in", "output_dir": "./out"}}), encoding="utf-8")
        proj_dir = tmp_path / "custom"
        proj_dir.mkdir()
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_put_config_project(handler, {}, {"compress": {"target_size_mb": 10}})
        handler._send_json.assert_called_once()
        args = handler._send_json.call_args
        assert args[0][0]["ok"] is True
        proj_yaml = proj_dir / "project.yaml"
        assert proj_yaml.is_file()
        data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
        assert data["compress"]["target_size_mb"] == 10

    def test_updates_existing_project_yaml(self, tmp_path: Path):
        handler = MagicMock()
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.dump({"paths": {"input_dir": "./in", "output_dir": "./out"}}), encoding="utf-8")
        proj_dir = tmp_path / "project2"
        proj_dir.mkdir()
        proj_yaml = proj_dir / "project.yaml"
        proj_yaml.write_text(yaml.dump({"compress": {"target_size_mb": 5}}), encoding="utf-8")
        handler.config_path = cfg
        handler.project_dir = tmp_path
        handler._resolve_project_dir.return_value = proj_dir
        handler.__class__._config_cache = MagicMock()
        handler._send_json = MagicMock()
        handle_put_config_project(handler, {}, {"compress": {"max_width": 1920}})
        data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8"))
        assert data["compress"]["target_size_mb"] == 5
        assert data["compress"]["max_width"] == 1920

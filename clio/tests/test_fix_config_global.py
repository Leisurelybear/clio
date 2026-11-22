"""Regression tests: PUT /api/config/global must not drop project-layer fields."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from clio.ui.routes.config_routes import _merge_global_layer, handle_put_config_global


def test_merge_keeps_hidden_fields_and_client_owned_deletions():
    ref = {
        "ui": {"language": "zh-CN"},
        "paths": {"output_dir": "./output", "ffmpeg": "ffmpeg"},
        "whisper": {"model_size": "small", "hf_endpoint": ""},
        "analyze": {"texts_subdir": "analysis"},
        "proxy": {"enabled": False},
    }
    client = {"ui": {"language": "en"}, "paths": {"ffmpeg": "/usr/bin/ffmpeg"}, "whisper": {"hf_endpoint": "x"}}
    merged = _merge_global_layer(ref, client)
    assert merged["ui"] == {"language": "en"}
    assert merged["paths"] == {"output_dir": "./output", "ffmpeg": "/usr/bin/ffmpeg"}
    assert merged["whisper"] == {"model_size": "small", "hf_endpoint": "x"}
    assert merged["analyze"] == {"texts_subdir": "analysis"}
    assert "proxy" not in merged  # exposed by GET, dropped by the client
    assert list(merged) == ["ui", "paths", "whisper", "analyze"]


def test_language_switch_keeps_project_fields_in_config_yaml(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "ui": {"language": "zh-CN"},
                "paths": {"output_dir": "./output"},
                "compress": {"target_size_mb": 50},
                "analyze": {"skip_existing": True},
            }
        ),
        encoding="utf-8",
    )
    handler = MagicMock()
    handler.config_path = cfg
    handler.__class__._config_cache = MagicMock()
    handler._send_json = MagicMock()

    handle_put_config_global(handler, {}, {"ui": {"language": "zh-CN"}})

    assert handler._send_json.call_args.args[0]["ok"] is True
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["paths"]["output_dir"] == "./output"
    assert data["compress"]["target_size_mb"] == 50
    assert data["analyze"]["skip_existing"] is True

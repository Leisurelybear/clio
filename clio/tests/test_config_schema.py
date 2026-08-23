"""Tests for clio.config.schema: field grouping, UI types, and codec validation."""

import pytest

from clio.config.models import GlobalCompressConfig
from clio.config.schema import ADVANCED, BASIC, HIDDEN, build_config_schema


def _all_fields(layer: str) -> dict[str, dict]:
    schema = build_config_schema()
    out = {}
    for sec in schema[layer]:
        for f in sec["fields"]:
            out[f["path"]] = f
    return out


class TestConfigSchema:
    def test_project_has_expected_sections(self):
        schema = build_config_schema()
        keys = [s["key"] for s in schema["project"]]
        assert "paths" in keys
        assert "compress" in keys
        assert "export" in keys

    def test_global_has_expected_sections(self):
        schema = build_config_schema()
        keys = [s["key"] for s in schema["global"]]
        assert "proxy" in keys
        assert "server" in keys
        assert "compress" in keys

    def test_canvas_ratio_is_basic_select(self):
        fields = _all_fields("project")
        f = fields["export.canvas_ratio"]
        assert f["group"] == BASIC
        assert f["ui"] == "select"
        assert "16:9" in f["choices"]

    def test_whisper_language_is_basic_select(self):
        fields = _all_fields("project")
        f = fields["whisper.language"]
        assert f["group"] == BASIC
        assert f["ui"] == "select"

    def test_max_width_is_basic_select_or_number(self):
        fields = _all_fields("project")
        f = fields["compress.max_width"]
        assert f["group"] == BASIC
        assert f["ui"] == "select_or_number"
        assert 640 in f["choices"]

    def test_analyze_window_is_advanced(self):
        fields = _all_fields("project")
        assert fields["analyze.window_max_min"]["group"] == ADVANCED

    def test_preview_subtitles_hidden(self):
        fields = _all_fields("project")
        assert fields["preview.subtitles.enabled"]["group"] == HIDDEN
        assert fields["preview.subtitles.font_size"]["group"] == HIDDEN

    def test_compress_codec_is_global_select(self):
        fields = _all_fields("global")
        f = fields["compress.codec"]
        assert f["group"] == ADVANCED
        assert f["ui"] == "select"
        assert f["choices"] == ["libx264", "libx265"]

    def test_proxy_url_visible_when_enabled(self):
        fields = _all_fields("global")
        f = fields["proxy.url"]
        assert f["visible_when"] == {"field": "proxy.enabled", "equals": True}

    def test_schema_is_cached(self):
        assert build_config_schema() is build_config_schema()


class TestCodecValidation:
    def test_accepts_libx264(self):
        GlobalCompressConfig(codec="libx264")  # no exception = valid

    def test_rejects_unknown_codec(self):
        from clio.config.validators import _require_choice

        with pytest.raises(ValueError, match="codec"):
            _require_choice("compress.codec", "mpeg4", ("libx264", "libx265"))

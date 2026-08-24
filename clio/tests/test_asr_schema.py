from __future__ import annotations

from unittest.mock import MagicMock, patch

from clio.asr.base import ProviderCapabilities
from clio.config.schema import build_config_schema


def test_schema_engine_choices_injected_by_route():
    from clio.ui.routes.config_routes import _inject_asr_choices

    caps_list = [
        ProviderCapabilities(id="local", display_name="Local"),
        ProviderCapabilities(id="aliyun", display_name="Aliyun"),
    ]
    schema = build_config_schema()
    result = _inject_asr_choices(schema, caps_list)
    for layer in ("project", "global"):
        for section in result.get(layer, []):
            for field in section.get("fields", []):
                if field.get("path") == "whisper.engine":
                    assert field["choices"] == ["local", "aliyun"]


def test_handler_calls_injection():
    from clio.ui.routes.config_routes import handle_get_config_schema

    handler = MagicMock()
    with (
        patch("clio.ui.routes.config_routes.build_config_schema") as mock_build,
        patch("clio.ui.routes.config_routes._inject_asr_choices", wraps=None) as mock_inject,
    ):
        mock_build.return_value = {"project": [], "global": []}
        mock_inject.side_effect = lambda s, c: s
        handle_get_config_schema(handler, {})
    handler._send_json.assert_called_once()


def test_engine_choices_default_to_empty_before_injection():
    schema = build_config_schema()
    engine_fields = [
        field
        for layer in ("project", "global")
        for section in schema[layer]
        for field in section["fields"]
        if field["path"] == "whisper.engine"
    ]
    assert engine_fields
    assert all("choices" not in field for field in engine_fields)


def test_cloud_legacy_config_is_resolved_with_notice(capsys):
    from clio.config.loader import _resolve_engine

    assert _resolve_engine({"whisper": {"engine": "cloud", "cloud_provider": "aliyun"}}) == "aliyun"
    assert _resolve_engine({"whisper": {"engine": "cloud"}}) == "local"
    output = capsys.readouterr().out
    assert "已迁移" in output

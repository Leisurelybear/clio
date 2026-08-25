"""Tests for clio/config/descriptions.py — schema integrity checks."""

from __future__ import annotations

import re

from clio.config.descriptions import CONFIG_DESCRIPTIONS
from clio.config.models import (
    AIConfig,
    AnalyzeConfig,
    CompressConfig,
    NamingConfig,
    PathsConfig,
    PlanConfig,
    PreviewSubtitlesConfig,
    ProviderConfig,
    ProxyConfig,
    ScriptConfig,
    TaskConfig,
    WhisperConfig,
)

_KEY_PATTERN = re.compile(r"\{name\}")

# Valid description keys that don't map to a specific dataclass field
_EXTRA_DESCRIPTION_KEYS = {"paths.project_dir", "whisper.engine"}

# Map of config field name → dataclass type (hardcoded to avoid annotation resolution issues)
_CONFIG_DC_MAP: list[tuple[str, type]] = [
    ("paths", PathsConfig),
    ("proxy", ProxyConfig),
    ("ai", AIConfig),
    ("compress", CompressConfig),
    ("analyze", AnalyzeConfig),
    ("naming", NamingConfig),
    ("script", ScriptConfig),
    ("plan", PlanConfig),
    ("whisper", WhisperConfig),
    ("preview.subtitles", PreviewSubtitlesConfig),
]


def _all_dc_fields() -> list[tuple[str, list[str]]]:
    """Return (prefix, field_names) for each sub-dataclass in AppConfig."""
    return [(prefix, list(dc_type.__dataclass_fields__)) for prefix, dc_type in _CONFIG_DC_MAP]


def _flatten_descriptions_keys() -> set[str]:
    """Return all concrete (non-pattern) description keys."""
    return {k for k in CONFIG_DESCRIPTIONS if not _KEY_PATTERN.search(k)}


def _pattern_keys() -> set[str]:
    """Return description keys that contain {name} pattern."""
    return {k for k in CONFIG_DESCRIPTIONS if _KEY_PATTERN.search(k)}


class TestSchemaCoverage:
    def test_all_model_fields_have_descriptions(self):
        """Every field in every config dataclass has a description entry (or pattern)."""
        flat = _flatten_descriptions_keys()
        pattern = _pattern_keys()
        for prefix, field_names in _all_dc_fields():
            for name in field_names:
                expected_key = f"{prefix}.{name}"
                # Check direct match or pattern match
                assert expected_key in flat or any(
                    expected_key.startswith(p.replace("{name}.", "").replace("{name}", "")) for p in pattern
                ), f"Missing description for {expected_key}"

    def test_no_extra_descriptions(self):
        """Every concrete description key maps to an actual dataclass field."""
        flat = _flatten_descriptions_keys()
        field_set: set[str] = set()
        for prefix, field_names in _all_dc_fields():
            for name in field_names:
                field_set.add(f"{prefix}.{name}")
        for key in flat:
            assert key in field_set or key in _EXTRA_DESCRIPTION_KEYS, f"Description key {key} does not match any field"

    def test_descriptions_non_empty(self):
        """All descriptions must have non-empty text content."""
        for key, desc in CONFIG_DESCRIPTIONS.items():
            assert desc and desc.strip(), f"Empty description for {key}"

    def test_no_sensitive_info_in_descriptions(self):
        """Descriptions must not contain API keys, local paths, or IPs."""
        sensitive_patterns = [
            re.compile(r"sk-[a-zA-Z0-9]+", re.IGNORECASE),
            re.compile(r"AIza[0-9A-Za-z_-]+"),
            re.compile(r"[A-Za-z]:\\"),  # Windows drive letter paths
            re.compile(r"/home/"),  # Unix home paths
            re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"),  # IP addresses
        ]
        for key, desc in CONFIG_DESCRIPTIONS.items():
            for pat in sensitive_patterns:
                assert not pat.search(desc), f"Sensitive content in description for {key}: {desc}"

    def test_pattern_keys_are_plausible(self):
        """{name} pattern keys should match likely dynamic paths from models."""
        patterns = _pattern_keys()
        # ai.providers.{name}.type -> check ProviderConfig fields
        prov_fields = set(ProviderConfig.__dataclass_fields__)
        for pkey in patterns:
            if "providers.{name}" in pkey:
                attr = pkey.split(".{name}.")[-1]
                assert attr in prov_fields, f"Pattern {pkey} doesn't match ProviderConfig field"
            if "tasks.{name}" in pkey:
                attr = pkey.split(".{name}.")[-1]
                assert attr in TaskConfig.__dataclass_fields__, f"Pattern {pkey} doesn't match TaskConfig field"

"""Regression tests: empty YAML sections (``proxy:``) load as defaults, not tracebacks."""

from __future__ import annotations

from pathlib import Path

import pytest

from clio.config.loader import load_global_config, load_project_config


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_global_config_with_empty_sections_uses_defaults(tmp_path: Path):
    cfg = _write(
        tmp_path / "config.yaml",
        "config_version: V2\nproxy:\nserver:\nnaming:\nui:\npaths:\nwhisper:\ncompress:\nai:\n",
    )

    gc = load_global_config(cfg)

    assert gc.proxy.enabled is False
    assert gc.ai.providers == {}
    assert gc.compress.codec == "libx264"


def test_empty_provider_and_task_sections_use_defaults(tmp_path: Path):
    cfg = _write(tmp_path / "config.yaml", "config_version: V2\nai:\n  providers:\n    gemini:\n  default_tasks:\n")

    gc = load_global_config(cfg)

    assert gc.ai.providers["gemini"].type == "gemini"
    assert gc.ai.default_tasks == {}


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("config_version: V2\nproxy: 5\n", "proxy"),
        ("config_version: V2\nai:\n  providers: [gemini]\n", "ai.providers"),
        ("config_version: V2\nai:\n  providers:\n    gemini: text\n", "ai.providers.gemini"),
    ],
)
def test_non_mapping_section_is_a_readable_error(tmp_path: Path, text: str, field: str):
    cfg = _write(tmp_path / "config.yaml", text)

    with pytest.raises(ValueError, match=f"{field} must be a mapping"):
        load_global_config(cfg)


def test_project_config_with_empty_sections_uses_defaults(tmp_path: Path):
    _write(
        tmp_path / "project.yaml",
        "paths:\nai:\ncompress:\nscript:\nanalyze:\nplan:\nexport:\nwhisper:\npreview:\n  subtitles:\n",
    )

    project = load_project_config(tmp_path)

    assert project is not None
    assert project.paths.output_dir == (tmp_path / "output").resolve()
    assert project.ai.tasks == {}
    assert project.whisper.engine == "local"

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from clio.config import (
    AppConfig,
    GlobalAIConfig,
    GlobalConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProviderConfig,
    ScriptConfig,
)


def _config(project_dir: Path) -> AppConfig:
    template = project_dir / "templates" / "vlog_template.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text("template", encoding="utf-8")
    return AppConfig(
        global_cfg=GlobalConfig(ai=GlobalAIConfig()),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=project_dir / "output"),
            script=ScriptConfig(template_file=template),
        ),
    )


class TestPromptResolver:
    def test_file_override_replaces_builtin_prompt(self, tmp_path: Path):
        from clio.prompt_overrides import resolve_prompt_template

        config = _config(tmp_path)
        prompt_dir = tmp_path / "templates" / "prompts"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "voiceover.md").write_text("Custom {title} {index}", encoding="utf-8")

        result = resolve_prompt_template("voiceover", "Builtin {title}", config)

        assert result == "Custom {title} {index}"

    def test_missing_project_dir_attribute_uses_template_parent(self, tmp_path: Path):
        from clio.prompt_overrides import prompt_override_dir

        template = tmp_path / "templates" / "vlog_template.md"
        config = SimpleNamespace(script=SimpleNamespace(template_file=template))

        assert prompt_override_dir(cast(AppConfig, config)) == tmp_path / "templates" / "prompts"

    def test_runtime_prompt_overrides_file_prompt(self, tmp_path: Path):
        from clio.prompt_overrides import resolve_prompt_template

        config = _config(tmp_path)
        prompt_dir = tmp_path / "templates" / "prompts"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "voiceover.md").write_text("File {title}", encoding="utf-8")

        result = resolve_prompt_template(
            "voiceover",
            "Builtin {title}",
            config,
            task_prompts={"voiceover": "Runtime {title}"},
        )

        assert result == "Runtime {title}"

    def test_runtime_prompt_accepts_pipeline_step_alias(self, tmp_path: Path):
        from clio.prompt_overrides import resolve_prompt_template

        config = _config(tmp_path)

        result = resolve_prompt_template(
            "video_analyze",
            "Builtin",
            config,
            task_prompts={"analyze": "Runtime analyze"},
        )

        assert result == "Runtime analyze"

    def test_empty_file_override_falls_back_to_builtin_prompt(self, tmp_path: Path):
        from clio.prompt_overrides import resolve_prompt_template

        config = _config(tmp_path)
        prompt_dir = tmp_path / "templates" / "prompts"
        prompt_dir.mkdir(parents=True)
        (prompt_dir / "voiceover.md").write_text("  \n", encoding="utf-8")

        result = resolve_prompt_template("voiceover", "Builtin {title}", config)

        assert result == "Builtin {title}"

    def test_missing_required_placeholder_fails_before_formatting(self):
        from clio.prompt_overrides import format_prompt_template

        with pytest.raises(ValueError, match=r"voiceover.*missing.*\{title\}"):
            format_prompt_template("voiceover", "Only {index}", index="001", title="Paris")

    def test_unknown_placeholder_fails_before_formatting(self):
        from clio.prompt_overrides import format_prompt_template

        with pytest.raises(ValueError, match=r"voiceover.*unknown.*\{titel\}"):
            format_prompt_template("voiceover", "Bad {titel} {title}", title="Paris")


def test_generate_voiceover_uses_runtime_task_prompt(monkeypatch, tmp_path: Path):
    from clio.analyze import generate_voiceover

    config = _config(tmp_path)
    config.ai._global.providers["deepseek"] = cast(ProviderConfig, SimpleNamespace(provider_id="deepseek"))
    provider = MagicMock()
    provider.provider_id = "deepseek"

    captured: dict[str, str] = {}

    monkeypatch.setattr("clio.analyze.get_task_provider", lambda *a: (provider, "deepseek-chat"))
    monkeypatch.setattr("clio.analyze._wrap_with_context", lambda prompt, cfg, **kw: prompt)
    monkeypatch.setattr("clio.analyze._call_ai", lambda *args, **kw: captured.setdefault("prompt", args[3]) or "{}")
    monkeypatch.setattr("clio.analyze.extract_json", lambda text: {"title": "ok", "voiceover": "ok"})

    generate_voiceover(
        {"index": "001", "title": "Paris", "summary": "Arrival", "location": "Paris", "timeline": []},
        "Template",
        config,
        task_prompts={
            "voiceover": "Runtime {index} {title} {summary} {location} {timeline_text} {template} {target_words}"
        },
    )

    assert captured["prompt"].startswith("Runtime 001 Paris Arrival Paris")


def test_refine_fix_uses_separate_runtime_prompt_name(monkeypatch, tmp_path: Path):
    from clio.analyze import refine_text

    config = _config(tmp_path)
    provider = MagicMock()
    provider.provider_id = "deepseek"
    captured: dict[str, str] = {}

    monkeypatch.setattr("clio.analyze.get_task_provider", lambda *a: (provider, "deepseek-chat"))
    monkeypatch.setattr("clio.analyze._wrap_with_context", lambda prompt, cfg, **kw: prompt)
    monkeypatch.setattr("clio.analyze._call_ai", lambda *args, **kw: captured.setdefault("prompt", args[3]) or "{}")
    monkeypatch.setattr("clio.analyze.extract_json", lambda text: {"index": "001", "title": "fixed"})

    refine_text(
        {"index": "001", "title": "old"},
        config,
        fix="rename",
        task_prompts={
            "refine_text": "Wrong {existing_json}",
            "refine_text_fix": "Fix {fix_instruction} {existing_json}",
        },
    )

    assert captured["prompt"].startswith("Fix rename")

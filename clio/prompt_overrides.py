from __future__ import annotations

import string
from pathlib import Path
from typing import Any

from clio.config import AppConfig

PROMPT_PLACEHOLDERS: dict[str, set[str]] = {
    "video_analyze": set(),
    "voiceover": {"index", "title", "summary", "location", "timeline_text", "template", "target_words"},
    "vlog_plan": {"clips_json", "max_clips", "target_duration_sec", "example_index"},
    "refine_text": {"existing_json"},
    "refine_text_fix": {"fix_instruction", "existing_json"},
    "refine_script": {"analysis_json", "existing_json"},
    "refine_script_fix": {"fix_instruction", "analysis_json", "existing_json"},
    "transcript_context": {"transcripts_json"},
}

PROMPT_ALIASES: dict[str, tuple[str, ...]] = {
    "video_analyze": ("analyze",),
    "voiceover": ("scripts",),
    "vlog_plan": ("plan",),
}


class _PromptFormatError(ValueError):
    pass


def _project_dir(config: AppConfig) -> Path:
    try:
        pd = config.project_dir
    except Exception:
        pd = None
    if isinstance(pd, Path):
        return pd
    template_file = getattr(getattr(config, "script", None), "template_file", None)
    if isinstance(template_file, Path) and template_file.parent.name == "templates":
        return template_file.parent.parent
    if isinstance(template_file, Path):
        return template_file.parent
    return Path.cwd()


def prompt_override_dir(config: AppConfig) -> Path:
    return _project_dir(config) / "templates" / "prompts"


def _used_placeholders(template: str) -> set[str]:
    names: set[str] = set()
    try:
        for _, field_name, _, _ in string.Formatter().parse(template):
            if not field_name:
                continue
            names.add(field_name.split(".", 1)[0].split("[", 1)[0])
    except ValueError as exc:
        raise _PromptFormatError(f"invalid brace syntax: {exc}") from exc
    return names


def validate_prompt_template(prompt_name: str, prompt_template: str, available: set[str] | None = None) -> None:
    available_placeholders = available if available is not None else PROMPT_PLACEHOLDERS.get(prompt_name, set())
    try:
        used = _used_placeholders(prompt_template)
    except _PromptFormatError as exc:
        raise ValueError(f"Prompt '{prompt_name}' has invalid placeholder syntax: {exc}") from exc
    missing = sorted(available_placeholders - used)
    unknown = sorted(used - available_placeholders)
    if missing:
        fields = ", ".join(f"{{{name}}}" for name in missing)
        raise ValueError(f"Prompt '{prompt_name}' missing required placeholder(s): {fields}")
    if unknown:
        fields = ", ".join(f"{{{name}}}" for name in unknown)
        raise ValueError(f"Prompt '{prompt_name}' has unknown placeholder(s): {fields}")


def resolve_prompt_template(
    prompt_name: str,
    builtin_template: str,
    config: AppConfig,
    *,
    task_prompts: dict[str, str] | None = None,
) -> str:
    if task_prompts:
        for key in (prompt_name, *PROMPT_ALIASES.get(prompt_name, ())):
            runtime = (task_prompts.get(key) or "").strip()
            if runtime:
                return runtime

    override_path = prompt_override_dir(config) / f"{prompt_name}.md"
    if isinstance(override_path, Path) and override_path.is_file():
        from clio.utils import validate_within_root

        root = _project_dir(config)
        try:
            validate_within_root(override_path, root)
        except ValueError:
            return builtin_template
        text = override_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return builtin_template


def format_prompt_template(prompt_name: str, prompt_template: str, **values: Any) -> str:
    validate_prompt_template(prompt_name, prompt_template, set(values))
    return prompt_template.format(**values)

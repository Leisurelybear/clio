"""Safe, non-secret configuration identity for pipeline continuation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from clio import prompts
from clio.config import AppConfig
from clio.prompt_overrides import prompt_override_dir
from clio.resources import find_resource

_PRIVATE = {"api_key", "api_key_env", "credential_ref", "credentials", "credential_service", "api_token"}


def _public_settings(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _public_settings(getattr(value, field.name))
            for field in fields(value)
            if field.name not in _PRIVATE and not field.metadata.get("runtime")
        }
    if isinstance(value, dict):
        return {str(key): _public_settings(item) for key, item in value.items() if key not in _PRIVATE}
    if isinstance(value, (list, tuple)):
        return [_public_settings(item) for item in value]
    if isinstance(value, Path):
        return str(value.resolve())
    return value


def pipeline_config_fingerprint(config: AppConfig) -> str:
    """Hash output-affecting settings and template contents, never resolved secrets.

    Persist only the digest. Credentials may rotate without changing the execution
    contract; a model, scope, output path or prompt change requires a new submission.
    """
    project = config.project_dir or Path.cwd()
    templates = [project / "videos.json", config.script.template_file, project / "templates" / "trip_context.md"]
    for resource in ("templates/vlog_template.md", "templates/trip_context.md"):
        fallback = find_resource(resource)
        if fallback is not None:
            templates.append(fallback)
    override_dir = prompt_override_dir(config)
    if override_dir.is_dir():
        templates.extend(sorted(override_dir.glob("*.md")))
    template_hashes = {}
    for path in templates:
        if path.is_file():
            template_hashes[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = {
        "project": _public_settings(config.project_cfg),
        "compression": _public_settings(config.global_cfg.compress),
        "naming": _public_settings(config.naming),
        "tasks": _public_settings(config.ai.tasks),
        "providers": _public_settings(config.ai.providers),
        "builtin_prompts": {
            name: value for name, value in vars(prompts).items() if name.isupper() and isinstance(value, str)
        },
        "templates": template_hashes,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

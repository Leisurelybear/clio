from __future__ import annotations

import dataclasses
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from clio.config.models import (
    AIConfig,
    AnalyzeConfig,
    AppConfig,
    ExportConfig,
    GlobalAIConfig,
    GlobalCompressConfig,
    GlobalConfig,
    GlobalPathsConfig,
    GlobalWhisperConfig,
    NamingConfig,
    PlanConfig,
    PreviewConfig,
    PreviewSubtitlesConfig,
    ProjectAIConfig,
    ProjectCompressConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProjectWhisperConfig,
    ProviderConfig,
    ProxyConfig,
    ScriptConfig,
    ServerConfig,
    TaskConfig,
)
from clio.config.parsers import (
    _infer_provider_capabilities,
    _parse_providers,
    _parse_tasks,
)
from clio.config.validators import _filter_dc, _validate_config, validate_global_config

_GLOBAL_SECTION_DC_MAP: dict[str, type] = {
    "paths": GlobalPathsConfig,
    "proxy": ProxyConfig,
    "server": ServerConfig,
    "naming": NamingConfig,
    "ai": GlobalAIConfig,
    "compress": GlobalCompressConfig,
    "whisper": GlobalWhisperConfig,
}

_PROJECT_SECTION_DC_MAP: dict[str, type] = {
    "paths": ProjectPathsConfig,
    "ai": ProjectAIConfig,
    "compress": ProjectCompressConfig,
    "whisper": ProjectWhisperConfig,
    "analyze": AnalyzeConfig,
    "script": ScriptConfig,
    "plan": PlanConfig,
    "export": ExportConfig,
    "preview": PreviewConfig,
}

_MISSING = object()


def _path(value: str | None, base: Path | None = None) -> Path:
    if not value:
        raise ValueError("路径不能为空")
    path = Path(value)
    if base and not path.is_absolute():
        return (base / path).resolve()
    return path.resolve()


def _load_dotenv(base: Path, override: bool = False) -> None:
    env_file = base / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and (override or key not in os.environ):
            os.environ[key] = value


def _example_config_path() -> Path | None:
    """Locate the bundled config.example.yaml template.

    Resolution order: PyInstaller _internal bundle (clio/config/), then the
    repo root in the dev tree (up three levels from clio/config/loader.py).
    Returns None when neither exists.
    """
    for base in (Path(__file__).parent, Path(__file__).resolve().parent.parent.parent):
        candidate = base / "config.example.yaml"
        if candidate.is_file():
            return candidate
    return None


def _ensure_global_config(config_file: Path) -> None:
    """Create config.yaml from the bundled example template when missing.

    Failures (no template, unwritable dir) are swallowed so the normal
    open() in load_global_config raises FileNotFoundError as before.
    """
    if config_file.is_file():
        return
    example = _example_config_path()
    if example is None:
        return
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[config] 未找到 {config_file.name}，已从示例生成默认配置: {config_file}")
    except OSError:
        return


def deep_merge(base: dict, override: dict) -> dict:
    result = {}
    for key in base:
        if key in override:
            if isinstance(base[key], dict) and isinstance(override[key], dict):
                result[key] = deep_merge(base[key], override[key])
            else:
                result[key] = override[key]
        else:
            result[key] = base[key]
    for key in override:
        if key not in base:
            result[key] = override[key]
    return result


def _resolve_field_default(fd: dataclasses.Field):
    if fd.default is not dataclasses.MISSING:
        return fd.default
    if fd.default_factory is not dataclasses.MISSING:
        return fd.default_factory()
    return _MISSING


def _is_plain_yaml_value(val: Any) -> bool:
    """A scalar/list/dict we can serialize without python-object tags.

    Dataclass instances must never be written back into config.yaml. Only
    accept none/str/int/float/bool and (deeply) plain dicts/lists.
    """
    if val is None or isinstance(val, (str, int, float, bool)):
        return True
    if isinstance(val, dict):
        return all(isinstance(k, str) and _is_plain_yaml_value(v) for k, v in val.items())
    if isinstance(val, list):
        return all(_is_plain_yaml_value(v) for v in val)
    return False


def _upgrade_config_file(yaml_path: Path, *, section_map: dict[str, type]) -> None:
    if not yaml_path.is_file():
        return
    try:
        with yaml_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return

    if not isinstance(raw, dict):
        return

    added: list[str] = []
    changed = False

    for section_name, dc_type in section_map.items():
        section = raw.get(section_name)
        if not isinstance(section, dict):
            continue
        for fd in getattr(dc_type, "__dataclass_fields__", {}).values():
            if fd.name in section:
                continue
            val = _resolve_field_default(fd)
            if val is _MISSING:
                continue
            if not _is_plain_yaml_value(val):
                continue
            if isinstance(val, Path):
                val = str(val)
            section[fd.name] = val
            added.append(f"{section_name}.{fd.name}")
            changed = True

    providers = raw.get("ai", {}).get("providers", {})
    if isinstance(providers, dict):
        for pname, pcfg in providers.items():
            if not isinstance(pcfg, dict):
                continue
            for fd in ProviderConfig.__dataclass_fields__.values():
                if fd.name in pcfg:
                    continue
                val = _resolve_field_default(fd)
                if val is _MISSING:
                    continue
                if fd.name == "capabilities":
                    val = _infer_provider_capabilities(pcfg.get("type", "gemini"))
                pcfg[fd.name] = val
                added.append(f"ai.providers.{pname}.{fd.name}")
                changed = True

    tasks = raw.get("ai", {}).get("tasks", {})
    if isinstance(tasks, dict):
        for tname, tcfg in tasks.items():
            if not isinstance(tcfg, dict):
                continue
            for fd in TaskConfig.__dataclass_fields__.values():
                if fd.name in tcfg:
                    continue
                val = _resolve_field_default(fd)
                if val is _MISSING:
                    continue
                tcfg[fd.name] = val
                added.append(f"ai.tasks.{tname}.{fd.name}")
                changed = True

    if not changed:
        return

    text = yaml.dump(raw, default_flow_style=False, allow_unicode=True, sort_keys=False)
    # Validate the generated text is plain YAML we can load back before touching
    # the original file (backup-first, atomic replace with rollback tolerance).
    try:
        reloaded = yaml.safe_load(text)
    except Exception:
        return
    if not isinstance(reloaded, dict):
        return
    try:
        with yaml_path.open(encoding="utf-8") as f:
            previous = f.read()
    except Exception:
        previous = None
    if previous is not None:
        bak = yaml_path.with_name(yaml_path.name + ".bak")
        if not bak.exists():
            try:
                bak.write_text(previous, encoding="utf-8")
            except Exception:
                pass
    tmp = yaml_path.with_suffix(".yaml.tmp")
    unique_tmp = tmp.with_name(tmp.name + f".{os.getpid()}.{id(raw) % 100000}")
    try:
        with unique_tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(unique_tmp, yaml_path)
    except Exception:
        unique_tmp.unlink(missing_ok=True)
        return
    print(f"[config] {yaml_path.name} auto-added {len(added)} new field(s): {', '.join(added)}")


def _load_context(ai_raw: dict, base: Path, project_dir: Path | None = None) -> str:
    inline = (ai_raw.get("context") or "").strip()
    if inline:
        return inline
    file_ref = (ai_raw.get("context_file") or "").strip()
    if not file_ref:
        return ""
    if project_dir is not None:
        path = _path(file_ref, project_dir)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    path = _path(file_ref, base)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


# ---------------------------------------------------------------------------
# V1 → V2 migration
# ---------------------------------------------------------------------------

_CONFIG_VERSION = "config_version"
_V2 = "V2"

# Sections entirely project-only (no split)
_PROJECT_ONLY_SECTIONS = {"analyze", "script", "plan", "export", "preview"}

# Sections entirely global-only (no split)
_GLOBAL_ONLY_SECTIONS = {"proxy", "server", "naming"}

# Split-section keys belonging to project
_SPLIT_PROJECT_KEYS: dict[str, set[str]] = {
    "paths": {"output_dir"},
    # Deprecated split keys stay here only so V1 migration moves them out of global config.
    "compress": {"target_size_mb", "max_width", "split_max_min", "splits_subdir", "reencode_split"},
    "ai": {"tasks", "context", "context_file"},
    "whisper": {"enabled", "model_size", "language", "device", "max_segments_per_clip", "transcripts_subdir"},
}

# Split-section keys belonging to global
_SPLIT_GLOBAL_KEYS: dict[str, set[str]] = {
    "paths": {"ffmpeg", "ffprobe", "logs_dir"},
    "ai": {"providers", "debug_print_prompt", "provider_ttl_min"},
    "compress": {"codec", "fps", "remove_audio", "crf"},
    "whisper": {"cache_dir", "hf_endpoint"},
}


def _is_global_key(section: str, key: str) -> bool:
    if section in _GLOBAL_ONLY_SECTIONS:
        return True
    if section in _SPLIT_GLOBAL_KEYS and key in _SPLIT_GLOBAL_KEYS[section]:
        return True
    return False


def _is_project_key(section: str, key: str) -> bool:
    if section in _PROJECT_ONLY_SECTIONS:
        return True
    if section in _SPLIT_PROJECT_KEYS and key in _SPLIT_PROJECT_KEYS[section]:
        return True
    return False


def _filter_global_only(raw: dict) -> dict:
    """Keep only global-layer keys from a merged config dict."""
    result: dict = {}
    for section, value in raw.items():
        if not isinstance(value, dict):
            # Top-level scalars (config_version, etc.) — keep
            result[section] = value
            continue
        if section in _GLOBAL_ONLY_SECTIONS:
            result[section] = value
        elif section in _PROJECT_ONLY_SECTIONS:
            continue
        elif section in _SPLIT_GLOBAL_KEYS:
            kept = {k: v for k, v in value.items() if k in _SPLIT_GLOBAL_KEYS[section]}
            if kept:
                result[section] = kept
        elif section in _SPLIT_PROJECT_KEYS:
            kept = {k: v for k, v in value.items() if k in _SPLIT_GLOBAL_KEYS.get(section, set())}
            if kept:
                result[section] = kept
        else:
            # Unknown section — keep as-is (conservative)
            result[section] = value
    return result


def _filter_project_only(raw: dict) -> dict:
    """Keep only project-layer keys from a merged config dict."""
    result: dict = {}
    for section, value in raw.items():
        if not isinstance(value, dict):
            continue
        if section in _PROJECT_ONLY_SECTIONS:
            result[section] = value
        elif section in _GLOBAL_ONLY_SECTIONS:
            continue
        elif section in _SPLIT_PROJECT_KEYS:
            kept = {k: v for k, v in value.items() if k in _SPLIT_PROJECT_KEYS[section]}
            if kept:
                result[section] = kept
        elif section in _SPLIT_GLOBAL_KEYS:
            kept = {k: v for k, v in value.items() if k in _SPLIT_PROJECT_KEYS.get(section, set())}
            if kept:
                result[section] = kept
        else:
            # Unknown section — skip (conservative for project)
            pass
    return result


def _migrate_v1_to_v2(config_path: Path) -> None:
    """Migrate merged V1 config.yaml to split V2 structure."""
    try:
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return

    if not isinstance(raw, dict):
        return

    # Backup original
    bak = config_path.with_suffix(config_path.suffix + ".bak")
    if not bak.exists():
        try:
            with config_path.open(encoding="utf-8") as f:
                bak.write_text(f.read(), encoding="utf-8")
        except Exception:
            pass

    # Write V2 global config.yaml
    global_raw = _filter_global_only(raw)
    global_raw[_CONFIG_VERSION] = _V2
    try:
        text = yaml.dump(global_raw, default_flow_style=False, allow_unicode=True, sort_keys=False)
        tmp = config_path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, config_path)
        print(f"[config] migrated config.yaml to V2 (global-only, backup at {bak.name})")
    except Exception:
        return

    # Extract project fields from V1 config and write project.yaml if not present
    project_out = _filter_project_only(raw)
    if project_out:
        proj_path = config_path.parent / "project.yaml"
        if not proj_path.exists():
            try:
                text = yaml.dump(project_out, default_flow_style=False, allow_unicode=True, sort_keys=False)
                tmp = proj_path.with_suffix(".yaml.tmp")
                tmp.write_text(text, encoding="utf-8")
                os.replace(tmp, proj_path)
                print("[config] created project.yaml from migrated V1 project fields")
            except Exception:
                pass

    # Migrate existing project.yaml files
    registry_path = config_path.parent / "projects.json"
    if registry_path.is_file():
        import json

        try:
            reg = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            reg = {}
        for p_str in reg.get("projects", []):
            proj_dir = Path(p_str)
            proj_yaml = proj_dir / "project.yaml"
            if not proj_yaml.is_file():
                continue
            try:
                with proj_yaml.open(encoding="utf-8") as f:
                    proj_raw = yaml.safe_load(f) or {}
            except Exception:
                continue

            # Back up project.yaml first
            proj_bak = proj_yaml.with_suffix(proj_yaml.suffix + ".bak")
            if not proj_bak.exists():
                try:
                    with proj_yaml.open(encoding="utf-8") as f:
                        proj_bak.write_text(f.read(), encoding="utf-8")
                except Exception:
                    pass

            project_out = _filter_project_only(proj_raw)
            if not project_out:
                # Project has nothing project-specific — remove file
                proj_yaml.unlink(missing_ok=True)
                print(f"[config] {proj_dir.name}/project.yaml: no project-only fields, removed")
            else:
                try:
                    text = yaml.dump(project_out, default_flow_style=False, allow_unicode=True, sort_keys=False)
                    tmp = proj_yaml.with_suffix(".yaml.tmp")
                    tmp.write_text(text, encoding="utf-8")
                    os.replace(tmp, proj_yaml)
                    print(f"[config] {proj_dir.name}/project.yaml: migrated to V2")
                except Exception:
                    pass


def _migrate_if_needed(config_path: Path) -> None:
    """Check config_version and auto-migrate if V1."""
    try:
        with config_path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    version = raw.get(_CONFIG_VERSION)
    if version == _V2:
        return
    _migrate_v1_to_v2(config_path)


def _legacy_ai_config(raw: dict) -> AIConfig:
    gemini_raw = raw.get("gemini", {})
    api_key = os.environ.get("GEMINI_API_KEY") or gemini_raw.get("api_key", "")
    model = gemini_raw.get("model", "gemini-2.5-flash")
    video_model = gemini_raw.get("video_model", "gemini-2.5-flash-lite")
    return AIConfig(
        providers={
            "gemini": ProviderConfig(
                name="gemini",
                type="gemini",
                api_key=api_key,
                api_key_env="GEMINI_API_KEY",
                poll_interval_sec=gemini_raw.get("poll_interval_sec", 5),
            ),
        },
        tasks={
            "video_analyze": TaskConfig(provider="gemini", model=video_model),
            "voiceover": TaskConfig(provider="gemini", model=model),
            "vlog_plan": TaskConfig(provider="gemini", model=model),
        },
    )


def load_global_config(config_path: str | Path = "config.yaml") -> GlobalConfig:
    """Load only the global config (config.yaml), return GlobalConfig."""
    config_file = Path(config_path).resolve()
    base = config_file.parent
    _load_dotenv(base)

    _ensure_global_config(config_file)
    _migrate_if_needed(config_file)
    _upgrade_config_file(config_file, section_map=_GLOBAL_SECTION_DC_MAP)

    try:
        with config_file.open(encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
    except (NotADirectoryError, IsADirectoryError) as exc:
        raise FileNotFoundError(exc) from exc

    # Providers use env var resolution
    ai_raw = raw.get("ai", {})
    ai_cfg = GlobalAIConfig(
        providers=_parse_providers(ai_raw.get("providers")),
        debug_print_prompt=ai_raw.get("debug_print_prompt", False),
        provider_ttl_min=ai_raw.get("provider_ttl_min", 60),
    )

    gc = GlobalConfig(
        proxy=ProxyConfig(**_filter_dc(raw.get("proxy", {}), ProxyConfig)),
        server=ServerConfig(**_filter_dc(raw.get("server", {}), ServerConfig)),
        naming=NamingConfig(**_filter_dc(raw.get("naming", {}), NamingConfig)),
        paths=GlobalPathsConfig(
            ffmpeg=raw.get("paths", {}).get("ffmpeg", ""),
            ffprobe=raw.get("paths", {}).get("ffprobe", ""),
            logs_dir=_path(raw.get("paths", {}).get("logs_dir", "./logs"), base),
        ),
        ai=ai_cfg,
        compress=GlobalCompressConfig(
            codec=raw.get("compress", {}).get("codec", "libx264"),
            fps=raw.get("compress", {}).get("fps", 15),
            remove_audio=raw.get("compress", {}).get("remove_audio", True),
            crf=raw.get("compress", {}).get("crf", 32),
        ),
        whisper=GlobalWhisperConfig(
            cache_dir=raw.get("whisper", {}).get("cache_dir"),
            hf_endpoint=raw.get("whisper", {}).get("hf_endpoint", ""),
        ),
    )
    validate_global_config(gc)
    return gc


def load_project_config(
    project_dir: Path,
    *,
    config_path: Path | None = None,
) -> ProjectConfig | None:
    """Load project-level config (project.yaml), return ProjectConfig or None."""
    project_yaml = project_dir.resolve() / "project.yaml"
    if not project_yaml.is_file():
        return None

    _upgrade_config_file(project_yaml, section_map=_PROJECT_SECTION_DC_MAP)

    with project_yaml.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    project_base = project_dir.resolve()
    config_base = config_path.parent if config_path is not None else project_base

    paths_raw = raw.get("paths", {})
    ai_raw = raw.get("ai", {})
    context = _load_context(ai_raw, config_base, project_dir=project_dir)

    return ProjectConfig(
        paths=ProjectPathsConfig(
            output_dir=_path(paths_raw.get("output_dir", "./output"), project_base),
        ),
        ai=ProjectAIConfig(
            tasks=_parse_tasks(ai_raw.get("tasks")),
            context=context,
        ),
        compress=ProjectCompressConfig(
            target_size_mb=raw.get("compress", {}).get("target_size_mb", 5),
            max_width=raw.get("compress", {}).get("max_width", 640),
        ),
        analyze=AnalyzeConfig(**_filter_dc(raw.get("analyze", {}), AnalyzeConfig)),
        script=ScriptConfig(
            scripts_subdir=raw.get("script", {}).get("scripts_subdir", "scripts"),
            template_file=_path(
                raw.get("script", {}).get("template_file", "./templates/vlog_template.md"),
                project_base,
            ),
            target_words=raw.get("script", {}).get("target_words", 80),
        ),
        plan=PlanConfig(**_filter_dc(raw.get("plan", {}), PlanConfig)),
        whisper=ProjectWhisperConfig(
            enabled=raw.get("whisper", {}).get("enabled", True),
            model_size=raw.get("whisper", {}).get("model_size", "medium"),
            language=raw.get("whisper", {}).get("language", "zh"),
            device=raw.get("whisper", {}).get("device", "auto"),
            max_segments_per_clip=raw.get("whisper", {}).get("max_segments_per_clip", 5),
            transcripts_subdir=raw.get("whisper", {}).get("transcripts_subdir", "transcripts"),
        ),
        export=ExportConfig(**_filter_dc(raw.get("export", {}), ExportConfig)),
        preview=PreviewConfig(
            subtitles=PreviewSubtitlesConfig(
                **_filter_dc(raw.get("preview", {}).get("subtitles", {}), PreviewSubtitlesConfig)
            )
        ),
    )


def load_config(
    config_path: str | Path = "config.yaml",
    project_dir: Path | None = None,
) -> AppConfig:
    """Load config using the new V2 split structure.

    Calls load_global_config + load_project_config internally,
    composes into AppConfig wrapper.
    """
    config_file = Path(config_path).resolve()
    global_cfg = load_global_config(config_file)
    effective_project_dir = project_dir
    if effective_project_dir is None and (config_file.parent / "project.yaml").is_file():
        effective_project_dir = config_file.parent
    project_cfg = (
        load_project_config(effective_project_dir, config_path=config_file)
        if effective_project_dir is not None
        else None
    )

    config = AppConfig(global_cfg=global_cfg, project_cfg=project_cfg, project_dir=effective_project_dir)
    _validate_config(config)
    return config


def apply_run_paths(
    config: AppConfig,
    output_dir: Path | None = None,
) -> AppConfig:
    config = deepcopy(config)
    if config.project_cfg is None:
        return config
    if output_dir:
        config.project_cfg.paths.output_dir = output_dir.resolve()
    return config

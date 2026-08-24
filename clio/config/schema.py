"""UI-facing config field schema.

Single source of truth for which fields appear in the settings page, in
which group (basic / advanced / hidden), and with which control type.
Generated from the section dataclasses so new fields default to the
"advanced" group and remain visible unless explicitly hidden.
"""

from __future__ import annotations

from dataclasses import fields as dc_fields
from functools import lru_cache
from typing import Any

from clio.config.loader import _GLOBAL_SECTION_DC_MAP, _PROJECT_SECTION_DC_MAP
from clio.config.models import PreviewConfig, PreviewSubtitlesConfig

BASIC = "basic"
ADVANCED = "advanced"
HIDDEN = "hidden"

_SECTION_LABELS: dict[str, str] = {
    "proxy": "代理",
    "server": "服务",
    "naming": "编号命名",
    "paths": "路径",
    "compress": "压缩",
    "whisper": "Whisper 语音识别",
    "ai": "AI 配置",
    "analyze": "AI 分析",
    "script": "口播脚本",
    "plan": "剪辑规划",
    "export": "导出设置",
}

# Explicit UI overrides keyed by <section>.<field>.
# Anything not listed here gets type-based inference and the advanced group.
_FIELD_OVERRIDES: dict[str, dict[str, Any]] = {
    # project basic
    "paths.output_dir": {"group": BASIC, "ui": "folder"},
    "ai.context": {"group": BASIC, "ui": "textarea"},
    "compress.target_size_mb": {"group": BASIC},
    "compress.max_width": {"group": BASIC, "ui": "select_or_number", "choices": [640, 1280, 1920]},
    "plan.target_duration_sec": {"group": BASIC},
    "whisper.enabled": {"group": BASIC},
    "whisper.language": {"group": BASIC, "ui": "select", "choices": ["zh", "en", "auto"]},
    "export.canvas_ratio": {"group": BASIC, "ui": "select", "choices": ["16:9", "9:16", "1:1"]},
    # project advanced (empty override = advanced default)
    "analyze.skip_existing": {},
    "analyze.max_analyze_duration_min": {},
    "analyze.window_max_min": {},
    "analyze.window_overlap_sec": {},
    "analyze.max_workers": {},
    "analyze.use_gpmf": {},
    "script.target_words": {},
    "script.template_file": {"ui": "file"},
    "plan.use_transcripts": {},
    "plan.max_clips_per_day": {},
    "whisper.model_size": {"ui": "select", "choices": ["small", "medium", "large-v3"]},
    "whisper.device": {"ui": "select", "choices": ["auto", "cpu", "cuda"]},
    "whisper.engine": {"ui": "select"},
    # global advanced
    "proxy.enabled": {},
    "proxy.url": {"visible_when": {"field": "proxy.enabled", "equals": True}},
    "server.api_token": {"ui": "password"},
    "server.task_retention_days": {},
    "server.task_max_terminal_tasks": {},
    "server.task_cleanup_interval_min": {},
    "naming.index_width": {},
    "paths.ffmpeg": {"ui": "exe"},
    "paths.ffprobe": {"ui": "exe"},
    "paths.logs_dir": {"ui": "folder"},
    "compress.fps": {},
    "compress.codec": {"ui": "select", "choices": ["libx264", "libx265"]},
    "compress.crf": {},
    "compress.remove_audio": {},
    "whisper.cache_dir": {"ui": "folder"},
    "whisper.hf_endpoint": {},
    "ai.debug_print_prompt": {},
    "ai.provider_ttl_min": {},
}


def _python_type_to_schema_type(annotation: Any) -> str:
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    type_name = getattr(annotation, "__name__", "") or str(annotation)
    if "Path" in type_name:
        return "path"
    origin = getattr(annotation, "__origin__", None)
    if origin is list or origin is dict:
        return "list"
    return "str"


@lru_cache(maxsize=1)
def build_config_schema() -> dict[str, list[dict[str, Any]]]:
    def layer(section_map: dict[str, type]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, dc in section_map.items():
            is_subtitles = dc is PreviewConfig
            field_list: list[dict[str, Any]] = []
            if is_subtitles:
                for sf in dc_fields(PreviewSubtitlesConfig):
                    path = f"{key}.subtitles.{sf.name}"
                    field_list.append(
                        {
                            "path": path,
                            "type": _python_type_to_schema_type(sf.type),
                            "group": HIDDEN,
                            "ui": "auto",
                        }
                    )
            else:
                for f in dc_fields(dc):
                    path = f"{key}.{f.name}"
                    override = _FIELD_OVERRIDES.get(path, {})
                    entry: dict[str, Any] = {
                        "path": path,
                        "type": _python_type_to_schema_type(f.type),
                        "group": override.get("group", ADVANCED),
                        "ui": override.get("ui", "auto"),
                    }
                    if override.get("choices") is not None:
                        entry["choices"] = override["choices"]
                    if override.get("visible_when"):
                        entry["visible_when"] = override["visible_when"]
                    field_list.append(entry)
            out.append(
                {
                    "key": key,
                    "label": _SECTION_LABELS.get(key, key),
                    "custom_ui": is_subtitles,
                    "fields": field_list,
                }
            )
        return out

    return {
        "project": layer(_PROJECT_SECTION_DC_MAP),
        "global": layer(_GLOBAL_SECTION_DC_MAP),
    }

"""Whisper model management — list, delete, switch model."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml

from clio.config import WhisperModelSize
from clio.transcribe import _resolve_cache_dir, is_model_loaded, model_usage_lock
from clio.ui.handler_protocol import HandlerProtocol
from clio.whisper_cache import (
    REQUIRED_MODEL_FILES,
    is_model_cache_complete,
    model_cache_dir,
)


def _get_cache_dir(handler: HandlerProtocol, qs: dict[str, Any]) -> Path:
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    return _resolve_cache_dir(cfg)


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _list_cached_models(cache_dir: Path) -> list[dict]:
    if not cache_dir.is_dir():
        return []
    models = []
    for entry in cache_dir.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        if "faster-whisper" not in name.lower():
            continue
        snapshots = entry / "snapshots"
        if not snapshots.is_dir():
            continue
        total_size = 0
        required_found = {filename: False for filename in REQUIRED_MODEL_FILES}
        for snap_dir in snapshots.iterdir():
            if not snap_dir.is_dir():
                continue
            for f in snap_dir.rglob("*"):
                if f.is_file():
                    try:
                        sz = f.stat().st_size
                        total_size += sz
                        if f.name in required_found and sz > 0:
                            required_found[f.name] = True
                    except OSError:
                        pass
        prefix = "models--Systran--faster-whisper-"
        model_size = name[len(prefix) :] if name.startswith(prefix) else name
        models.append(
            {
                "name": model_size,
                "size_bytes": total_size,
                "size_display": _format_bytes(total_size),
                "valid": all(required_found.values()) or is_model_cache_complete(cache_dir, model_size),
            }
        )
    return models


def handle_get_whisper_models(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    cache_dir = _get_cache_dir(handler, qs)
    cached = _list_cached_models(cache_dir)
    available = list(WhisperModelSize)
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    current_model = cfg.whisper.model_size
    free_bytes = 0
    try:
        if cache_dir.is_dir():
            free_bytes = shutil.disk_usage(cache_dir).free
        else:
            free_bytes = shutil.disk_usage(cache_dir.parent).free
    except OSError:
        pass
    handler._send_json(
        {
            "ok": True,
            "cached": cached,
            "available": [{"name": m.value, "label": m.value} for m in available],
            "current_model": current_model,
            "cache_dir": str(cache_dir),
            "free_bytes": free_bytes,
            "free_display": _format_bytes(free_bytes),
        }
    )


def handle_post_whisper_model_delete(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    model_name = (obj.get("name") or "").strip()
    if not model_name:
        handler._send_json({"ok": False, "error": "missing model name"}, 400)
        return
    if model_name not in list(WhisperModelSize):
        handler._send_json(
            {
                "ok": False,
                "error": f"invalid model name, must be one of: {', '.join(WhisperModelSize)}",
            },
            400,
        )
        return
    cache_dir = _get_cache_dir(handler, qs)
    if not cache_dir.is_dir():
        handler._send_json({"ok": False, "error": "cache dir not found"}, 404)
        return
    target = model_cache_dir(cache_dir, model_name)
    if not target.is_dir():
        handler._send_json({"ok": True, "deleted": False})
        return
    with model_usage_lock():
        if is_model_loaded(model_name):
            handler._send_json(
                {"ok": False, "error": "该模型正在使用中，请先切换到其他模型后再删除"},
                409,
            )
            return
        quarantine = target.with_name(f"{target.name}.deleting-{os.urandom(4).hex()}")
        try:
            target.rename(quarantine)
        except OSError as e:
            handler._send_json({"ok": False, "error": f"删除失败: {e}"}, 500)
            return
        try:
            shutil.rmtree(quarantine)
        except OSError as e:
            quarantine.rename(target)
            handler._send_json({"ok": False, "error": f"删除失败: {e}"}, 500)
            return
    handler._send_json({"ok": True, "deleted": True})


def handle_put_whisper_model(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    model_name = (obj.get("model_size") or "").strip()
    if not model_name:
        handler._send_json({"ok": False, "error": "missing model_size"}, 400)
        return
    if model_name not in list(WhisperModelSize):
        handler._send_json(
            {
                "ok": False,
                "error": f"invalid model_size, must be one of: {', '.join(WhisperModelSize)}",
            },
            400,
        )
        return
    proj_dir = handler._resolve_project_dir(qs)
    proj_yaml = proj_dir / "project.yaml"
    if not proj_yaml.is_file():
        proj_yaml.parent.mkdir(parents=True, exist_ok=True)
        raw: dict[str, Any] = {}
    else:
        with open(proj_yaml, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    raw.setdefault("whisper", {})["model_size"] = model_name
    suffix = os.urandom(4).hex()
    tmp = proj_yaml.parent / f"{proj_yaml.name}.{suffix}.tmp"
    try:
        tmp.write_text(yaml.dump(raw, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        tmp.replace(proj_yaml)
    except OSError:
        tmp.unlink(missing_ok=True)
        handler._send_json({"ok": False, "error": "写入配置文件失败"}, 500)
        return
    handler.__class__._config_cache.invalidate_key(str(proj_dir.resolve()))
    handler._send_json({"ok": True, "model_size": model_name})

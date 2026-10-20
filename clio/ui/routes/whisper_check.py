"""Whisper installation check handler."""

from __future__ import annotations

import sys
from typing import Any

from clio.asr.readiness import check_asr_ready
from clio.transcribe import _resolve_cache_dir, check_cublas, check_whisper
from clio.ui.handler_protocol import HandlerProtocol
from clio.whisper_cache import is_model_cache_complete


def handle_get_whisper_check(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    frozen = bool(getattr(sys, "frozen", False))
    engine = "local"
    display_name = "本地 faster-whisper"
    ready = False
    readiness_error = None
    readiness = None
    cfg = None
    try:
        proj_dir = handler._resolve_project_dir(qs)
        cfg = handler._get_config(proj_dir)
        configured_engine = getattr(cfg.whisper, "engine", "local")
        if isinstance(configured_engine, str) and configured_engine:
            engine = configured_engine
        readiness = check_asr_ready(cfg, engine=engine)
        display_name = readiness.display_name
        ready = readiness.ready
        readiness_error = readiness.error
    except Exception:
        pass

    installed = check_whisper() if engine == "local" else False
    if readiness is None:
        ready = installed
        readiness_error = (
            None
            if installed
            else "本地 faster-whisper 不可用。请执行 `python main.py whisper install`，并检查 CUDA/cuBLAS。"
        )
    cuda = False
    if engine == "local" and installed:
        try:
            from ctranslate2 import get_cuda_device_count

            cuda = get_cuda_device_count() > 0
        except ImportError:
            pass

    cache_path = None
    model_cached = False
    try:
        proj_dir = handler._resolve_project_dir(qs)
        if cfg is None:
            cfg = handler._get_config(proj_dir)
        cache_dir = _resolve_cache_dir(cfg)
        if cache_dir.is_dir():
            cache_path = str(cache_dir)
            model_cached = is_model_cache_complete(cache_dir, cfg.whisper.model_size)
    except Exception:
        pass

    handler._send_json(
        {
            "ok": True,
            "engine": engine,
            "engine_display_name": display_name,
            "ready": ready,
            "readiness_error": readiness_error,
            "requires_local_model": engine == "local",
            "installed": installed,
            "frozen": frozen,
            "cublas": check_cublas(),
            "cuda": cuda,
            "cache_path": cache_path,
            "model_cached": model_cached,
        }
    )

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from clio.asr.factory import build_provider


@dataclass(frozen=True)
class ASRReadiness:
    engine: str
    display_name: str
    ready: bool
    error: str | None


def check_asr_ready(
    config: Any,
    *,
    engine: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ASRReadiness:
    whisper = getattr(config, "whisper", None)
    configured_engine = engine or getattr(whisper, "engine", "local")
    engine_id = configured_engine if isinstance(configured_engine, str) and configured_engine else "local"
    try:
        provider = build_provider(engine_id, config)
    except RuntimeError as exc:
        return ASRReadiness(engine=engine_id, display_name=engine_id, ready=False, error=str(exc))

    error = provider.readiness_error(environ)
    return ASRReadiness(
        engine=engine_id,
        display_name=provider.capabilities.display_name,
        ready=error is None,
        error=error,
    )

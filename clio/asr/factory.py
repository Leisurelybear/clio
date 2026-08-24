from __future__ import annotations

from typing import Any

from clio.asr.base import ProviderCapabilities, TranscriptionProvider

_PROVIDERS: dict[str, Any] = {}


def register_provider(cls: Any) -> Any:
    caps: ProviderCapabilities | None = getattr(cls, "capabilities", None)
    if not isinstance(caps, ProviderCapabilities):
        raise TypeError(f"{cls.__name__} must define capabilities: ProviderCapabilities")
    _PROVIDERS[caps.id] = cls
    return cls


def list_providers() -> list[ProviderCapabilities]:
    return [cls.capabilities for cls in _PROVIDERS.values()]


def build_provider(engine_id: str, config) -> TranscriptionProvider:
    if not engine_id:
        raise RuntimeError("ASR engine 未配置，请在 whisper.engine 中指定")
    cls = _PROVIDERS.get(engine_id)
    if cls is None:
        available = ", ".join(sorted(_PROVIDERS)) or "无"
        raise RuntimeError(f"未知的 ASR engine: {engine_id}，当前可用: {available}")
    return cls(config)

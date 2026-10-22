from __future__ import annotations

from typing import Any

from clio.asr.base import ProviderCapabilities, TranscriptionProvider
from clio.i18n import t

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
        raise RuntimeError(t("asr.factory.engine_not_configured"))
    cls = _PROVIDERS.get(engine_id)
    if cls is None:
        available = ", ".join(sorted(_PROVIDERS)) or t("asr.factory.none")
        raise RuntimeError(t("asr.factory.unknown_engine", {"engine": engine_id, "available": available}))
    return cls(config)

from __future__ import annotations

from clio.asr.base import TranscriptionProvider
from clio.config import AppConfig

_PROVIDERS: dict[str, type] = {}


def build_provider(name: str, config: AppConfig) -> TranscriptionProvider:
    if not name:
        raise RuntimeError("云端 ASR provider 未配置，请在 whisper.cloud.provider 中指定")
    cls = _PROVIDERS.get(name)
    if cls is None:
        raise RuntimeError(f"未知的云端 ASR provider: {name}，当前可用: {', '.join(_PROVIDERS) or '无'}")
    return cls(config)

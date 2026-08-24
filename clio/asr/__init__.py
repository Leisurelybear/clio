from clio.asr import (
    aliyun,  # noqa: F401  (registers "aliyun" provider)
    local,  # noqa: F401  (registers "local" provider)
)
from clio.asr.base import ProviderCapabilities, TranscriptionProvider, TranscriptSegment

__all__ = ["ProviderCapabilities", "TranscriptSegment", "TranscriptionProvider"]

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str
    avg_logprob: float = 0.0
    low_confidence: bool = False

    def to_dict(self) -> dict:
        entry: dict = {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "text": self.text.strip(),
            "avg_logprob": round(self.avg_logprob, 3),
        }
        if self.low_confidence:
            entry["low_confidence"] = True
        return entry


@dataclass(frozen=True)
class ProviderCapabilities:
    id: str
    display_name: str
    supports_local_file: bool = True
    requires_public_url: bool = False
    max_audio_mb: int | None = None
    supported_languages: list[str] = field(default_factory=lambda: ["*"])


@runtime_checkable
class TranscriptionProvider(Protocol):
    capabilities: ProviderCapabilities

    def transcribe(
        self,
        audio_path: Path,
        language: str,
        progress_callback: Callable[[int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[TranscriptSegment]: ...

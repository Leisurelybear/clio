from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
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


@runtime_checkable
class TranscriptionProvider(Protocol):
    def transcribe(
        self,
        audio_path: Path,
        language: str,
        progress_callback: Callable[[int], None] | None = None,
    ) -> list[TranscriptSegment]: ...

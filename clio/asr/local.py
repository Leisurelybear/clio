from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from clio.asr.base import ProviderCapabilities, TranscriptSegment
from clio.asr.factory import register_provider
from clio.i18n import t


@register_provider
class LocalWhisperProvider:
    capabilities = ProviderCapabilities(
        id="local",
        display_name=t("asr.local.display_name"),
        supports_local_file=True,
    )

    def __init__(self, config) -> None:
        self._config = config

    def readiness_error(self, environ=None) -> str | None:
        from clio.transcribe import check_whisper

        if check_whisper():
            return None
        return t("asr.local.unavailable")

    def transcribe(
        self,
        audio_path: Path,
        language: str,
        progress_callback: Callable[[int], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[TranscriptSegment]:
        from clio.transcribe import _transcribe_local_whisper

        raw = _transcribe_local_whisper(self._config, audio_path, progress_callback, cancel_event)
        return [
            TranscriptSegment(
                start=entry["start"],
                end=entry["end"],
                text=entry["text"],
                avg_logprob=entry.get("avg_logprob", 0.0),
                low_confidence=entry.get("low_confidence", False),
            )
            for entry in raw
        ]

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from clio._str_enum import StrEnum


class TaskName(StrEnum):
    VIDEO_ANALYZE = "video_analyze"
    VOICEOVER = "voiceover"
    VLOG_PLAN = "vlog_plan"
    REFINE_TEXT = "refine_text"


class ProviderCapability(StrEnum):
    TEXT = "text"
    VIDEO = "video"


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class AIResponse:
    text: str
    token_usage: TokenUsage | None = None
    finish_reason: str | None = None


@runtime_checkable
class TextAIProvider(Protocol):
    """纯文本 AI 能力（口播、规划等）。"""

    provider_id: str

    def generate_text(self, prompt: str, model: str) -> AIResponse: ...

    def close(self) -> None: ...


@runtime_checkable
class VideoAIProvider(TextAIProvider, Protocol):
    """支持视频理解的 AI 能力。"""

    def analyze_video(
        self,
        video_path: str,
        prompt: str,
        model: str,
        progress_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> AIResponse: ...


def provider_supports_video(cfg: object) -> bool:
    caps = getattr(cfg, "capabilities", None)
    if caps is None:
        return getattr(cfg, "type", None) == "gemini"
    return ProviderCapability.VIDEO.value in caps

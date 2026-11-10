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

    def __post_init__(self) -> None:
        for field_name in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"TokenUsage.{field_name} must be a non-negative int")


def normalize_response_reason(value: object) -> str | None:
    """Normalize a provider reason while preserving its meaningful raw value."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


@dataclass(frozen=True)
class AIResponse:
    """Normalized completion returned by every AI provider.

    ``finish_reason`` and ``block_reason`` always use the provider's raw,
    non-empty reason string when it is available.  A provider must return a
    blocked completion instead of raising so callers can handle every
    completion outcome through this one carrier.
    """

    text: str
    token_usage: TokenUsage | None = None
    finish_reason: str | None = None
    block_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("AIResponse.text must be str")
        if self.token_usage is not None and not isinstance(self.token_usage, TokenUsage):
            raise TypeError("AIResponse.token_usage must be TokenUsage or None")
        for field_name in ("finish_reason", "block_reason"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"AIResponse.{field_name} must be a non-empty str or None")
        if self.block_reason is not None and self.text:
            raise ValueError("A blocked AIResponse must not contain text")

    @property
    def blocked(self) -> bool:
        return self.block_reason is not None


@runtime_checkable
class TextAIProvider(Protocol):
    """纯文本 AI 能力（口播、规划等）。"""

    provider_id: str

    def generate_text(
        self,
        prompt: str,
        model: str,
        cancel_event: threading.Event | None = None,
        *,
        temperature: float | None = None,
    ) -> AIResponse: ...

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
        *,
        temperature: float | None = None,
    ) -> AIResponse: ...


def provider_supports_video(cfg: object) -> bool:
    caps = getattr(cfg, "capabilities", None)
    if caps is None:
        return getattr(cfg, "type", None) == "gemini"
    return ProviderCapability.VIDEO.value in caps

"""Token usage tracking — records AI token consumption per project."""

from __future__ import annotations

import copy
import json
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from clio.utils import write_json_atomic

if TYPE_CHECKING:
    from clio.ai.base import TokenUsage


class TokenUsageStore(ABC):
    @abstractmethod
    def record(self, task: str, model: str, usage: TokenUsage) -> None: ...
    @abstractmethod
    def get_stats(self) -> dict: ...
    def close(self) -> None: ...


_EMPTY_STATS = {
    "total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    "by_model": {},
    "by_task": {},
    "history": [],
}


def _merge_stats(stats: dict, task: str, model: str, pt: int, ct: int, tt: int) -> None:
    stats["total"]["prompt_tokens"] += pt
    stats["total"]["completion_tokens"] += ct
    stats["total"]["total_tokens"] += tt

    model_key = stats["by_model"].setdefault(
        model, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    )
    model_key["prompt_tokens"] += pt
    model_key["completion_tokens"] += ct
    model_key["total_tokens"] += tt
    model_key["calls"] += 1

    task_key = stats["by_task"].setdefault(
        task, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    )
    task_key["prompt_tokens"] += pt
    task_key["completion_tokens"] += ct
    task_key["total_tokens"] += tt
    task_key["calls"] += 1


_MAX_TOKEN_HISTORY = 500


class FileTokenUsageStore(TokenUsageStore):
    def __init__(self, output_dir: str):
        self._path = Path(output_dir) / ".token_usage.json"
        self._lock = threading.Lock()

    def record(self, task: str, model: str, usage: TokenUsage) -> None:
        with self._lock:
            raw = self._read_raw()
            entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": task,
                "model": model,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            raw["history"].append(entry)
            if len(raw["history"]) > _MAX_TOKEN_HISTORY:
                raw["history"] = raw["history"][-_MAX_TOKEN_HISTORY:]
            _merge_stats(raw, task, model, usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
            write_json_atomic(self._path, raw)

    def get_stats(self) -> dict:
        with self._lock:
            return self._read_raw()

    def close(self) -> None:
        pass

    def _read_raw(self) -> dict:
        if not self._path.is_file():
            return copy.deepcopy(_EMPTY_STATS)
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return copy.deepcopy(_EMPTY_STATS)
        if not isinstance(data, dict) or "history" not in data or not isinstance(data.get("history"), list):
            return copy.deepcopy(_EMPTY_STATS)
        return data

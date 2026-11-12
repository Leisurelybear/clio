"""Token usage tracking — records AI token consumption and estimated cost per project."""

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
    def record(
        self,
        task: str,
        model: str,
        usage: TokenUsage,
        *,
        provider: str = "",
        prompt_version: str = "",
        cost_usd: float = 0.0,
    ) -> None: ...
    @abstractmethod
    def get_stats(self) -> dict: ...
    @abstractmethod
    def close(self) -> None: ...


_EMPTY_STATS = {
    "total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0},
    "by_model": {},
    "by_task": {},
    "history": [],
}


def estimate_cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    prompt_price_per_1m_usd: float = 0.0,
    completion_price_per_1m_usd: float = 0.0,
) -> float:
    cost = (
        prompt_tokens * max(0.0, prompt_price_per_1m_usd) + completion_tokens * max(0.0, completion_price_per_1m_usd)
    ) / 1_000_000
    return round(cost, 8)


def _add_cost(bucket: dict, cost_usd: float) -> None:
    bucket["cost_usd"] = round(float(bucket.get("cost_usd", 0.0)) + cost_usd, 8)


def _merge_stats(
    stats: dict,
    task: str,
    model: str,
    pt: int,
    ct: int,
    tt: int,
    cost_usd: float = 0.0,
) -> None:
    total = stats["total"]
    total["prompt_tokens"] += pt
    total["completion_tokens"] += ct
    total["total_tokens"] += tt
    _add_cost(total, cost_usd)

    model_key = stats["by_model"].setdefault(
        model, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0, "cost_usd": 0.0}
    )
    model_key["prompt_tokens"] += pt
    model_key["completion_tokens"] += ct
    model_key["total_tokens"] += tt
    model_key["calls"] += 1
    _add_cost(model_key, cost_usd)

    task_key = stats["by_task"].setdefault(
        task, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0, "cost_usd": 0.0}
    )
    task_key["prompt_tokens"] += pt
    task_key["completion_tokens"] += ct
    task_key["total_tokens"] += tt
    task_key["calls"] += 1
    _add_cost(task_key, cost_usd)


_MAX_TOKEN_HISTORY = 500


class FileTokenUsageStore(TokenUsageStore):
    def __init__(self, output_dir: str):
        self._path = Path(output_dir) / ".token_usage.json"
        self._lock = threading.Lock()

    def record(
        self,
        task: str,
        model: str,
        usage: TokenUsage,
        *,
        provider: str = "",
        prompt_version: str = "",
        cost_usd: float = 0.0,
    ) -> None:
        with self._lock:
            raw = self._read_raw()
            entry = {
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "task": task,
                "model": model,
                "provider": provider,
                "prompt_version": prompt_version,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "cost_usd": round(cost_usd, 8),
            }
            raw["history"].append(entry)
            if len(raw["history"]) > _MAX_TOKEN_HISTORY:
                raw["history"] = raw["history"][-_MAX_TOKEN_HISTORY:]
            _merge_stats(
                raw,
                task,
                model,
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                cost_usd,
            )
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
        total = data.setdefault("total", {})
        if isinstance(total, dict):
            total.setdefault("cost_usd", 0.0)
        for bucket_name in ("by_model", "by_task"):
            buckets = data.get(bucket_name)
            if isinstance(buckets, dict):
                for bucket in buckets.values():
                    if isinstance(bucket, dict):
                        bucket.setdefault("cost_usd", 0.0)
        return data

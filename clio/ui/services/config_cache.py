"""Config cache for the UI server.

Provides a thread-safe LRU cache with precise change-fingerprint invalidation.
Extracted from server.py's make_handler closure to support testability.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from clio.config import AppConfig, load_config

# A change fingerprint for one config file: (st_mtime_ns, st_size).
_Fingerprint = tuple[int, int]


class ConfigCache:
    """Thread-safe LRU cache for project-specific AppConfig instances.

    - Keyed by project_dir (or '__global__' for no project).
    - Fingerprint-aware: re-reads config files when mtime (nanoseconds) or size changes.
    - Per-key locks so distinct projects never serialize on each other.
    - LRU eviction at maxsize (default 20).
    - Returns deep copies to prevent caller mutation.
    """

    def __init__(self, config_path: Path | None, maxsize: int = 20, on_load: Callable[..., Any] | None = None) -> None:
        self._config_path = config_path
        self._maxsize = maxsize
        self._on_load = on_load
        self._cache: dict[str, AppConfig] = {}
        self._meta: dict[str, tuple[_Fingerprint, _Fingerprint]] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def get(self, project_dir: Path | None = None) -> AppConfig:
        _GLOBAL_KEY = "__global__"
        key = _GLOBAL_KEY if project_dir is None else str(project_dir.resolve())

        cfg_fp = self._fingerprint(self._config_path)
        proj_fp = self._fingerprint(None if project_dir is None else project_dir / "project.yaml")

        with self._lock:
            key_lock = self._locks.get(key) or threading.Lock()
            self._locks[key] = key_lock

        with key_lock:
            with self._lock:
                if key in self._cache:
                    old_cfg_fp, old_proj_fp = self._meta.get(key, ((0, 0), (0, 0)))
                    if cfg_fp == old_cfg_fp and proj_fp == old_proj_fp:
                        return copy.deepcopy(self._cache[key])
                    del self._cache[key]
                    self._meta.pop(key, None)

            new_config = load_config(self._config_path or "config.yaml", project_dir=project_dir)

            with self._lock:
                if len(self._cache) >= self._maxsize:
                    oldest_key = next(iter(self._cache))
                    self._cache.pop(oldest_key)
                    self._meta.pop(oldest_key, None)
                    self._locks.pop(oldest_key, None)

                self._cache[key] = new_config
                self._meta[key] = (cfg_fp, proj_fp)
            if self._on_load:
                self._on_load(new_config)
            return copy.deepcopy(new_config)

    def invalidate_all(self) -> None:
        with self._lock:
            self._cache.clear()
            self._meta.clear()
            self._locks.clear()

    def invalidate_key(self, key: str) -> None:
        with self._lock:
            self._cache.pop(key, None)
            self._meta.pop(key, None)
            self._locks.pop(key, None)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._cache.keys())

    @staticmethod
    def _fingerprint(path: Path | None) -> _Fingerprint:
        """Precise change fingerprint for a file.

        ``st_size`` catches writes that land on the same nanosecond timestamp;
        ``st_mtime_ns`` catches sub-second edits. Falls back to float mtime
        when the filesystem does not expose nanosecond stat fields.
        """
        if path is None:
            return (0, 0)
        try:
            st = path.stat()
        except OSError:
            return (0, 0)
        ns = getattr(st, "st_mtime_ns", None)
        size = getattr(st, "st_size", None)
        if ns is None:
            ns = int(getattr(st, "st_mtime", 0) * 1_000_000_000)
        if size is None:
            size = 0
        return (ns, size)

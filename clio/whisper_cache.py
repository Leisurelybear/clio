from __future__ import annotations

import contextlib
from pathlib import Path

REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def model_cache_dir(cache_dir: Path, model_name: str) -> Path:
    return cache_dir / f"models--Systran--faster-whisper-{model_name}"


def model_snapshot_dir(cache_dir: Path, model_name: str) -> Path:
    return model_cache_dir(cache_dir, model_name) / "snapshots" / "downloaded"


def ensure_model_cache_refs(cache_dir: Path, model_name: str) -> None:
    repo_cache = model_cache_dir(cache_dir, model_name)
    refs = repo_cache / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    (refs / "main").write_text("downloaded", encoding="utf-8")


def is_model_cache_complete(cache_dir: Path, model_name: str) -> bool:
    snapshots = model_cache_dir(cache_dir, model_name) / "snapshots"
    if not snapshots.is_dir():
        return False
    for snap_dir in snapshots.iterdir():
        if not snap_dir.is_dir():
            continue
        complete = True
        for filename in REQUIRED_MODEL_FILES:
            path = snap_dir / filename
            try:
                valid = path.is_file() and path.stat().st_size > 0
            except OSError:
                valid = False
            if not valid:
                complete = False
                break
        if complete:
            return True
    return False


def largest_model_file_size(path: Path) -> int:
    max_size = 0
    if not path.is_dir():
        return 0
    for f in path.rglob("*"):
        if not f.is_file():
            continue
        with contextlib.suppress(OSError):
            max_size = max(max_size, f.stat().st_size)
    return max_size

# clio/desktop/state.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def state_path(config_dir: Path) -> Path:
    return Path(config_dir) / "desktop-state.json"


def load_last_dir(config_dir: Path) -> str | None:
    p = state_path(config_dir)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("last_dir") if isinstance(data, dict) else None
    if not raw:
        return None
    path = Path(str(raw))
    return str(path) if path.is_dir() else None


def save_last_dir(config_dir: Path, path: str, is_file: bool = False) -> None:
    target = Path(path).expanduser().resolve()
    folder = target.parent if is_file else target
    if not folder.is_dir():
        return
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"last_dir": str(folder)}
    state_path(config_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def resolve_initial_dir(
    config_dir: Path,
    preferred: str | None = None,
    base_dir: str | Path | None = None,
) -> str | None:
    if preferred:
        p = Path(preferred).expanduser()
        if not p.is_absolute():
            p = Path(base_dir or config_dir).expanduser() / p
        if p.is_file():
            p = p.parent
        if p.is_dir():
            return str(p.resolve())
    return load_last_dir(config_dir)

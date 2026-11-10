"""Protect user-edited generated artifacts before regeneration."""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

USER_EDITED_KEY = "_user_edited"
USER_EDITED_AT_KEY = "_user_edited_at"


class ArtifactOverwriteConfirmationRequired(RuntimeError):
    """Raised when regeneration would replace a user-edited artifact."""

    def __init__(self, message: str, artifacts: Iterable[Path] | None = None) -> None:
        super().__init__(message)
        self.artifacts = tuple(Path(path) for path in artifacts or ())


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def is_user_edited(data: Any) -> bool:
    return isinstance(data, dict) and data.get(USER_EDITED_KEY) is True


def mark_user_edited(data: dict[str, Any], *, edited_at: str | None = None) -> dict[str, Any]:
    data[USER_EDITED_KEY] = True
    data[USER_EDITED_AT_KEY] = edited_at or utc_now_iso()
    return data


def clear_user_edited(data: dict[str, Any]) -> dict[str, Any]:
    data.pop(USER_EDITED_KEY, None)
    data.pop(USER_EDITED_AT_KEY, None)
    return data


def read_user_edited(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return False
    return is_user_edited(data)


def artifact_requires_overwrite_confirmation(path: Path) -> bool:
    """Return True when an existing artifact is edited or cannot be parsed safely.

    A missing file is not a risk. Existing but unreadable, undecodable,
    non-object, or malformed JSON is treated as protected so regeneration never
    silently replaces an artifact the user may have edited.
    """
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return True
    if not isinstance(data, dict):
        return True
    return is_user_edited(data)


def backup_artifacts(paths: Iterable[Path]) -> list[Path]:
    """Create unique byte-for-byte backups without overwriting an existing one."""
    unique_paths = []
    seen: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        unique_paths.append(path)

    token = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    backups: list[Path] = []
    for path in unique_paths:
        backup = path.with_name(f"{path.name}.bak.{token}")
        with path.open("rb") as source, backup.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        try:
            os.chmod(backup, path.stat().st_mode & 0o777)
        except OSError:
            pass
        backups.append(backup)
    return backups

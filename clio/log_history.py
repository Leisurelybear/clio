"""Read-only access to hourly on-disk log files (R-027e).

The web UI can browse files produced by ``clio.log._HourlyFileHandler``
without exposing arbitrary filesystem reads. Only direct children named
``YYYY-MM-DD-HH.log`` under the configured logs directory are accepted.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_READ_BYTES = 2 * 1024 * 1024
MAX_READ_BYTES = 5 * 1024 * 1024

_LOG_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{2}\.log$")
_LINE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)$")
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _valid_log_name(name: str) -> bool:
    if not _LOG_NAME_RE.fullmatch(name):
        return False
    try:
        datetime.strptime(name[:-4], "%Y-%m-%d-%H")
    except ValueError:
        return False
    return True


def _logs_root(logs_dir: Path) -> Path:
    return Path(logs_dir).expanduser().resolve()


def _resolve_log_path(logs_dir: Path, name: str) -> tuple[Path, Path]:
    if not isinstance(name, str) or not _valid_log_name(name):
        raise ValueError("invalid log file name")

    root = _logs_root(logs_dir)
    candidate = root / name
    if candidate.is_symlink():
        raise ValueError("invalid log file name")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise FileNotFoundError("log file not found") from None

    if resolved.parent != root or not resolved.is_file():
        raise ValueError("invalid log file name")
    return root, resolved


def list_log_files(logs_dir: Path) -> list[dict[str, Any]]:
    """List valid hourly log files newest first.

    Each item contains ``name``, ``size`` and ``mtime`` (epoch seconds).
    Missing logs directories are treated as empty.
    """
    root = _logs_root(logs_dir)
    if not root.is_dir():
        return []

    files: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not _valid_log_name(entry.name):
            continue
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            stat = entry.stat()
        except OSError:
            continue
        files.append(
            {
                "name": entry.name,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return sorted(files, key=lambda item: str(item["name"]), reverse=True)


def _read_tail(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    size = path.stat().st_size
    with path.open("rb") as stream:
        if size <= max_bytes:
            return stream.read(), False
        stream.seek(size - max_bytes)
        data = stream.read(max_bytes)
    newline = data.find(b"\n")
    if newline < 0:
        return b"", True
    return data[newline + 1 :], True


def _parse_entry_timestamp(raw: str) -> float | None:
    try:
        return datetime.strptime(raw, _TS_FORMAT).timestamp()
    except ValueError:
        return None


def _parse_log_entries(text: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    previous_ts: float | None = None
    for index, raw_line in enumerate(text.splitlines()):
        line = raw_line.rstrip("\r")
        if index == 0:
            line = line.lstrip("\ufeff")
        if not line:
            continue
        match = _LINE_TS_RE.match(line)
        if match:
            parsed_ts = _parse_entry_timestamp(match.group(1))
            if parsed_ts is not None:
                previous_ts = parsed_ts
                entries.append({"ts": parsed_ts, "text": match.group(2)})
                continue
        entries.append({"ts": previous_ts, "text": line})
    return entries


def read_log_file(
    logs_dir: Path,
    name: str,
    *,
    max_bytes: int = DEFAULT_READ_BYTES,
) -> dict[str, Any]:
    """Read the tail of one hourly log file as UI-compatible entries."""
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise ValueError("max_bytes must be a positive integer")

    _, path = _resolve_log_path(logs_dir, name)
    stat = path.stat()
    data, truncated = _read_tail(path, min(max_bytes, MAX_READ_BYTES))
    text = data.decode("utf-8", errors="replace")
    return {
        "name": name,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "bytes_read": len(data),
        "truncated": truncated,
        "logs": _parse_log_entries(text),
    }

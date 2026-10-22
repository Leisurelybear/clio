"""Locate runtime resources that live outside the Python package tree."""

from __future__ import annotations

import sys
import sysconfig
from collections.abc import Iterable
from pathlib import Path


def _resource_roots() -> tuple[Path, ...]:
    package_root = Path(__file__).resolve().parent
    install_root = Path(sysconfig.get_path("data") or sys.prefix).resolve()
    candidates = (
        package_root,
        package_root.parent,
        install_root / "share" / "clio",
        Path(sys.prefix).resolve() / "share" / "clio",
        Path(sys.executable).resolve().parent.parent / "share" / "clio",
    )
    roots: list[Path] = []
    seen: set[Path] = set()
    for root in candidates:
        if root not in seen:
            seen.add(root)
            roots.append(root)
    return tuple(roots)


def find_resource(relative: str, *, roots: Iterable[Path] | None = None) -> Path | None:
    """Return the first existing resource path without allowing path escapes."""
    rel = Path(relative)
    if rel.is_absolute() or any(part in (".", "..") for part in rel.parts):
        return None
    for root in roots or _resource_roots():
        candidate = root / rel
        if candidate.is_file():
            return candidate.resolve()
    return None

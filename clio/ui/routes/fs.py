"""Authenticated filesystem browsing and reveal route handlers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

from clio._constants import VIDEO_EXTENSIONS
from clio.ui.services.file_service import _list_drives

# ── security: restrict file-system browsing to known-safe roots ──


def _is_allowed_path(resolved: Path) -> bool:
    """Allow browsing under home, or anywhere on a Windows drive letter.

    Local desktop tool: users need to pick originals on D:/E: etc. Restricting
    to drive roots only made the video manager unusable for external media.
    """
    try:
        if resolved.is_relative_to(Path.home()):
            return True
    except (ValueError, OSError):
        pass
    if sys.platform == "win32":
        # Any path with a drive letter (C:\..., D:\GoPro\..., UNC excluded)
        if resolved.drive and not resolved.drive.startswith("\\\\"):
            return True
    return False


def _browse_base_dir(handler: HandlerProtocol, qs: dict[str, Any], scope: str) -> Path:
    if scope == "project":
        return handler._resolve_project_dir(qs).expanduser().resolve()
    config_path = handler.config_path
    return (config_path.parent if config_path is not None else handler.project_dir).expanduser().resolve()


def handle_get_fs_dirs(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/fs/dirs."""
    dir_path = qs.get("path", [""])[0]
    if not dir_path:
        if sys.platform == "win32":
            drives = _list_drives()
            return handler._send_json({"path": "", "dirs": drives, "parent": None, "is_drive_list": True})
        return handler._send_json({"path": "/", "dirs": ["/"], "parent": None, "is_drive_list": True})
    try:
        resolved = Path(dir_path).resolve()
        if not _is_allowed_path(resolved):
            return handler._send_json({"error": "access denied"}, 403)
        if not resolved.is_dir():
            return handler._send_json({"error": "not a directory"}, 400)
        dirs: list[str] = []
        try:
            with os.scandir(resolved) as it:
                for entry in it:
                    if entry.is_dir() and not entry.name.startswith("."):
                        dirs.append(entry.path)
        except PermissionError:
            pass
        dirs.sort(key=lambda x: Path(x).name.lower())
        parent = str(resolved.parent) if resolved.parent != resolved else None
        return handler._send_json(
            {
                "path": str(resolved),
                "dirs": dirs,
                "parent": parent,
                "is_drive_list": False,
            }
        )
    except PermissionError:
        return handler._send_json({"error": "access denied"}, 403)
    except OSError as e:
        return handler._send_json({"error": str(e)}, 500)


def _entry_matches_kind(path: Path, kind: str) -> bool:
    if kind == "video":
        return path.suffix.lower() in VIDEO_EXTENSIONS
    if kind == "exe":
        if sys.platform == "win32":
            return path.suffix.lower() == ".exe"
        return os.access(path, os.X_OK)
    return True


def handle_get_fs_entries(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """List selectable directories and files for the in-app picker."""
    raw_path = (qs.get("path", [""])[0] or "").strip()
    kind = (qs.get("kind", ["any"])[0] or "any").strip().lower()
    scope = (qs.get("scope", ["project"])[0] or "project").strip().lower()
    if kind not in {"any", "video", "exe"}:
        return handler._send_json({"error": "invalid file kind"}, 400)
    if scope not in {"config", "project"}:
        return handler._send_json({"error": "invalid path scope"}, 400)

    if not raw_path and sys.platform == "win32":
        drives = _list_drives()
        return handler._send_json(
            {
                "path": "",
                "dirs": [{"name": drive, "path": drive} for drive in drives],
                "files": [],
                "parent": None,
                "is_drive_list": True,
            }
        )

    try:
        selected_path: str | None = None
        if raw_path:
            requested = Path(raw_path).expanduser()
            if not requested.is_absolute():
                requested = _browse_base_dir(handler, qs, scope) / requested
            resolved = requested.resolve()
        else:
            try:
                base = _browse_base_dir(handler, qs, scope)
            except (OSError, ValueError):
                base = Path.home()
            resolved = base if base.is_dir() and _is_allowed_path(base) else Path.home()
            resolved = resolved.resolve()
        if resolved.is_file():
            selected_path = str(resolved)
            resolved = resolved.parent
        if not _is_allowed_path(resolved):
            return handler._send_json({"error": "access denied"}, 403)
        if not resolved.is_dir():
            return handler._send_json({"error": "not a directory"}, 400)

        dirs: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        try:
            with os.scandir(resolved) as it:
                for entry in it:
                    if entry.name.startswith("."):
                        continue
                    try:
                        entry_path = Path(entry.path).resolve()
                        if not _is_allowed_path(entry_path):
                            continue
                        if entry.is_dir():
                            dirs.append({"name": entry.name, "path": str(entry_path)})
                        elif entry.is_file() and _entry_matches_kind(entry_path, kind):
                            files.append(
                                {
                                    "name": entry.name,
                                    "path": str(entry_path),
                                    "size": entry.stat().st_size,
                                }
                            )
                    except (OSError, PermissionError):
                        continue
        except PermissionError:
            pass

        dirs.sort(key=lambda item: item["name"].lower())
        files.sort(key=lambda item: item["name"].lower())
        parent_path: str | None = None
        if resolved.parent != resolved and _is_allowed_path(resolved.parent):
            parent_path = str(resolved.parent)
        return handler._send_json(
            {
                "path": str(resolved),
                "dirs": dirs,
                "files": files,
                "parent": parent_path,
                "is_drive_list": False,
                "selected_path": selected_path,
            }
        )
    except PermissionError:
        return handler._send_json({"error": "access denied"}, 403)
    except OSError as e:
        return handler._send_json({"error": str(e)}, 500)


def handle_post_fs_mkdir(handler: HandlerProtocol, obj: dict) -> None:
    """Handle POST /api/fs/mkdir — create a new directory."""
    parent_raw = (obj.get("parent") or "").strip()
    name = (obj.get("name") or "").strip()
    if not parent_raw or not name:
        return handler._send_json({"ok": False, "error": "parent and name required"}, 400)
    if "/" in name or "\\" in name or ".." in name:
        return handler._send_json({"ok": False, "error": "invalid name"}, 400)
    try:
        resolved = Path(parent_raw).resolve()
        if not _is_allowed_path(resolved):
            return handler._send_json({"ok": False, "error": "access denied"}, 403)
        new_dir = resolved / name
        if not _is_allowed_path(new_dir.resolve()):
            return handler._send_json({"ok": False, "error": "access denied"}, 403)
        new_dir.mkdir(parents=True, exist_ok=True)
        return handler._send_json({"ok": True, "path": str(new_dir)})
    except OSError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 500)


def build_reveal_command(
    path: Path,
    platform: str | None = None,
    *,
    select_file: bool = False,
) -> list[str]:
    """Build a platform file-manager command for a directory or selected file."""
    plat = platform if platform is not None else sys.platform
    target = str(path)
    if plat == "win32":
        return ["explorer.exe", f"/select,{target}"] if select_file else ["explorer.exe", target]
    if plat == "darwin":
        return ["open", "-R", target] if select_file else ["open", target]
    return ["xdg-open", str(path.parent) if select_file else target]


def reveal_path_in_file_manager(path: Path) -> Path:
    """Open a directory or select a file in the platform file manager."""
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"path not found: {resolved}")
    select_file = resolved.is_file()
    if sys.platform == "win32":
        if select_file:
            subprocess.Popen(build_reveal_command(resolved, select_file=True))
        else:
            os.startfile(str(resolved))  # type: ignore[attr-defined]
        return resolved
    subprocess.Popen(build_reveal_command(resolved, select_file=select_file))
    return resolved


def handle_post_fs_reveal(handler: HandlerProtocol, obj: dict) -> None:
    """Handle POST /api/fs/reveal for either a directory or a file."""
    raw = (obj.get("path") or "").strip()
    if not raw:
        return handler._send_json({"ok": False, "error": "path is required"}, 400)
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)
    if not _is_allowed_path(resolved):
        return handler._send_json({"ok": False, "error": "access denied"}, 403)
    if not resolved.exists():
        return handler._send_json({"ok": False, "error": "path not found"}, 400)
    try:
        opened = reveal_path_in_file_manager(resolved)
    except OSError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 500)
    return handler._send_json({"ok": True, "path": str(opened)})


def handle_get_fs_videos(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/fs/videos — list video files in a directory."""
    dir_path = qs.get("path", [""])[0]
    if not dir_path:
        return handler._send_json({"error": "path is required"}, 400)
    try:
        resolved = Path(dir_path).resolve()
        if not _is_allowed_path(resolved):
            return handler._send_json({"error": "access denied"}, 403)
        if not resolved.is_dir():
            return handler._send_json({"error": "not a directory"}, 400)
        files: list[dict[str, Any]] = []
        try:
            with os.scandir(resolved) as it:
                for entry in it:
                    if entry.is_dir() or entry.name.startswith("."):
                        continue
                    ext = Path(entry.name).suffix.lower()
                    if ext not in VIDEO_EXTENSIONS:
                        continue
                    st = entry.stat()
                    files.append(
                        {
                            "name": entry.name,
                            "path": entry.path,
                            "size": st.st_size,
                        }
                    )
        except PermissionError:
            pass
        files.sort(key=lambda f: f["name"].lower())
        parent = str(resolved.parent) if resolved.parent != resolved else None
        return handler._send_json(
            {
                "path": str(resolved),
                "files": files,
                "parent": parent,
            }
        )
    except PermissionError:
        return handler._send_json({"error": "access denied"}, 403)
    except OSError as e:
        return handler._send_json({"error": str(e)}, 500)

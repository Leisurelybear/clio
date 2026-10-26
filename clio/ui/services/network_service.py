"""Windows network location discovery for the in-app file picker.

The picker can browse any UNC path, but users still need a way to discover
servers and shares.  This module uses the Windows ``WNet*`` APIs and a few
Explorer registry hints so the picker can show a "Network" view instead of
requiring a hand-typed UNC path.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

RESOURCE_CONNECTED = 0x00000001
RESOURCE_GLOBALNET = 0x00000002
RESOURCETYPE_ANY = 0x00000000
RESOURCETYPE_DISK = 0x00000001

_NO_ERROR = 0
_ERROR_MORE_DATA = 234
_MAX_ENTRIES = 512
_INVALID_SERVER_CHARS = set('<>:"/\\|?*')


class _NETRESOURCEW(ctypes.Structure):
    _fields_ = [
        ("dwScope", ctypes.c_uint32),
        ("dwType", ctypes.c_uint32),
        ("dwDisplayType", ctypes.c_uint32),
        ("dwUsage", ctypes.c_uint32),
        ("lpLocalName", ctypes.c_wchar_p),
        ("lpRemoteName", ctypes.c_wchar_p),
        ("lpComment", ctypes.c_wchar_p),
        ("lpProvider", ctypes.c_wchar_p),
    ]


def normalize_network_server(value: str) -> str | None:
    """Return ``\\\\server`` for a valid server-only UNC value, else None."""
    raw = (value or "").strip().replace("/", "\\")
    if not raw.startswith("\\\\"):
        return None
    parts = [part for part in raw[2:].split("\\") if part]
    if len(parts) != 1:
        return None
    name = parts[0]
    if not name or name in {".", ".."} or len(name) > 255:
        return None
    if any(char in _INVALID_SERVER_CHARS or ord(char) < 32 for char in name):
        return None
    return f"\\\\{name}"


def _server_from_unc(value: str | None) -> str | None:
    raw = (value or "").strip().replace("/", "\\")
    if not raw.startswith("\\\\"):
        return None
    parts = [part for part in raw[2:].split("\\") if part]
    if not parts:
        return None
    return normalize_network_server(f"\\\\{parts[0]}")


def _load_mpr() -> Any | None:
    if sys.platform != "win32":
        return None
    try:
        return ctypes.WinDLL("mpr.dll")
    except OSError:
        return None


def _wnet_enum(
    scope: int,
    resource_type: int,
    usage: int,
    *,
    remote: str | None = None,
) -> list[dict[str, str]]:
    """Enumerate one level of Windows network resources."""
    mpr = _load_mpr()
    if mpr is None:
        return []

    open_enum = mpr.WNetOpenEnumW
    open_enum.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(_NETRESOURCEW),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    open_enum.restype = ctypes.c_uint32
    enum_resource = mpr.WNetEnumResourceW
    enum_resource.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    enum_resource.restype = ctypes.c_uint32
    close_enum = mpr.WNetCloseEnum
    close_enum.argtypes = [ctypes.c_void_p]
    close_enum.restype = ctypes.c_uint32

    resource = _NETRESOURCEW()
    resource.dwScope = scope
    resource.dwType = resource_type
    resource.dwUsage = usage
    if remote:
        resource.lpRemoteName = remote
    resource_ptr = ctypes.byref(resource) if remote else None

    handle = ctypes.c_void_p()
    if open_enum(scope, resource_type, usage, resource_ptr, ctypes.byref(handle)) != _NO_ERROR:
        return []

    out: list[dict[str, str]] = []
    try:
        while len(out) < _MAX_ENTRIES:
            count = ctypes.c_uint32(128)
            buffer = (_NETRESOURCEW * 128)()
            size = ctypes.c_uint32(ctypes.sizeof(buffer))
            result = enum_resource(
                handle, ctypes.byref(count), ctypes.cast(buffer, ctypes.c_void_p), ctypes.byref(size)
            )
            if result not in (_NO_ERROR, _ERROR_MORE_DATA):
                break
            for index in range(min(count.value, 128)):
                item = buffer[index]
                if not item.lpRemoteName:
                    continue
                out.append(
                    {
                        "remote": str(item.lpRemoteName),
                        "local": str(item.lpLocalName or ""),
                        "comment": str(item.lpComment or ""),
                    }
                )
                if len(out) >= _MAX_ENTRIES:
                    break
            if result == _NO_ERROR or count.value == 0:
                break
    finally:
        close_enum(handle)
    return out


def _global_servers() -> list[str]:
    providers = _wnet_enum(RESOURCE_GLOBALNET, RESOURCETYPE_ANY, 0)
    servers: list[str] = []
    for provider in providers:
        remote = provider.get("remote") or ""
        if remote.lower() != "microsoft windows network":
            continue
        for entry in _wnet_enum(RESOURCE_GLOBALNET, RESOURCETYPE_ANY, 0, remote=remote):
            server = _server_from_unc(entry.get("remote"))
            if server:
                servers.append(server)
    return servers


def _connected_servers() -> list[str]:
    servers: list[str] = []
    for entry in _wnet_enum(RESOURCE_CONNECTED, RESOURCETYPE_ANY, 0):
        server = _server_from_unc(entry.get("remote"))
        if server:
            servers.append(server)
    return servers


def _registry_servers() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows always has winreg
        return []

    servers: list[str] = []

    def add(value: str | None) -> None:
        server = _server_from_unc(value)
        if server:
            servers.append(server)

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Network") as key:
            index = 0
            while True:
                try:
                    drive = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, drive) as drive_key:
                        remote, _ = winreg.QueryValueEx(drive_key, "RemotePath")
                        if isinstance(remote, str):
                            add(remote)
                except OSError:
                    continue
    except OSError:
        pass

    try:
        path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            index = 0
            while True:
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                if name.startswith("##"):
                    add("\\\\" + name[2:].replace("#", "\\"))
    except OSError:
        pass

    try:
        path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Map Network Drive MRU"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            index = 0
            while True:
                try:
                    _, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if isinstance(value, str):
                    add(value)
    except OSError:
        pass

    return servers


def list_network_servers() -> list[dict[str, str]]:
    """Return discoverable SMB servers as ``{name, path}`` entries."""
    if sys.platform != "win32":
        return []
    found: dict[str, str] = {}
    for server in [*_global_servers(), *_connected_servers(), *_registry_servers()]:
        found.setdefault(server.lower(), server)
    return [{"name": server, "path": server} for server in sorted(found.values(), key=str.lower)]


def list_network_shares(server: str) -> list[dict[str, str]]:
    """Return disk shares for a validated ``\\\\server`` value."""
    normalized = normalize_network_server(server)
    if normalized is None or sys.platform != "win32":
        return []
    shares: list[dict[str, str]] = []
    for entry in _wnet_enum(RESOURCE_GLOBALNET, RESOURCETYPE_DISK, 0, remote=normalized):
        remote = entry.get("remote") or ""
        name = remote.rsplit("\\", 1)[-1]
        if not name or name.endswith("$"):
            continue
        shares.append({"name": name, "path": remote, "comment": entry.get("comment") or ""})
    shares.sort(key=lambda item: item["name"].lower())
    return shares

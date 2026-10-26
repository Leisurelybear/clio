from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace

from clio.ui.services import network_service


class _FakeKey:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeWinreg:
    HKEY_CURRENT_USER = object()

    def __init__(self, tree):
        self.tree = tree

    def OpenKey(self, root, path):
        if isinstance(root, _FakeKey):
            data = root.data.get(path)
        else:
            data = self.tree.get(path)
        if data is None:
            raise OSError(path)
        return _FakeKey(data)

    def EnumKey(self, key, index):
        names = [name for name in key.data if not name.startswith("__")]
        if index >= len(names):
            raise OSError(index)
        return names[index]

    def QueryValueEx(self, key, name):
        values = key.data.get("__values__", {})
        if name not in values:
            raise OSError(name)
        return values[name], 1

    def EnumValue(self, key, index):
        values = key.data.get("__values_list__", [])
        if index >= len(values):
            raise OSError(index)
        return values[index]


class TestNormalizeNetworkServer:
    def test_accepts_server_only_unc(self):
        assert network_service.normalize_network_server(r"\\nas") == r"\\nas"

    def test_normalizes_forward_slashes(self):
        assert network_service.normalize_network_server("//nas") == r"\\nas"

    def test_strips_trailing_separator(self):
        assert network_service.normalize_network_server("\\\\nas\\") == r"\\nas"

    def test_rejects_share_paths(self):
        assert network_service.normalize_network_server(r"\\nas\video") is None

    def test_rejects_non_unc_values(self):
        assert network_service.normalize_network_server("nas") is None
        assert network_service.normalize_network_server(r"C:\video") is None

    def test_rejects_empty_and_invalid_names(self):
        assert network_service.normalize_network_server(r"\\") is None
        assert network_service.normalize_network_server(r"\\bad|name") is None
        assert network_service.normalize_network_server("\\\\nas\\..") is None


class TestListNetworkServers:
    def test_non_windows_returns_empty(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "linux")
        assert network_service.list_network_servers() == []

    def test_deduplicates_and_sorts_sources(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "win32")
        monkeypatch.setattr(network_service, "_global_servers", lambda: [r"\\NAS", r"\\other"])
        monkeypatch.setattr(network_service, "_connected_servers", lambda: [r"\\nas"])
        monkeypatch.setattr(network_service, "_registry_servers", lambda: [r"\\third"])

        assert network_service.list_network_servers() == [
            {"name": r"\\NAS", "path": r"\\NAS"},
            {"name": r"\\other", "path": r"\\other"},
            {"name": r"\\third", "path": r"\\third"},
        ]


class TestListNetworkShares:
    def test_non_windows_returns_empty(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "linux")
        assert network_service.list_network_shares(r"\\nas") == []

    def test_rejects_invalid_server(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "win32")
        assert network_service.list_network_shares(r"\\nas\video") == []

    def test_filters_administrative_shares_and_sorts(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "win32")
        monkeypatch.setattr(
            network_service,
            "_wnet_enum",
            lambda *args, **kwargs: [
                {"remote": r"\\nas\video", "local": "", "comment": "Videos"},
                {"remote": r"\\nas\IPC$", "local": "", "comment": ""},
                {"remote": r"\\nas\music", "local": "", "comment": "Music"},
            ],
        )

        assert network_service.list_network_shares(r"\\nas") == [
            {"name": "music", "path": r"\\nas\music", "comment": "Music"},
            {"name": "video", "path": r"\\nas\video", "comment": "Videos"},
        ]


def _fake_mpr(open_result=0, enum_results=None):
    """Build a fake mpr.dll that fills the caller's _NETRESOURCEW buffer."""
    enum_results = list(enum_results or [])
    state = {"closed": [], "opened": []}

    def open_enum(scope, resource_type, usage, resource_ptr, handle_ptr):
        state["opened"].append((scope, resource_type, usage))
        return open_result

    def enum_resource(handle, count_ptr, buffer_ptr, size_ptr):
        items, count, more = enum_results.pop(0)
        count = min(count, 128)
        array = ctypes.cast(buffer_ptr, ctypes.POINTER(network_service._NETRESOURCEW))
        for index, item in enumerate(items[:count]):
            array[index].lpRemoteName = item.get("remote")
            array[index].lpLocalName = item.get("local")
            array[index].lpComment = item.get("comment")
        ctypes.cast(count_ptr, ctypes.POINTER(ctypes.c_uint32))[0] = count
        return 234 if more else 0

    def close_enum(handle):
        state["closed"].append(handle)
        return 0

    mpr = SimpleNamespace(
        WNetOpenEnumW=open_enum,
        WNetEnumResourceW=enum_resource,
        WNetCloseEnum=close_enum,
    )
    return mpr, state


class TestWnetEnum:
    def test_non_windows_returns_empty(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "linux")
        assert network_service._wnet_enum(2, 1, 0) == []

    def test_reads_entries_and_skips_missing_remote(self, monkeypatch):
        mpr, state = _fake_mpr(
            enum_results=[
                (
                    [
                        {"remote": r"\\nas\video", "local": "Z:", "comment": "Videos"},
                        {"remote": None},
                        {"remote": r"\\nas\music"},
                    ],
                    3,
                    False,
                )
            ]
        )
        monkeypatch.setattr(network_service, "_load_mpr", lambda: mpr)

        assert network_service._wnet_enum(2, 1, 0, remote=r"\\nas") == [
            {"remote": r"\\nas\video", "local": "Z:", "comment": "Videos"},
            {"remote": r"\\nas\music", "local": "", "comment": ""},
        ]
        assert state["opened"] == [(2, 1, 0)]
        assert len(state["closed"]) == 1

    def test_open_failure_returns_empty_without_closing(self, monkeypatch):
        mpr, state = _fake_mpr(open_result=5)
        monkeypatch.setattr(network_service, "_load_mpr", lambda: mpr)

        assert network_service._wnet_enum(2, 1, 0) == []
        assert state["closed"] == []

    def test_enum_error_breaks_and_closes(self, monkeypatch):
        mpr, state = _fake_mpr(enum_results=[([{"remote": r"\\nas\share"}], 1, True)])
        mpr.WNetEnumResourceW = lambda *args: 87
        monkeypatch.setattr(network_service, "_load_mpr", lambda: mpr)

        assert network_service._wnet_enum(2, 1, 0) == []
        assert len(state["closed"]) == 1

    def test_more_data_continues_until_last_page(self, monkeypatch):
        mpr, _state = _fake_mpr(
            enum_results=[
                ([{"remote": r"\\nas\one"}], 1, True),
                ([{"remote": r"\\nas\two"}], 1, False),
            ]
        )
        monkeypatch.setattr(network_service, "_load_mpr", lambda: mpr)

        assert [item["remote"] for item in network_service._wnet_enum(2, 1, 0)] == [
            r"\\nas\one",
            r"\\nas\two",
        ]


class TestServerFromUnc:
    def test_extracts_server_from_share(self):
        assert network_service._server_from_unc(r"\\nas\video\trip") == r"\\nas"

    def test_normalizes_forward_slashes(self):
        assert network_service._server_from_unc("//nas/video") == r"\\nas"

    def test_rejects_missing_or_invalid_values(self):
        assert network_service._server_from_unc(None) is None
        assert network_service._server_from_unc("") is None
        assert network_service._server_from_unc("nas") is None
        assert network_service._server_from_unc(r"\\") is None


class TestGlobalServers:
    def test_walks_the_windows_network_provider(self, monkeypatch):
        calls = []

        def fake_enum(scope, resource_type, usage, *, remote=None):
            calls.append(remote)
            if remote is None:
                return [
                    {"remote": "Microsoft Windows Network"},
                    {"remote": "Unrelated Provider"},
                ]
            return [{"remote": r"\\nas\video"}, {"remote": "invalid"}]

        monkeypatch.setattr(network_service, "_wnet_enum", fake_enum)

        assert network_service._global_servers() == [r"\\nas"]
        assert calls == [None, "Microsoft Windows Network"]


class TestConnectedServers:
    def test_keeps_servers_from_connected_resources(self, monkeypatch):
        monkeypatch.setattr(
            network_service,
            "_wnet_enum",
            lambda *args, **kwargs: [
                {"remote": r"\\nas\video"},
                {"remote": None},
                {"remote": r"\\other\share"},
            ],
        )

        assert network_service._connected_servers() == [r"\\nas", r"\\other"]


class TestRegistryServers:
    def test_collects_network_mount_and_mru_hints(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "win32")
        fake = _FakeWinreg(
            {
                "Network": {
                    "Z:": {"__values__": {"RemotePath": r"\\registry\video"}},
                },
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2": {
                    "##mount#video": {},
                    "C:": {},
                },
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Map Network Drive MRU": {
                    "__values_list__": [
                        ("a", r"\\mru\share", 1),
                        ("b", r"\\another\music", 1),
                    ],
                },
            }
        )
        monkeypatch.setitem(sys.modules, "winreg", fake)

        assert network_service._registry_servers() == [
            r"\\registry",
            r"\\mount",
            r"\\mru",
            r"\\another",
        ]

    def test_missing_registry_keys_return_empty(self, monkeypatch):
        monkeypatch.setattr(network_service.sys, "platform", "win32")
        monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg({}))

        assert network_service._registry_servers() == []

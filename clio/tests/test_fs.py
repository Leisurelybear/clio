from __future__ import annotations

from pathlib import Path, PureWindowsPath
from unittest.mock import MagicMock

from clio.ui.routes.fs import (
    _is_allowed_path,
    build_reveal_command,
    handle_get_fs_dirs,
    handle_get_fs_entries,
    handle_get_fs_videos,
    handle_post_fs_mkdir,
    handle_post_fs_reveal,
)


class TestIsAllowedPath:
    def test_home_dir_returns_true(self):
        home = Path.home()
        p = home / "subdir" / "project"
        assert _is_allowed_path(p) is True

    def test_non_home_returns_false_on_linux(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        p = Path("/nonexistent_test_path_xyz")
        assert _is_allowed_path(p) is False

    def test_root_drive_win32_returns_true(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        p = PureWindowsPath("C:\\")
        assert _is_allowed_path(p) is True

    def test_root_drive_linux_returns_false(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        p = Path("/")
        assert _is_allowed_path(p) is False

    def test_unc_path_win32_returns_false(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        assert _is_allowed_path(PureWindowsPath(r"\\server\share\trip")) is False


class TestHandleGetFsDirs:
    def test_empty_path_win32_returns_drives(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("clio.ui.routes.fs._list_drives", lambda: ["C:\\", "D:\\"])
        handler = MagicMock()

        handle_get_fs_dirs(handler, {"path": [""]})

        handler._send_json.assert_called_once_with(
            {"path": "", "dirs": ["C:\\", "D:\\"], "parent": None, "is_drive_list": True}
        )

    def test_empty_path_linux_returns_root(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        handler = MagicMock()

        handle_get_fs_dirs(handler, {"path": [""]})

        handler._send_json.assert_called_once_with({"path": "/", "dirs": ["/"], "parent": None, "is_drive_list": True})

    def test_valid_path_returns_sorted_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        (tmp_path / "b_dir").mkdir()
        (tmp_path / "a_dir").mkdir()
        (tmp_path / ".hidden").mkdir()
        f = tmp_path / "file.txt"
        f.write_bytes(b"")

        handler = MagicMock()
        handle_get_fs_dirs(handler, {"path": [str(tmp_path)]})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args.args[0]
        assert payload["dirs"] == [
            str(tmp_path / "a_dir"),
            str(tmp_path / "b_dir"),
        ]
        assert payload["is_drive_list"] is False
        assert payload["path"] == str(tmp_path.resolve())
        assert payload["parent"] == str(tmp_path.parent)

    def test_path_traversal_returns_403(self, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: False)
        handler = MagicMock()

        handle_get_fs_dirs(handler, {"path": [".."]})

        handler._send_json.assert_called_once_with({"error": "access denied"}, 403)

    def test_non_directory_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        f = tmp_path / "file.txt"
        f.write_bytes(b"")

        handler = MagicMock()
        handle_get_fs_dirs(handler, {"path": [str(f)]})

        handler._send_json.assert_called_once_with({"error": "not a directory"}, 400)

    def test_scandir_permission_error_returns_empty_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)

        def mock_scandir(_path):
            raise PermissionError("access denied")

        monkeypatch.setattr("os.scandir", mock_scandir)

        handler = MagicMock()
        handle_get_fs_dirs(handler, {"path": [str(tmp_path)]})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args.args[0]
        assert payload["dirs"] == []

    def test_permission_error_returns_403(self, monkeypatch):
        def mock_is_allowed(resolved):
            raise PermissionError("access denied")

        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", mock_is_allowed)

        handler = MagicMock()
        handle_get_fs_dirs(handler, {"path": ["some/path"]})

        handler._send_json.assert_called_once_with({"error": "access denied"}, 403)

    def test_os_error_returns_500(self, monkeypatch):
        def mock_resolve(self, strict=False):
            raise OSError("disk failure")

        monkeypatch.setattr("pathlib.Path.resolve", mock_resolve)

        handler = MagicMock()
        handle_get_fs_dirs(handler, {"path": ["some/path"]})

        handler._send_json.assert_called_once_with({"error": "disk failure"}, 500)


class TestHandleGetFsVideos:
    def test_requires_path(self):
        handler = MagicMock()
        handle_get_fs_videos(handler, {"path": [""]})
        handler._send_json.assert_called_once_with({"error": "path is required"}, 400)

    def test_access_denied(self, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: False)
        handler = MagicMock()
        handle_get_fs_videos(handler, {"path": ["C:\\Windows"]})
        handler._send_json.assert_called_once_with({"error": "access denied"}, 403)

    def test_non_directory_returns_400(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        f = tmp_path / "file.txt"
        f.write_bytes(b"")
        handler = MagicMock()
        handle_get_fs_videos(handler, {"path": [str(f)]})
        handler._send_json.assert_called_once_with({"error": "not a directory"}, 400)

    def test_lists_video_files_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        (tmp_path / "a.mp4").write_bytes(b"")
        (tmp_path / "b.mov").write_bytes(b"")
        (tmp_path / "c.txt").write_bytes(b"")
        (tmp_path / ".hidden.mp4").write_bytes(b"")

        handler = MagicMock()
        handle_get_fs_videos(handler, {"path": [str(tmp_path)]})

        handler._send_json.assert_called_once()
        payload = handler._send_json.call_args.args[0]
        assert payload["path"] == str(tmp_path.resolve())
        assert len(payload["files"]) == 2
        assert [f["name"] for f in payload["files"]] == ["a.mp4", "b.mov"]
        assert payload["parent"] == str(tmp_path.parent)

    def test_includes_file_sizes(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        f = tmp_path / "v.mp4"
        f.write_bytes(b"x" * 1234)
        handler = MagicMock()
        handle_get_fs_videos(handler, {"path": [str(tmp_path)]})
        payload = handler._send_json.call_args.args[0]
        assert payload["files"][0]["size"] == 1234

    def test_permission_error_returns_403(self, monkeypatch):
        def mock_is_allowed(resolved):
            raise PermissionError("access denied")

        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", mock_is_allowed)
        handler = MagicMock()
        handle_get_fs_videos(handler, {"path": ["some/path"]})
        handler._send_json.assert_called_once_with({"error": "access denied"}, 403)

    def test_os_error_returns_500(self, monkeypatch):
        def mock_resolve(self, strict=False):
            raise OSError("disk failure")

        monkeypatch.setattr("pathlib.Path.resolve", mock_resolve)
        handler = MagicMock()
        handle_get_fs_videos(handler, {"path": ["some/path"]})
        handler._send_json.assert_called_once_with({"error": "disk failure"}, 500)

    def test_drive_subdir_win32_returns_true(self, monkeypatch):
        """Any path on a Windows drive letter is allowed (not just drive root)."""
        monkeypatch.setattr("sys.platform", "win32")
        p = PureWindowsPath(r"D:\GoPro\trip1")
        assert _is_allowed_path(p) is True


class TestHandleGetFsEntries:
    def test_empty_path_win32_lists_drives(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr("clio.ui.routes.fs._list_drives", lambda: ["C:\\", "D:\\"])
        handler = MagicMock()

        handle_get_fs_entries(handler, {"path": [""], "kind": ["video"]})

        handler._send_json.assert_called_once_with(
            {
                "path": "",
                "dirs": [
                    {"name": "C:\\", "path": "C:\\"},
                    {"name": "D:\\", "path": "D:\\"},
                ],
                "files": [],
                "parent": None,
                "is_drive_list": True,
            }
        )

    def test_lists_dirs_and_filters_video_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        (tmp_path / "b-dir").mkdir()
        (tmp_path / "a-dir").mkdir()
        (tmp_path / "clip.mp4").write_bytes(b"video")
        (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
        handler = MagicMock()

        handle_get_fs_entries(handler, {"path": [str(tmp_path)], "kind": ["video"]})

        payload = handler._send_json.call_args.args[0]
        assert [entry["name"] for entry in payload["dirs"]] == ["a-dir", "b-dir"]
        assert [entry["name"] for entry in payload["files"]] == ["clip.mp4"]
        assert payload["files"][0]["size"] == 5
        assert payload["path"] == str(tmp_path.resolve())

    def test_empty_path_uses_config_scope_base(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        config_dir = tmp_path / "settings"
        config_dir.mkdir()
        handler = MagicMock()
        handler.config_path = config_dir / "config.yaml"
        handler.project_dir = tmp_path / "project"

        handle_get_fs_entries(handler, {"path": [""], "kind": ["any"], "scope": ["config"]})

        payload = handler._send_json.call_args.args[0]
        assert payload["path"] == str(config_dir.resolve())

    def test_file_initial_path_opens_parent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        file_path = tmp_path / "tool.bin"
        file_path.write_bytes(b"x")
        handler = MagicMock()

        handle_get_fs_entries(handler, {"path": [str(file_path)], "kind": ["any"]})

        payload = handler._send_json.call_args.args[0]
        assert payload["path"] == str(tmp_path.resolve())
        assert [entry["name"] for entry in payload["files"]] == ["tool.bin"]
        assert payload["selected_path"] == str(file_path.resolve())

    def test_relative_path_uses_project_scope(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        project_dir = tmp_path / "project"
        output_dir = project_dir / "output"
        output_dir.mkdir(parents=True)
        handler = MagicMock()
        handler._resolve_project_dir.return_value = project_dir
        handler.config_path = tmp_path / "config.yaml"
        handler.project_dir = project_dir

        handle_get_fs_entries(
            handler,
            {"path": ["./output"], "kind": ["any"], "scope": ["project"]},
        )

        payload = handler._send_json.call_args.args[0]
        assert payload["path"] == str(output_dir.resolve())

    def test_relative_path_uses_config_scope(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        config_dir = tmp_path / "settings"
        logs_dir = config_dir / "logs"
        logs_dir.mkdir(parents=True)
        handler = MagicMock()
        handler.config_path = config_dir / "config.yaml"
        handler.project_dir = tmp_path / "project"

        handle_get_fs_entries(
            handler,
            {"path": ["./logs"], "kind": ["any"], "scope": ["config"]},
        )

        payload = handler._send_json.call_args.args[0]
        assert payload["path"] == str(logs_dir.resolve())

    def test_invalid_kind_returns_400(self):
        handler = MagicMock()
        handle_get_fs_entries(handler, {"kind": ["secret"]})
        handler._send_json.assert_called_once_with({"error": "invalid file kind"}, 400)

    def test_invalid_scope_returns_400(self):
        handler = MagicMock()
        handle_get_fs_entries(handler, {"scope": ["system"]})
        handler._send_json.assert_called_once_with({"error": "invalid path scope"}, 400)


class TestHandlePostFsMkdir:
    def test_missing_parent_returns_400(self):
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"name": "newdir"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "parent and name required"}, 400)

    def test_missing_name_returns_400(self):
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": "/tmp"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "parent and name required"}, 400)

    def test_empty_name_returns_400(self):
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": "/tmp", "name": "  "})
        handler._send_json.assert_called_once_with({"ok": False, "error": "parent and name required"}, 400)

    def test_name_with_slash_returns_400(self):
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": "/tmp", "name": "a/b"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "invalid name"}, 400)

    def test_name_with_backslash_returns_400(self):
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": "/tmp", "name": "a\\b"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "invalid name"}, 400)

    def test_name_with_dotdot_returns_400(self):
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": "/tmp", "name": ".."})
        handler._send_json.assert_called_once_with({"ok": False, "error": "invalid name"}, 400)

    def test_parent_not_allowed_returns_403(self, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: False)
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": "/tmp", "name": "newdir"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "access denied"}, 403)

    def test_new_dir_not_allowed_returns_403(self, tmp_path, monkeypatch):
        """New dir resolves outside the allowed tree after symlink."""
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: p == tmp_path.resolve())
        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": str(tmp_path), "name": "newdir"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "access denied"}, 403)

    def test_os_error_returns_500(self, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)

        def mock_mkdir(*a, **kw):
            raise OSError("permission denied")

        monkeypatch.setattr("pathlib.Path.mkdir", mock_mkdir)

        handler = MagicMock()
        handle_post_fs_mkdir(handler, {"parent": "/tmp", "name": "newdir"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "permission denied"}, 500)


class TestBuildRevealCommand:
    def test_darwin(self):
        p = Path.cwd()
        assert build_reveal_command(p, "darwin") == ["open", str(p)]

    def test_linux(self):
        p = Path.cwd()
        assert build_reveal_command(p, "linux") == ["xdg-open", str(p)]

    def test_reveal_file_commands(self, tmp_path):
        p = tmp_path / "clip.mp4"
        assert build_reveal_command(p, "win32", select_file=True) == ["explorer.exe", f"/select,{p}"]
        assert build_reveal_command(p, "darwin", select_file=True) == ["open", "-R", str(p)]
        assert build_reveal_command(p, "linux", select_file=True) == ["xdg-open", str(tmp_path)]


class TestHandlePostFsReveal:
    def test_missing_path_returns_400(self):
        handler = MagicMock()
        handle_post_fs_reveal(handler, {})
        handler._send_json.assert_called_once_with({"ok": False, "error": "path is required"}, 400)

    def test_access_denied(self, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: False)
        handler = MagicMock()
        handle_post_fs_reveal(handler, {"path": "/etc"})
        handler._send_json.assert_called_once_with({"ok": False, "error": "access denied"}, 403)

    def test_missing_path_on_disk(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        handler = MagicMock()
        handle_post_fs_reveal(handler, {"path": str(tmp_path / "missing.txt")})
        handler._send_json.assert_called_once_with({"ok": False, "error": "path not found"}, 400)

    def test_opens_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        file_path = tmp_path / "clip.mp4"
        file_path.write_bytes(b"x")
        opened: list[Path] = []
        monkeypatch.setattr(
            "clio.ui.routes.fs.reveal_path_in_file_manager",
            lambda path: opened.append(path) or path.resolve(),
        )
        handler = MagicMock()

        handle_post_fs_reveal(handler, {"path": str(file_path)})

        assert opened == [file_path.resolve()]
        assert handler._send_json.call_args.args[0]["ok"] is True

    def test_opens_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr("clio.ui.routes.fs._is_allowed_path", lambda p: True)
        opened: list[Path] = []

        def fake_reveal(path: Path) -> Path:
            opened.append(path)
            return path.resolve()

        monkeypatch.setattr("clio.ui.routes.fs.reveal_path_in_file_manager", fake_reveal)
        handler = MagicMock()
        handle_post_fs_reveal(handler, {"path": str(tmp_path)})
        assert opened and opened[0].resolve() == tmp_path.resolve()
        payload = handler._send_json.call_args.args[0]
        assert payload["ok"] is True
        assert Path(payload["path"]).resolve() == tmp_path.resolve()

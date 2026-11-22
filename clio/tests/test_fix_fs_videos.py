"""Regression test: one unreadable entry must not fail /api/fs/videos."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.routes.fs import handle_get_fs_videos


class _Entry:
    def __init__(self, path: Path, *, broken: bool = False) -> None:
        self.name = path.name
        self.path = str(path)
        self._broken = broken
        self._real = path

    def is_dir(self) -> bool:
        return False

    def stat(self):
        if self._broken:
            raise FileNotFoundError(self.path)
        return self._real.stat()


class _Scan:
    def __init__(self, entries):
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc):
        return False


def test_broken_entry_is_skipped(tmp_path: Path):
    good = tmp_path / "good.mp4"
    good.write_bytes(b"1234")
    entries = [_Entry(tmp_path / "dangling.mp4", broken=True), _Entry(good)]
    handler = MagicMock()

    with (
        patch("clio.ui.routes.fs._is_allowed_path", return_value=True),
        patch.object(os, "scandir", return_value=_Scan(entries)),
    ):
        handle_get_fs_videos(handler, {"path": [str(tmp_path)]})

    args = handler._send_json.call_args.args
    assert len(args) == 1  # 200, not 500
    assert [f["name"] for f in args[0]["files"]] == ["good.mp4"]

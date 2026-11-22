"""Regression tests for E-04b: atomic POST /api/videos/selected/remove."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import clio.ui.server as server
from clio.ui.route_table import build_router
from clio.ui.routes.videos import handle_post_videos_selected_remove


def _handler(proj_dir: Path) -> MagicMock:
    handler = MagicMock()
    handler._resolve_project_dir.return_value = proj_dir
    handler._send_json = MagicMock()
    return handler


def _write_selection(proj_dir: Path, paths: list[Path]) -> None:
    (proj_dir / "videos.json").write_text(json.dumps([str(p) for p in paths]), encoding="utf-8")


def _stored(proj_dir: Path) -> list[str]:
    return json.loads((proj_dir / "videos.json").read_text(encoding="utf-8"))


def test_removes_only_requested_paths(tmp_path: Path) -> None:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    a, b, c = (tmp_path / f"{name}.mp4" for name in "abc")
    for p in (a, b, c):
        p.write_bytes(b"x")
    _write_selection(proj_dir, [a, b, c])
    handler = _handler(proj_dir)

    handle_post_videos_selected_remove(handler, {}, {"paths": [str(b), str(tmp_path / "gone.mp4")]})

    payload = handler._send_json.call_args[0][0]
    assert payload["ok"] is True
    assert payload["removed"] == [str(b)]
    assert payload["videos"] == [str(a), str(c)]
    assert _stored(proj_dir) == [str(a), str(c)]


def test_reads_current_file_inside_lock(tmp_path: Path) -> None:
    """An entry added after the client loaded its list must survive the removal."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _write_selection(proj_dir, [a])
    _write_selection(proj_dir, [a, b])  # concurrent add from another tab
    handler = _handler(proj_dir)

    handle_post_videos_selected_remove(handler, {}, {"paths": [str(a)]})

    assert _stored(proj_dir) == [str(b)]


def test_no_match_leaves_file_untouched(tmp_path: Path) -> None:
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    a = tmp_path / "a.mp4"
    _write_selection(proj_dir, [a])
    before = (proj_dir / "videos.json").read_bytes()
    handler = _handler(proj_dir)

    handle_post_videos_selected_remove(handler, {}, {"paths": [str(tmp_path / "other.mp4")]})

    payload = handler._send_json.call_args[0][0]
    assert payload == {"ok": True, "removed": [], "videos": [str(a)]}
    assert (proj_dir / "videos.json").read_bytes() == before


def test_rejects_non_list_paths(tmp_path: Path) -> None:
    handler = _handler(tmp_path)

    handle_post_videos_selected_remove(handler, {}, {"paths": "a.mp4"})

    args = handler._send_json.call_args[0]
    assert args[0]["ok"] is False
    assert args[1] == 400


def test_route_is_registered_and_resolvable() -> None:
    router = build_router(lambda name: getattr(server, name, None))
    handler, _, route = router.dispatch("POST", "/api/videos/selected/remove")
    assert route is not None and route.auth_required
    assert handler is handle_post_videos_selected_remove

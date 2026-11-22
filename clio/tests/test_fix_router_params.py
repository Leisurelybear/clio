"""Regression tests: router decodes path params once; vmeta glob is escaped."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.ui.routes.videos import handle_get_vmeta
from clio.ui.router import Route, Router


def test_dispatch_percent_decodes_path_params():
    router = Router()
    router.add(Route("GET", "/api/vmeta/{stem}", "h"))
    _, kwargs, _ = router.dispatch("GET", "/api/vmeta/%E6%97%85%E8%A1%8C_01")
    assert kwargs == {"stem": "旅行_01"}


def test_dispatch_decodes_only_once():
    router = Router()
    router.add(Route("POST", "/api/credentials/{credential_id}/reveal", "h"))
    _, kwargs, _ = router.dispatch("POST", "/api/credentials/a%2541/reveal")
    assert kwargs == {"credential_id": "a%41"}


def test_vmeta_stem_with_brackets_is_not_a_glob_class(tmp_path: Path):
    comp = tmp_path / "compressed"
    comp.mkdir()
    (comp / "a.mp4").write_bytes(b"x")  # would match the unescaped pattern "[ab].*"
    handler = MagicMock()
    handler._resolve_project_dir.return_value = tmp_path
    handler._get_project_output.return_value = tmp_path

    with (
        patch("clio.ui.routes.videos.VideoMeta.read", return_value=object()),
        patch("clio.ui.routes.videos._meta_to_dict", return_value={"ok": True}),
    ):
        handle_get_vmeta(handler, {}, "[ab]")

    handler._send_json.assert_called_once_with({"ok": False, "error": "not found"}, 404)

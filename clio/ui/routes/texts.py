"""Route handlers: /api/texts, /api/voiceover, /api/cover"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio.ui.services.file_service import _save_atomic

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_SAFE_COVER_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _detect_cover_content_type(data: bytes) -> str | None:
    """Return an image Content-Type from magic bytes, or None if not a trusted bitmap."""
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def handle_get_texts(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/texts."""
    proj_out = handler._get_project_output(qs)
    fname = qs.get("file", [""])[0]
    p = handler._resolve_texts(fname, proj_out)
    if p is None:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    handler._send_bytes(p.read_bytes(), "application/json; charset=utf-8")


def handle_get_voiceover(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/voiceover."""
    proj_out = handler._get_project_output(qs)
    fname = qs.get("file", [""])[0]
    p = handler._resolve_in("scripts", fname, proj_out)
    if p is None:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    handler._send_bytes(p.read_bytes(), "application/json; charset=utf-8")


def handle_get_cover(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/cover?file=<name.jpg> — AI-picked cover frame under covers/.

    GAP-P2-11: only allow image extensions, verify magic bytes, and send nosniff /
    restrictive CSP so a planted non-image cannot become active same-origin content.
    """
    raw = qs.get("file", [""])[0]
    basename = Path(str(raw).replace("\\", "/")).name
    suffix = Path(basename).suffix.lower()
    if suffix not in _COVER_EXTS or not _SAFE_COVER_NAME.match(basename):
        return handler._send_json({"ok": False, "error": "仅允许 jpg/png/webp 封面文件"}, 403)
    proj_out = handler._get_project_output(qs)
    p = handler._resolve_in("covers", basename, proj_out)
    if p is None:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    data = p.read_bytes()
    ct = _detect_cover_content_type(data)
    if ct is None:
        return handler._send_json({"ok": False, "error": "封面不是有效图片"}, 403)
    handler._send_bytes(
        data,
        ct,
        extra_headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{basename}"',
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; sandbox",
        },
    )


def handle_put_texts(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/texts."""
    proj_out = handler._get_project_output(qs)
    fname = qs.get("file", [""])[0]
    p = handler._resolve_texts(fname, proj_out)
    if p is None:
        return handler._send_json({"ok": False, "error": "forbidden or not found"}, 403)
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    _save_atomic(p, data)
    handler._send_json({"ok": True, "path": str(p)})


def handle_put_voiceover(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/voiceover."""
    proj_out = handler._get_project_output(qs)
    fname = qs.get("file", [""])[0]
    p = handler._resolve_in("scripts", fname, proj_out)
    if p is None:
        return handler._send_json({"ok": False, "error": "forbidden or not found"}, 403)
    data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
    _save_atomic(p, data)
    handler._send_json({"ok": True, "path": str(p)})

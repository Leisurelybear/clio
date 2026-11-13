"""Route handlers: /api/texts, /api/voiceover, /api/cover"""

from __future__ import annotations

import json
import re
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from clio.artifact_guard import mark_user_edited
from clio.i18n import t
from clio.tasks._helpers import _rewrite_script_md, _rewrite_text_file
from clio.ui.services.file_service import (
    ArtifactConflictError,
    read_with_version,
    request_version,
    save_with_version,
    version_headers,
)

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_COVER_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_SAFE_COVER_NAME = re.compile(r"^[^\x00-\x1f\x7f/\\<>:\"|?*]+$")
_ANALYSIS_SERVER_FIELDS = {
    "_lineage",
    "_lineage_components",
    "_schema_version",
    "compressed_file",
    "cover_file",
    "id",
    "index",
    "media_identity",
    "source_file",
}


def _content_disposition(basename: str) -> str:
    """RFC 6266 header — ASCII fallback + UTF-8 encoded filename* for non-ASCII names.

    Python's BaseHTTPRequestHandler encodes header values as latin-1, so a raw
    non-ASCII filename would crash the request (ERR_EMPTY_RESPONSE).
    """
    if basename.isascii():
        return f'inline; filename="{basename}"'
    fallback = basename.encode("ascii", "replace").decode("ascii")
    return f"inline; filename=\"{fallback}\"; filename*=UTF-8''{quote(basename, safe='')}"


def _detect_cover_content_type(data: bytes) -> str | None:
    """Return an image Content-Type from magic bytes, or None if not a trusted bitmap."""
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _analysis_payload_error(obj: dict) -> str | None:
    """Return an i18n error message when an analysis payload is malformed.

    The editor saves the whole analysis object, so a bad type here would only
    surface later as a broken plan or voiceover prompt. Reject it at the route.
    """
    title = obj.get("title")
    if not isinstance(title, str) or not title.strip():
        return t("api.texts_title_required")
    summary = obj.get("summary")
    if summary is not None and not isinstance(summary, str):
        return t("api.texts_field_type", {"field": "summary", "expected": "string"})
    for key in ("location", "mood", "suggested_use", "cover_timestamp", "cover_file"):
        value = obj.get(key)
        if value is not None and not isinstance(value, str):
            return t("api.texts_field_type", {"field": key, "expected": "string"})
    timeline = obj.get("timeline")
    if not isinstance(timeline, list):
        return t("api.texts_timeline_invalid")
    for position, item in enumerate(timeline, start=1):
        if not isinstance(item, dict):
            return t("api.texts_timeline_entry_invalid", {"position": position})
        for key in ("start", "end"):
            value = item.get(key)
            if isinstance(value, str):
                if not value.strip():
                    return t("api.texts_timeline_entry_invalid", {"position": position})
            elif isinstance(value, bool) or not isinstance(value, (int, float)):
                return t("api.texts_timeline_entry_invalid", {"position": position})
        if "description" in item and not isinstance(item["description"], str):
            return t("api.texts_timeline_entry_invalid", {"position": position})
    highlights = obj.get("highlights")
    if highlights is not None:
        if not isinstance(highlights, list):
            return t("api.texts_field_type", {"field": "highlights", "expected": "array"})
        if any(not isinstance(item, (dict, str)) for item in highlights):
            return t("api.texts_highlights_entry_invalid")
    return None


def _voiceover_payload_error(obj: dict) -> str | None:
    """Return an i18n error message when a voiceover payload is malformed."""
    voiceover = obj.get("voiceover")
    if not isinstance(voiceover, str) or not voiceover.strip():
        return t("api.texts_voiceover_required")
    for key in ("title", "edit_tip"):
        value = obj.get(key)
        if value is not None and not isinstance(value, str):
            return t("api.texts_field_type", {"field": key, "expected": "string"})
    duration = obj.get("duration_hint_sec")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0):
        return t(
            "api.texts_field_type",
            {"field": "duration_hint_sec", "expected": t("api.texts_non_negative_number")},
        )
    return None


def _read_existing_object(path: Path) -> tuple[dict | None, str | None]:
    """Read an existing JSON artifact and its version for optimistic writes."""
    try:
        raw, version = read_with_version(path)
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, None
    return (parsed, version) if isinstance(parsed, dict) else (None, None)


def _preserve_analysis_server_fields(existing: dict, candidate: dict) -> dict:
    """Keep server-owned analysis metadata authoritative during UI saves."""
    saved = dict(candidate)
    for key in _ANALYSIS_SERVER_FIELDS:
        if key in existing:
            saved[key] = existing[key]
        else:
            saved.pop(key, None)
    return saved


def _send_artifact_conflict(handler: HandlerProtocol, exc: ArtifactConflictError) -> None:
    headers = version_headers(exc.current_version) if exc.current_version else None
    handler._send_json(
        {
            "ok": False,
            "code": "artifact_conflict",
            "error": t("api.artifact_conflict"),
            "current_version": exc.current_version,
        },
        409,
        extra_headers=headers,
    )


def handle_get_texts(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/texts."""
    proj_out = handler._get_project_output(qs)
    fname = qs.get("file", [""])[0]
    p = handler._resolve_texts(fname, proj_out)
    if p is None:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    data, version = read_with_version(p)
    handler._send_bytes(
        data,
        "application/json; charset=utf-8",
        extra_headers=version_headers(version),
    )


def handle_get_voiceover(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/voiceover."""
    proj_out = handler._get_project_output(qs)
    fname = qs.get("file", [""])[0]
    p = handler._resolve_in("scripts", fname, proj_out)
    if p is None:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    data, version = read_with_version(p)
    handler._send_bytes(
        data,
        "application/json; charset=utf-8",
        extra_headers=version_headers(version),
    )


def handle_get_cover(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/cover?file=<name.jpg> — AI-picked cover frame under covers/.

    GAP-P2-11: only allow image extensions, verify magic bytes, and send nosniff /
    restrictive CSP so a planted non-image cannot become active same-origin content.
    """
    raw = qs.get("file", [""])[0]
    basename = Path(str(raw).replace("\\", "/")).name
    suffix = Path(basename).suffix.lower()
    if suffix not in _COVER_EXTS or not _SAFE_COVER_NAME.match(basename):
        return handler._send_json({"ok": False, "error": t("api.cover_extension_not_allowed")}, 403)
    proj_out = handler._get_project_output(qs)
    p = handler._resolve_in("covers", basename, proj_out)
    if p is None:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    data = p.read_bytes()
    ct = _detect_cover_content_type(data)
    if ct is None:
        return handler._send_json({"ok": False, "error": t("api.cover_not_an_image")}, 403)
    handler._send_bytes(
        data,
        ct,
        extra_headers={
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": _content_disposition(basename),
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
    error = _analysis_payload_error(obj)
    if error is not None:
        return handler._send_json({"ok": False, "code": "invalid_analysis", "error": error}, 400)
    existing, existing_version = _read_existing_object(p)
    if existing is None:
        return handler._send_json(
            {"ok": False, "code": "invalid_analysis", "error": t("api.texts_existing_invalid")},
            500,
        )
    saved = _preserve_analysis_server_fields(existing, obj)
    mark_user_edited(saved)
    data = json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        version = save_with_version(p, data, request_version(handler) or existing_version)
    except ArtifactConflictError as exc:
        return _send_artifact_conflict(handler, exc)
    _rewrite_text_file(p.with_suffix(".txt"), saved)
    handler._send_json(
        {"ok": True, "path": str(p), "version": version},
        extra_headers=version_headers(version),
    )


def handle_put_voiceover(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/voiceover."""
    proj_out = handler._get_project_output(qs)
    fname = qs.get("file", [""])[0]
    p = handler._resolve_in("scripts", fname, proj_out)
    if p is None:
        return handler._send_json({"ok": False, "error": "forbidden or not found"}, 403)
    error = _voiceover_payload_error(obj)
    if error is not None:
        return handler._send_json({"ok": False, "code": "invalid_voiceover", "error": error}, 400)
    existing, existing_version = _read_existing_object(p)
    if existing is None:
        return handler._send_json(
            {"ok": False, "code": "invalid_voiceover", "error": t("api.texts_existing_invalid")},
            500,
        )
    saved = dict(obj)
    for key in ("_lineage", "_schema_version"):
        if key in existing:
            saved[key] = existing[key]
        else:
            saved.pop(key, None)
    mark_user_edited(saved)
    data = json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        version = save_with_version(p, data, request_version(handler) or existing_version)
    except ArtifactConflictError as exc:
        return _send_artifact_conflict(handler, exc)
    _rewrite_script_md(p.with_suffix(".md"), saved)
    handler._send_json(
        {"ok": True, "path": str(p), "version": version},
        extra_headers=version_headers(version),
    )

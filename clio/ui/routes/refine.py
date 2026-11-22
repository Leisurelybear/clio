"""Route handler: POST /api/refine"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import refine_script, refine_text
from clio.i18n import t
from clio.tasks._helpers import _rewrite_script_md, _rewrite_text_file
from clio.tasks.refine import _load_analysis_for_script
from clio.ui.routes.texts import _preserve_analysis_server_fields
from clio.ui.services.file_service import (
    ArtifactConflictError,
    content_version,
    save_with_version,
    version_headers,
)

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_refining: set[str] = set()
_refining_lock = threading.Lock()


def _is_file_busy(path: str) -> bool:
    with _refining_lock:
        return path in _refining


def _try_mark_busy(path: str) -> bool:
    """Atomically claim *path* for a refine; False when one is already running."""
    with _refining_lock:
        if path in _refining:
            return False
        _refining.add(path)
        return True


def _mark_busy(path: str, busy: bool) -> None:
    with _refining_lock:
        if busy:
            _refining.add(path)
        else:
            _refining.discard(path)


def handle_post_refine(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle POST /api/refine.

    Body: {file: str, type: "texts"|"scripts", context?: str}
    Refines the given texts/scripts file via AI and saves it back.
    """
    fname = obj.get("file", "")
    ftype = obj.get("type", "")
    raw_context = obj.get("context")

    if not isinstance(fname, str) or not fname or ftype not in ("texts", "scripts"):
        return handler._send_json({"ok": False, "error": "missing or invalid file/type"}, 400)
    if raw_context is not None and not isinstance(raw_context, str):
        return handler._send_json({"ok": False, "error": "context must be a string"}, 400)
    context_override = raw_context or None

    proj_dir = handler._resolve_project_dir(qs)
    proj_out = handler._get_project_output(proj_dir)
    p = handler._resolve_texts(fname, proj_out) if ftype == "texts" else handler._resolve_in("scripts", fname, proj_out)

    if p is None:
        return handler._send_json({"ok": False, "error": "forbidden or not found"}, 404)

    abs_path = str(p.resolve())
    # Check-and-claim in one lock step so two requests cannot refine (and bill)
    # the same file concurrently.
    if not _try_mark_busy(abs_path):
        return handler._send_json(
            {"ok": False, "error": t("api.refine_busy"), "code": "refine_busy"},
            409,
        )
    try:
        _refine_and_save(handler, p, ftype, proj_dir, proj_out, context_override)
    finally:
        _mark_busy(abs_path, False)


def _refine_and_save(
    handler: HandlerProtocol,
    p: Path,
    ftype: str,
    proj_dir: Path,
    proj_out: Path,
    context_override: str | None,
) -> None:
    try:
        raw_before = p.read_bytes()
        base_version = content_version(raw_before)
        data = json.loads(raw_before.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError, OSError) as e:
        return handler._send_json({"ok": False, "error": f"failed to read file: {e}"}, 500)

    config = handler._get_config(proj_dir)

    token_store = FileTokenUsageStore(str(proj_out))

    try:
        if ftype == "texts":
            refined = refine_text(data, config, context_override=context_override, token_store=token_store)
        else:
            analysis = _load_analysis_for_script(p, config.texts_dir)
            refined = refine_script(data, analysis, config, context_override=context_override, token_store=token_store)
    except Exception as e:  # noqa: BLE001 - AI provider failures are heterogeneous
        return handler._send_json({"ok": False, "error": f"refine failed: {e}"}, 500)

    if ftype == "texts" and isinstance(data, dict) and isinstance(refined, dict):
        refined = _preserve_analysis_server_fields(data, refined)
    raw = json.dumps(refined, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        # The AI call can take tens of seconds; never overwrite an edit the user
        # saved meanwhile. Return the refined data so the client can merge.
        version = save_with_version(p, raw, base_version)
    except ArtifactConflictError as exc:
        return handler._send_json(
            {
                "ok": False,
                "code": "artifact_conflict",
                "error": t("api.artifact_conflict"),
                "current_version": exc.current_version,
                "data": refined,
            },
            409,
            extra_headers=version_headers(exc.current_version) if exc.current_version else None,
        )
    if isinstance(refined, dict):
        if ftype == "texts":
            _rewrite_text_file(p.with_suffix(".txt"), refined)
        else:
            _rewrite_script_md(p.with_suffix(".md"), refined)

    handler._send_json(
        {"ok": True, "data": refined, "version": version},
        extra_headers=version_headers(version),
    )

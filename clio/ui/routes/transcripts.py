"""API handlers for transcript GET, POST, and PUT."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from clio.ui.handler_protocol import HandlerProtocol
from clio.ui.services.file_service import _is_safe_basename, _save_atomic

_MAX_SEGMENT_TEXT_LENGTH = 5000
_MAX_TIME_SEC = 86400  # 24h

_SEG_SUFFIX_RE = re.compile(r"_seg\d+$")


def _read_transcript(path: Path) -> tuple[dict[str, Any], int]:
    """Load transcript JSON plus its optimistic-concurrency revision (_rev).

    Returns (data, rev). Missing or non-numeric _rev is treated as 0 so legacy
    Whisper files remain writable once.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    rev = data.get("_rev", 0)
    if not isinstance(rev, int) or rev < 0:
        rev = 0
    return data, rev


def _require_revision(obj: dict[str, Any], current_rev: int, handler: HandlerProtocol) -> bool:
    """Validate the client-issued revision; send 409 on mismatch.

    Returns True when the write may proceed (revision matched).
    """
    client_rev = obj.get("revision")
    if not isinstance(client_rev, int):
        handler._send_json({"ok": False, "error": "revision must be an integer"}, 400)
        return False
    if client_rev != current_rev:
        handler._send_json(
            {
                "ok": False,
                "error": f"资源已更改 (revision {current_rev} != {client_rev})，请刷新后重试",
                "revision": current_rev,
            },
            409,
        )
        return False
    return True


def _resolve_stem(file: str) -> str | None:
    safe = _is_safe_basename(file)
    if not safe:
        return None
    name = file.rsplit(".", 1)[0]
    stem = _SEG_SUFFIX_RE.sub("", name)
    return stem


def _intended_transcript_path(handler: HandlerProtocol, qs: dict[str, Any], video: str) -> Path | None:
    """Return the intended transcript file path for the given video (may not exist yet)."""
    stem = _resolve_stem(video)
    if not stem:
        return None
    proj_out = handler._get_project_output(qs)
    if not proj_out:
        return None
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    transcripts_dir = proj_out / cfg.whisper.transcripts_subdir
    return transcripts_dir / f"{stem}_transcript.json"


def _transcript_path(handler: HandlerProtocol, qs: dict[str, Any], video: str) -> Path | None:
    safe = _is_safe_basename(video)
    if not safe:
        return None
    name = video.rsplit(".", 1)[0]  # full name, preserving _segNN
    stem = _SEG_SUFFIX_RE.sub("", name)  # _segNN stripped

    proj_out = handler._get_project_output(qs)
    if not proj_out:
        return None
    proj_dir = handler._resolve_project_dir(qs)
    cfg = handler._get_config(proj_dir)
    transcripts_dir = proj_out / cfg.whisper.transcripts_subdir

    # Try 1: exact match preserving _segNN (e.g., "001_GL010695_seg01" -> "001_GL010695_seg01_transcript.json")
    full_path = transcripts_dir / f"{name}_transcript.json"
    if full_path.is_file():
        return full_path

    # Try 2: stem with _segNN stripped (e.g., "001_GX010682" -> "001_GX010682_transcript.json")
    compressed_path = transcripts_dir / f"{stem}_transcript.json"
    if compressed_path.is_file():
        return compressed_path

    # Try 3: strip index prefix (e.g., "001_GX010682" -> "GX010682_transcript.json")
    orig_stem = stem
    if "_" in stem:
        _, orig_stem = stem.split("_", 1)
        orig_stem = _SEG_SUFFIX_RE.sub("", orig_stem)
        orig_path = transcripts_dir / f"{orig_stem}_transcript.json"
        if orig_path.is_file():
            return orig_path

    # Reverse fallback: walk transcripts dir, first try exact
    # stem match (index prefix + _segNN-aware), then broad
    # orig_stem match for no-prefix videos.
    if transcripts_dir.is_dir():
        fallback = None
        for p in transcripts_dir.iterdir():
            if not (p.is_file() and p.name.endswith("_transcript.json")):
                continue
            base = p.stem[: -len("_transcript")]  # "001_GL010695_seg01"
            base_clean = _SEG_SUFFIX_RE.sub("", base)  # "001_GL010695"
            if base_clean == stem:
                return p
            if fallback is None and "_" in base_clean:
                _, base_orig = base_clean.split("_", 1)
                if base_orig == orig_stem:
                    fallback = p
        if fallback:
            return fallback
    return None


def handle_get_transcripts(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    video = qs.get("video", [None])[0]
    if not video:
        return handler._send_json({"ok": False, "error": "missing video param"}, 400)

    tp = _transcript_path(handler, qs, video)
    if not tp or not tp.is_file():
        return handler._send_json({"ok": False}, 404)

    try:
        data, rev = _read_transcript(tp)
        if data is None:
            data = {}
        data.pop("ok", None)
        handler._send_json({"ok": True, "revision": rev, **data})
    except Exception as e:
        handler._send_json({"ok": False, "error": str(e)}, 500)


def handle_put_transcripts(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    video = qs.get("video", [None])[0]
    if not video:
        return handler._send_json({"ok": False, "error": "missing video param"}, 400)

    segment_index = obj.get("segment_index")
    if segment_index is None or not isinstance(segment_index, int):
        return handler._send_json({"ok": False, "error": "missing/invalid segment_index"}, 400)

    tp = _transcript_path(handler, qs, video)
    if not tp or not tp.is_file():
        return handler._send_json({"ok": False, "error": "transcript not found"}, 404)

    try:
        data, rev = _read_transcript(tp)
        if not _require_revision(obj, rev, handler):
            return
        segments = data.get("segments", [])
        if not isinstance(segments, list):
            return handler._send_json({"ok": False, "error": "segments is not a list"}, 400)
        if segment_index < 0 or segment_index >= len(segments):
            return handler._send_json({"ok": False, "error": f"segment_index {segment_index} out of range"}, 400)

        if obj.get("delete"):
            del segments[segment_index]
        else:
            new_text = obj.get("text", "")
            if not isinstance(new_text, str):
                return handler._send_json({"ok": False, "error": "text must be a string"}, 400)
            new_text = new_text.strip()
            if not new_text:
                return handler._send_json({"ok": False, "error": "text cannot be empty"}, 400)
            if len(new_text) > _MAX_SEGMENT_TEXT_LENGTH:
                return handler._send_json(
                    {"ok": False, "error": f"text too long ({len(new_text)} > {_MAX_SEGMENT_TEXT_LENGTH} chars)"}, 400
                )
            segments[segment_index]["text"] = new_text

        data["segments"] = segments
        data["_rev"] = rev + 1
        _save_atomic(tp, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        handler._send_json({"ok": True, "revision": rev + 1})
    except Exception as e:
        handler._send_json({"ok": False, "error": str(e)}, 500)


def handle_post_transcripts(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    video = qs.get("video", [None])[0]
    if not video:
        return handler._send_json({"ok": False, "error": "missing video param"}, 400)

    if obj.get("create"):
        tp = _intended_transcript_path(handler, qs, video)
        if not tp:
            return handler._send_json({"ok": False, "error": "cannot create transcript path"}, 400)
        if tp.is_file():
            return handler._send_json({"ok": False, "error": "transcript already exists"}, 409)
        tp.parent.mkdir(parents=True, exist_ok=True)
        skeleton = {"segments": [], "_rev": 1}
        _save_atomic(tp, json.dumps(skeleton, ensure_ascii=False, indent=2).encode("utf-8"))
        handler._send_json({"ok": True, "message": "transcript file created"})
        return

    start = obj.get("start")
    end = obj.get("end")
    text = obj.get("text", "")
    if start is None or end is None:
        return handler._send_json({"ok": False, "error": "missing start/end"}, 400)
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return handler._send_json({"ok": False, "error": "start/end must be numbers"}, 400)
    if not isinstance(text, str):
        return handler._send_json({"ok": False, "error": "text must be a string"}, 400)

    if not math.isfinite(start) or not math.isfinite(end):
        return handler._send_json({"ok": False, "error": "start/end must be finite numbers"}, 400)

    if start < 0:
        return handler._send_json({"ok": False, "error": "start time cannot be negative"}, 400)
    if end <= start:
        return handler._send_json({"ok": False, "error": "end time must be greater than start time"}, 400)
    if end > _MAX_TIME_SEC:
        return handler._send_json({"ok": False, "error": f"end time exceeds maximum ({_MAX_TIME_SEC}s)"}, 400)

    text = text.strip()
    if not text:
        return handler._send_json({"ok": False, "error": "text cannot be empty"}, 400)
    if len(text) > _MAX_SEGMENT_TEXT_LENGTH:
        return handler._send_json(
            {"ok": False, "error": f"text too long ({len(text)} > {_MAX_SEGMENT_TEXT_LENGTH} chars)"}, 400
        )

    tp = _transcript_path(handler, qs, video)
    if not tp or not tp.is_file():
        return handler._send_json({"ok": False, "error": "transcript not found"}, 404)

    try:
        data, rev = _read_transcript(tp)
        if not _require_revision(obj, rev, handler):
            return
        segments = data.get("segments", [])
        if not isinstance(segments, list):
            return handler._send_json({"ok": False, "error": "segments is not a list"}, 400)

        new_seg = {
            "start": round(start, 2),
            "end": round(end, 2),
            "text": text,
            "avg_logprob": 0.0,
            "low_confidence": False,
        }

        insert_idx = len(segments)
        for i, seg in enumerate(segments):
            if seg.get("start", 0) > start:
                insert_idx = i
                break
        segments.insert(insert_idx, new_seg)
        data["segments"] = segments
        data["_rev"] = rev + 1

        _save_atomic(tp, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        handler._send_json({"ok": True, "segment_index": insert_idx, "revision": rev + 1, "segment": new_seg})
    except Exception as e:
        handler._send_json({"ok": False, "error": str(e)}, 500)

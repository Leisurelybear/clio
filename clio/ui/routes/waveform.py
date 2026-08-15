"""Route handlers: /api/waveform — lazy audio peaks for the player."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio._constants import VIDEO_EXTENSIONS, VIDEO_EXTS
from clio.task_center.manager import TaskManager
from clio.tasks._video_loader import load_selected_videos
from clio.tasks.waveform import ensure_waveform
from clio.ui.services.file_service import _is_safe_basename

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in {"1", "true", "yes", "on"}


def _original_allowed(proj_dir: Path, vp: Path) -> bool:
    """True if abspath may be used — exact path must be in videos.json selection."""
    selected = load_selected_videos(proj_dir)
    if not selected:
        return False
    allowed: set[Path] = set()
    for p in selected:
        try:
            allowed.add(p.resolve())
        except OSError:
            allowed.add(p)
    return vp in allowed


def _resolve_waveform_media(
    qs: dict[str, Any],
    proj_dir: Path,
    proj_out: Path,
) -> tuple[Path, str] | None:
    """Return (peaks_source_path, audio_source) or None if no readable media."""
    fname = qs.get("file", [""])[0]
    source = qs.get("source", ["compressed"])[0]
    if source not in ("compressed", "original"):
        source = "compressed"
    is_segment = _truthy(qs.get("is_segment", [""])[0])
    abspath_raw = qs.get("abspath", [None])[0]

    compressed_path: Path | None = None
    if fname and _is_safe_basename(fname):
        cand = proj_out / "compressed" / fname
        if cand.is_file() and cand.suffix.lower() in VIDEO_EXTS:
            compressed_path = cand

    original_path: Path | None = None
    if abspath_raw:
        try:
            vp = Path(str(abspath_raw)).resolve()
        except OSError:
            vp = None
        if (
            vp is not None
            and vp.is_file()
            and vp.suffix.lower() in VIDEO_EXTENSIONS
            and _original_allowed(proj_dir, vp)
        ):
            original_path = vp

    if is_segment:
        if source == "compressed" and compressed_path is not None:
            return compressed_path, "compressed"
        if original_path is not None:
            return original_path, "original"
        if compressed_path is not None:
            return compressed_path, "compressed"
        return None

    if original_path is not None:
        return original_path, "original"
    if compressed_path is not None:
        return compressed_path, "compressed"
    return None


def handle_get_waveform(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/waveform — ready peaks (200) or pending (202)."""
    proj_dir = handler._resolve_project_dir(qs)
    proj_out = handler._get_project_output(proj_dir)
    resolved = _resolve_waveform_media(qs, proj_dir, proj_out)
    if resolved is None:
        return handler._send_json({"ok": False, "error": "no media"}, 404)

    peaks_path, audio_source = resolved
    cfg = handler._get_config(proj_dir)
    paths = getattr(cfg, "paths", None)
    # Keep empty string empty — resolve_binary("") discovers PATH.
    # Bare "ffmpeg" is treated as a configured path and raises FileNotFoundError.
    ffmpeg = getattr(paths, "ffmpeg", "") or ""
    ffprobe = getattr(paths, "ffprobe", "") or ""
    try:
        task_manager = handler._get_task_manager()
    except (AttributeError, TypeError):
        task_manager = None
    if not isinstance(task_manager, TaskManager):
        task_manager = None

    result = ensure_waveform(
        proj_out,
        peaks_path,
        ffmpeg,
        audio_source=audio_source,
        ffprobe=ffprobe,
        task_manager=task_manager,
        project_id=str(proj_dir.resolve()),
        project_path=str(proj_dir.resolve()),
    )
    status = result.get("status")
    if status == "pending":
        return handler._send_json(result, 202)
    if status == "error":
        return handler._send_json(result, 503)
    return handler._send_json(result)

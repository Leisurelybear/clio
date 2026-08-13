"""File-system utilities for the UI server.

Module-level helpers extracted from server.py:
- basename safety checks
- directory scanning (texts dirs, drives)
- atomic file writes
- config type coercion
- video matching (compressed <-> original)
- range-based video streaming
- text/script file resolution
"""

from __future__ import annotations

import re
import shutil
import string
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Any

import yaml

from clio._constants import VIDEO_EXTS
from clio.config.loader import _filter_project_only
from clio.vmeta import VideoIndex, VideoMeta


def _is_safe_basename(name: str) -> bool:
    if not name or len(name) > 200:
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return False
    return True


def _find_texts_dirs(output_dir: Path) -> list[Path]:
    """Return all texts* subdirectories (texts, texts - Paris, ...)."""
    if not output_dir or not output_dir.is_dir():
        return []
    return [
        d for d in sorted(output_dir.iterdir()) if d.is_dir() and (d.name == "texts" or d.name.startswith("texts - "))
    ]


def _save_atomic(path: Path, data: bytes) -> None:
    """Atomically write *data* while preserving prior backups (GAP-P2-02).

    - New content is written to an exclusive temp and renamed into place.
    - If *path* exists and ``.bak`` already exists, the previous ``.bak`` is
      rotated aside (timestamped) before the current file becomes the new bak —
      so a corrupt primary cannot clobber the last known-good backup.
    - If only ``.bak`` exists (no primary), it is left untouched.
    """
    import os
    import time

    from clio.utils import _exclusive_random_tmp

    path.parent.mkdir(parents=True, exist_ok=True)
    bak = path.with_suffix(path.suffix + ".bak")
    tmp, fd = _exclusive_random_tmp(path)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if path.exists():
            if bak.exists():
                rotated = path.with_suffix(path.suffix + f".bak.{time.time_ns()}")
                try:
                    bak.replace(rotated)
                except OSError:
                    shutil.copy2(bak, rotated)
            try:
                path.replace(bak)
            except OSError:
                shutil.copy2(path, bak)
                path.unlink(missing_ok=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        tmp.unlink(missing_ok=True)
        raise
    _prune_rotated_backups(path)


def _prune_rotated_backups(path: Path, keep: int = 3) -> None:
    """Keep at most *keep* timestamped ``.bak.*`` sidecars besides ``.bak``."""
    bak = path.with_suffix(path.suffix + ".bak")
    rotated = sorted(
        (p for p in path.parent.glob(path.name + ".bak.*") if p.is_file() and p != bak),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in rotated[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def _create_project_yaml(proj_dir: Path, config_path: Path | None, proj_out: Path) -> Path | None:
    """Create project.yaml from global config template, with paths adjusted to the project.

    Returns the project.yaml path, or None if no global config is available.
    """
    if not config_path or not config_path.is_file():
        return None
    target = proj_dir / "project.yaml"
    if target.is_file():
        return target  # already exists
    try:
        with open(config_path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        paths = raw.get("paths", {})
        paths.pop("input_dir", None)
        paths.pop("recursive", None)
        paths["output_dir"] = str(proj_out.resolve())
        raw["paths"] = paths
        raw.setdefault("ai", {})
        raw["ai"].setdefault("context", "")
        _inject_provider_defaults(raw)
        _inject_whisper_defaults(raw)
        # Strip global-only fields (providers, ffmpeg paths, codec params, etc.)
        # to prevent API key metadata from leaking into project.yaml
        raw = _filter_project_only(raw)
        yml = yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False, indent=2)
        _save_atomic(target, yml.encode("utf-8"))
        return target
    except Exception:
        return None


def _inject_provider_defaults(raw: dict) -> None:
    """Inject default provider fields into
    the ai.providers section of a config dict. Only touches provider blocks."""
    providers = raw.get("ai", {}).get("providers", {})
    for pname, pcfg in providers.items():
        if isinstance(pcfg, dict):
            pcfg.setdefault("requests_per_minute", 0)
            pcfg.setdefault("retry_attempts", 2)
            pcfg.setdefault("timeout_sec", 120.0)
            pcfg.setdefault("max_tokens", 0)
            pcfg.setdefault("poll_interval_sec", 5)


def _inject_whisper_defaults(raw: dict) -> None:
    """Inject default whisper fields into a config dict."""
    whisper = raw.setdefault("whisper", {})
    if isinstance(whisper, dict):
        whisper.setdefault("enabled", True)
        whisper.setdefault("model_size", "medium")
        whisper.setdefault("language", "zh")
        whisper.setdefault("device", "auto")
        whisper.setdefault("max_segments_per_clip", 5)
        whisper.setdefault("transcripts_subdir", "transcripts")
        whisper.setdefault("hf_endpoint", "")  # 空=官方地址；国内可设 https://hf-mirror.com


def _migrate_project_configs(projects_root: Path) -> tuple[int, list[str]]:
    """Scan for project.yaml files and inject missing provider defaults.
    Returns (count_updated, list of errors)."""
    updated = 0
    errors: list[str] = []
    if not projects_root.is_dir():
        return updated, errors
    for proj_dir in sorted(projects_root.iterdir()):
        if not proj_dir.is_dir():
            continue
        proj_yaml = proj_dir / "project.yaml"
        if not proj_yaml.is_file():
            continue
        try:
            with open(proj_yaml, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            before = yaml.dump(raw, allow_unicode=True, sort_keys=False)
            _inject_provider_defaults(raw)
            _inject_whisper_defaults(raw)
            after = yaml.dump(raw, allow_unicode=True, sort_keys=False)
            if before != after:
                yml = yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False, indent=2)
                _save_atomic(proj_yaml, yml.encode("utf-8"))
                updated += 1
        except Exception as e:
            errors.append(f"{proj_dir.name}: {e}")
    return updated, errors


def _list_drives() -> list[str]:
    """Quickly list available Windows drive letters (avoid Path.is_dir() timeout on network drives)."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{d}:\\" for d in string.ascii_uppercase if bitmask & (1 << (ord(d) - ord("A")))]
    except Exception:
        # fallback: traditional approach
        return [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").is_dir()]


def _find_original_for_compressed(
    stem: str, fallback_dir: Path, comp_dir: Path | None = None, project_dir: Path | None = None
) -> str | None:
    """For a compressed stem like '001_GL010695', find the matching original path.

    Returns an absolute path string when possible (external originals via .vmeta /
    videos.json), otherwise a basename under fallback_dir/project_dir for legacy.
    """
    # Try .vmeta first (O(1), supports any directory layout)
    if comp_dir is not None:
        for p in comp_dir.glob(f"{stem}.*"):
            if p.suffix.lower() not in VIDEO_EXTS:
                continue
            meta = VideoMeta.read(p)
            if meta is not None:
                sp = Path(meta.source_path)
                return str(sp.resolve()) if sp.is_file() else str(sp)

    if "_" not in stem:
        return None
    suffix = stem.split("_", 1)[1].lower()

    # Project dir: use load_selected_videos when videos.json is present
    lookup_dir = project_dir or fallback_dir
    if lookup_dir:
        from clio.tasks._video_loader import load_selected_videos

        selected = load_selected_videos(lookup_dir)
        if selected:
            for p in selected:
                if p.stem.lower() == suffix:
                    try:
                        return str(p.resolve()) if p.is_file() else str(p)
                    except OSError:
                        return str(p)
            m = re.match(r"^(.+)_seg\d+$", suffix)
            if m:
                base = m.group(1)
                for p in selected:
                    if p.stem.lower() == base:
                        try:
                            return str(p.resolve()) if p.is_file() else str(p)
                        except OSError:
                            return str(p)
            # videos.json present but no match — do not fall through to dir scan
            # of project_dir (would miss external-only selections intentionally)
            if project_dir is not None:
                return None

    # Legacy fallback: stem matching under fallback_dir / project_dir
    scan_dir = (
        fallback_dir if fallback_dir.is_dir() else (project_dir if project_dir and project_dir.is_dir() else None)
    )
    if scan_dir is None:
        return None
    for p in sorted(scan_dir.iterdir()):
        if p.is_file() and p.stem.lower() == suffix:
            return str(p.resolve())
    m = re.match(r"^(.+)_seg\d+$", suffix)
    if m:
        base = m.group(1)
        for p in sorted(scan_dir.iterdir()):
            if p.is_file() and p.stem.lower() == base:
                return str(p.resolve())
    return None


def _find_compressed_for_original(stem: str, comp_dir: Path) -> list[tuple[str, str]] | None:
    """For an original stem like 'GL010695', find matching compressed file(s) and
    their indices. Prefers .vindex for O(1) lookup; falls back to directory scan
    for legacy projects.
    Returns a sorted list of (compressed_basename, index) tuples,
    or None if not found. For split videos, returns all segments sorted by index.
    """
    if not comp_dir.is_dir():
        return None

    # Try .vindex first (O(1))
    vindex = VideoIndex.read(stem, comp_dir)
    if vindex is not None:
        paths = vindex.compressed_paths(comp_dir)
        if paths:
            matches = [(p.name, s.index) for p, s in zip(paths, vindex.segments)]
            matches.sort(key=lambda m: m[1])
            return matches

    # Legacy fallback: directory scan
    needle = stem.lower()
    fallback_matches: list[tuple[str, str]] = []
    for p in sorted(comp_dir.iterdir()):
        if p.suffix.lower() not in VIDEO_EXTS or "_" not in p.stem:
            continue
        idx, rest = p.stem.split("_", 1)
        if rest.lower() == needle:
            return [(p.name, idx)]
        seg_prefix = needle + "_seg"
        if rest.lower().startswith(seg_prefix) and rest.lower()[len(seg_prefix) :].isdigit():
            fallback_matches.append((p.name, idx))
    if not fallback_matches:
        return None
    fallback_matches.sort(key=lambda m: m[1])
    return fallback_matches


def _coerce_config_types(new_val: Any, ref_val: Any) -> Any:
    if ref_val is None:
        return new_val
    if isinstance(ref_val, bool):
        if isinstance(new_val, str):
            return new_val.lower() in ("true", "1", "yes")
        return bool(new_val)
    if isinstance(ref_val, int):
        if new_val is None:
            return None
        try:
            return int(new_val)
        except (ValueError, TypeError):
            return new_val
    if isinstance(ref_val, float):
        if new_val is None:
            return None
        try:
            return float(new_val)
        except (ValueError, TypeError):
            return new_val
    if isinstance(ref_val, str):
        return str(new_val) if not isinstance(new_val, str) else new_val
    if isinstance(ref_val, list) and isinstance(new_val, list):
        if ref_val and new_val:
            return [_coerce_config_types(n, ref_val[0]) for n in new_val]
        return new_val
    if isinstance(ref_val, dict) and isinstance(new_val, dict):
        result = {}
        for k in ref_val:
            if k in new_val:
                result[k] = _coerce_config_types(new_val[k], ref_val[k])
        for k in new_val:
            if k not in result:
                result[k] = new_val[k]
        return result
    return new_val


_VIDEO_MIME = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".lrv": "video/mp4",
}


def send_video_range(handler, path: Path) -> None:
    """Respond to a Range-based video request.

    Supports full and partial (bytes=start-end, bytes=-N) range requests.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        handler.send_error(HTTPStatus.NOT_FOUND)
        return
    rng = handler.headers.get("Range")
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if not m:
            handler.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        start_s, end_s = m.group(1), m.group(2)
        if start_s == "" and end_s != "":
            suffix_len = int(end_s)
            start = max(0, size - suffix_len)
            end = size - 1
        else:
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else size - 1
        if start >= size or end >= size or start > end:
            handler.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        length = end - start + 1
        if length <= 0:
            handler.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return
        handler.send_response(206)
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.send_header("Content-Length", str(length))
    else:
        start = 0
        length = size
        handler.send_response(200)
        handler.send_header("Content-Length", str(size))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Type", _VIDEO_MIME.get(path.suffix.lower(), "video/mp4"))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    with path.open("rb") as f:
        f.seek(start)
        remaining = length
        chunk = 64 * 1024
        try:
            while remaining > 0:
                buf = f.read(min(chunk, remaining))
                if not buf:
                    break
                handler.wfile.write(buf)
                remaining -= len(buf)
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            pass


def resolve_texts(basename: str, proj_out: Path | None, output_dir: Path) -> Path | None:
    if not _is_safe_basename(basename):
        return None
    base = proj_out or output_dir
    for d in _find_texts_dirs(base):
        p = d / basename
        if p.is_file():
            return p
    return None


def resolve_in(subdir: str, basename: str, proj_out: Path | None, output_dir: Path) -> Path | None:
    if not _is_safe_basename(basename):
        return None
    if subdir == "texts":
        return resolve_texts(basename, proj_out, output_dir)
    base = proj_out or output_dir
    d = base / subdir
    if not d.is_dir():
        return None
    p = d / basename
    return p if p.is_file() else None

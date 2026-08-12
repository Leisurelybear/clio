"""Route handlers: /api/videos, /api/video, /api/videos/selected"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any

from clio._constants import VIDEO_EXTENSIONS, VIDEO_EXTS
from clio.identity import prefer_canonical_compressed
from clio.index import ArtifactIndex
from clio.tasks._video_loader import load_selected_videos, save_selected_videos
from clio.ui.services.file_service import (
    _find_compressed_for_original,
    _find_original_for_compressed,
    _find_texts_dirs,
    _is_safe_basename,
)
from clio.utils import get_duration_sec, resolve_binary
from clio.vmeta import VideoMeta

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_SEG_RE = re.compile(r"^(.+)_(?:seg|part|pt|chunk)(\d+)$", re.IGNORECASE)
_VIDEOS_CACHE_LOCK = threading.Lock()
_VIDEOS_CACHE_MAX = 20
_VIDEOS_CACHE: dict[tuple[str, str, str], tuple[tuple[Any, ...], dict[str, Any]]] = {}


def _cache_dir(proj_out: Path) -> Path:
    return proj_out / ".cache"


def _disk_cache_path(proj_out: Path, source: str) -> Path:
    return _cache_dir(proj_out) / f"videos_{source}.json"


def _signature_hash(signature: tuple[Any, ...]) -> str:
    return hashlib.sha256(json.dumps(signature, default=str).encode("utf-8")).hexdigest()


def _load_disk_cache(proj_out: Path, source: str, signature: tuple[Any, ...]) -> dict[str, Any] | None:
    """Load payload from persisted cache if the stored signature matches."""
    path = _disk_cache_path(proj_out, source)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != 1 or data.get("signature") != _signature_hash(signature):
        return None
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return None
    return payload


def _save_disk_cache(proj_out: Path, source: str, signature: tuple[Any, ...], payload: dict[str, Any]) -> None:
    from clio.utils import write_json_atomic

    try:
        cache_dir = _cache_dir(proj_out)
        cache_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "signature": _signature_hash(signature), "payload": payload}
        write_json_atomic(_disk_cache_path(proj_out, source), data)
    except OSError:
        pass


def _parse_segment_info(stem: str) -> tuple[str | None, int | None]:
    """Extract group info from a compressed stem like '001_GL010683_seg01'.
    Also supports _partNN, _ptNN, _chunkNN suffixes (case-insensitive).
    Returns (group_key, segment_number) or (None, None).
    """
    if "_" not in stem:
        return None, None
    m = _SEG_RE.match(stem.split("_", 1)[1])
    if not m:
        return None, None
    return m.group(1), int(m.group(2))


def _original_rel_name(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _resolve_original_video_path(root: Path, requested: str) -> Path | None:
    parts = requested.split("_", 1)
    actual = parts[1] if len(parts) == 2 and parts[0].isdigit() else requested
    rel = Path(actual)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    try:
        candidate = (root / rel).resolve()
        root_resolved = root.resolve()
        candidate.relative_to(root_resolved)
    except (OSError, ValueError):
        return None
    return candidate


def _find_selected_original_for_request(proj_dir: Path, requested: str) -> Path | None:
    """Resolve an original video request against the selected video allowlist."""
    candidate = _resolve_original_video_path(proj_dir, requested)
    if candidate is not None and candidate.is_file() and candidate.suffix.lower() in VIDEO_EXTENSIONS:
        return candidate

    requested_path = Path(requested)
    if requested_path.is_absolute() or ".." in requested_path.parts:
        return None
    requested_name = requested_path.name.lower()
    requested_stem = requested_path.stem
    if "_" in requested_stem:
        prefix, suffix = requested_stem.split("_", 1)
        if prefix.isdigit():
            requested_stem = suffix
    requested_stem = re.sub(r"_(?:seg|part|pt|chunk)\d+$", "", requested_stem, flags=re.IGNORECASE).lower()

    selected = load_selected_videos(proj_dir)
    exact_name = [
        path
        for path in selected
        if path.name.lower() == requested_name and path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if len(exact_name) == 1:
        return exact_name[0].resolve()

    stem_matches = [
        path
        for path in selected
        if path.stem.lower() == requested_stem and path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if len(stem_matches) == 1:
        return stem_matches[0].resolve()
    return None


def _selection_stems(selected_set: set[Path]) -> set[str]:
    return {p.stem.lower() for p in selected_set}


def _compressed_matches_selection(
    compressed: Path,
    stem: str,
    selected_set: set[Path],
    orig: str | None,
) -> bool:
    """True if this compressed artifact belongs to the curated selection.

    Matches absolute source path first, then basename / original stem. After
    relink, .vmeta may still hold the old path while videos.json has the new
    one — stem/basename matching keeps the compressed file visible.
    """
    meta = VideoMeta.read(compressed)
    candidates: list[Path] = []
    if meta is not None and meta.source_path:
        candidates.append(Path(meta.source_path))
    if orig:
        candidates.append(Path(orig))

    for cand in candidates:
        try:
            resolved = cand.resolve() if cand.is_absolute() or cand.exists() else cand
        except OSError:
            resolved = cand
        if resolved in selected_set:
            return True
        if any(s.name.lower() == cand.name.lower() for s in selected_set):
            return True
        if cand.stem.lower() in _selection_stems(selected_set):
            return True

    # compressed stem like 001_GL010695 or 001_GL010695_seg01
    suffix = stem.split("_", 1)[1] if "_" in stem else stem
    suffix_l = suffix.lower()
    stems = _selection_stems(selected_set)
    if suffix_l in stems:
        return True
    m = _SEG_RE.match(suffix)
    if m and m.group(1).lower() in stems:
        return True
    # strip _segNN manually if regex only matched full stem form
    base = re.sub(r"_(?:seg|part|pt|chunk)\d+$", "", suffix_l, flags=re.IGNORECASE)
    if base in stems:
        return True
    return False


def _load_videos_json_selection(proj_dir: Path) -> set[Path] | None:
    """Load videos.json selection.

    Returns None only when videos.json is absent (legacy project).
    Empty list / offline paths still yield a set so selection mode stays active.
    """
    if not (proj_dir / "videos.json").is_file():
        return None
    selected = load_selected_videos(proj_dir)
    out: set[Path] = set()
    for p in selected:
        try:
            out.add(p.resolve())
        except OSError:
            out.add(p)
    return out


def handle_get_videos(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/videos. Sends JSON response directly."""

    proj_dir = handler._resolve_project_dir(qs)
    proj_out = handler._get_project_output(proj_dir)
    source = qs.get("source", ["compressed"])[0]
    if source not in ("compressed", "original"):
        return handler._send_json({"ok": False, "error": "source must be compressed|original"}, 400)
    comp_dir = proj_out / "compressed"
    cfg = handler._get_config(proj_dir)
    cache_key = (str(proj_dir.resolve()), str(proj_out.resolve()), source)
    selected_set = _load_videos_json_selection(proj_dir)
    signature = _videos_cache_signature(proj_dir, proj_out, comp_dir, cfg)
    with _VIDEOS_CACHE_LOCK:
        cached = _VIDEOS_CACHE.get(cache_key)
        if cached is not None and cached[0] == signature:
            return handler._send_json(deepcopy(cached[1]))

    # Persisted cache survives process restart (cold start); only a signature
    # change forces a rebuild.
    disk_payload = _load_disk_cache(proj_out, source, signature)
    if disk_payload is not None:
        with _VIDEOS_CACHE_LOCK:
            if len(_VIDEOS_CACHE) >= _VIDEOS_CACHE_MAX and cache_key not in _VIDEOS_CACHE:
                _VIDEOS_CACHE.pop(next(iter(_VIDEOS_CACHE)))
            _VIDEOS_CACHE[cache_key] = (signature, deepcopy(disk_payload))
        return handler._send_json(disk_payload)

    payload = _build_videos_payload(handler, proj_dir, proj_out, comp_dir, source, cfg, selected_set=selected_set)
    _save_disk_cache(proj_out, source, signature, payload)
    with _VIDEOS_CACHE_LOCK:
        if len(_VIDEOS_CACHE) >= _VIDEOS_CACHE_MAX and cache_key not in _VIDEOS_CACHE:
            _VIDEOS_CACHE.pop(next(iter(_VIDEOS_CACHE)))
        _VIDEOS_CACHE[cache_key] = (signature, deepcopy(payload))
    handler._send_json(payload)


def _file_fingerprint(path: Path) -> tuple[Any, ...]:
    """Fingerprint a single file (size, mtime) for cache invalidation."""
    try:
        st = path.stat()
        return (st.st_size, st.st_mtime_ns)
    except OSError:
        return ()


def _videos_cache_signature(proj_dir: Path, proj_out: Path, comp_dir: Path, cfg: Any) -> tuple[Any, ...]:
    text_dirs = tuple(_find_texts_dirs(proj_out))
    videos_json = proj_dir / "videos.json"
    selected_videos = load_selected_videos(proj_dir)
    selected_fingerprints: list[tuple[str, tuple[Any, ...]]] = []
    for video in selected_videos:
        # Source files may live outside the project (including on a NAS), so
        # project-dir fingerprints alone cannot invalidate an offline payload.
        selected_fingerprints.append((str(video), _file_fingerprint(video)))
    return (
        _file_fingerprint(videos_json),  # must invalidate when selection changes
        tuple(selected_fingerprints),  # must invalidate when external media returns
        _dir_fingerprint(proj_dir, video_only=True),
        _dir_fingerprint(comp_dir),
        tuple((str(td), _dir_fingerprint(td, json_only=True)) for td in text_dirs),
        _dir_fingerprint(proj_out / "scripts", json_only=True),
        _dir_fingerprint(proj_out / cfg.whisper.transcripts_subdir, json_only=True),
        _dir_fingerprint(proj_out / "covers"),  # AI cover thumbs
        cfg.whisper.transcripts_subdir,
        cfg.paths.ffprobe,
    )


def _invalidate_videos_cache(proj_dir: Path | None = None) -> None:
    """Drop cached /api/videos payloads (all sources for a project, or entire cache)."""
    with _VIDEOS_CACHE_LOCK:
        if proj_dir is None:
            _VIDEOS_CACHE.clear()
            return
        key_prefix = str(proj_dir.resolve())
        for key in list(_VIDEOS_CACHE):
            if key[0] == key_prefix:
                _VIDEOS_CACHE.pop(key, None)


def _dir_fingerprint(path: Path, *, video_only: bool = False, json_only: bool = False) -> tuple[Any, ...]:
    if not path.is_dir():
        return ()
    entries = []
    for p in sorted(path.iterdir()):
        if video_only and p.suffix.lower() not in VIDEO_EXTS:
            continue
        if json_only and p.suffix.lower() != ".json":
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        kind = "dir" if p.is_dir() else "file"
        entries.append((p.name, kind, st.st_size, st.st_mtime_ns))
    return tuple(entries)


def _cover_map(proj_out: Path) -> dict[str, str]:
    """Map analysis stem → cover basename under output/covers/."""
    covers_dir = proj_out / "covers"
    mapping: dict[str, str] = {}
    if not covers_dir.is_dir():
        return mapping
    try:
        for p in covers_dir.iterdir():
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                mapping[p.stem] = p.name
    except OSError:
        return mapping
    return mapping


def _cover_for_text_json(
    text_json: str | None,
    cover_map: dict[str, str],
    index: str | None = None,
) -> str | None:
    if text_json:
        stem = Path(text_json).stem
        if stem in cover_map:
            return cover_map[stem]
    if index:
        # Prefer exact / unique prefix match for "001_title.jpg"
        for stem, name in cover_map.items():
            if stem == index or stem.startswith(f"{index}_"):
                return name
    return None


def _build_videos_payload(
    handler: HandlerProtocol,
    proj_dir: Path,
    proj_out: Path,
    comp_dir: Path,
    source: str,
    cfg: Any,
    *,
    selected_set: set[Path] | None = None,
) -> dict[str, Any]:
    # -- Build artifact index ------------------------------------------------
    index = ArtifactIndex(
        output_dir=proj_out,
        project_dir=proj_dir,
        compressed_dir=comp_dir,
        texts_dir=proj_out / "texts",
        scripts_dir=proj_out / "scripts",
        transcripts_dir=proj_out / cfg.whisper.transcripts_subdir,
    )
    index.build()

    # Keep text_titles by index for original source view
    text_titles: dict[str, str] = {}
    for g in index.all_groups():
        if g.texts:
            text_titles[g.compressed.index] = g.texts[0].title

    cover_map = _cover_map(proj_out)

    videos: list[dict] = []
    groups: dict[str, dict] = {}

    if source == "compressed":
        if comp_dir.is_dir():
            # Pass 1: build flat video list + collect group members
            group_members: dict[str, list[tuple[str, int]]] = {}
            compressed_paths = prefer_canonical_compressed(
                sorted(p for p in comp_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
            )
            for p in compressed_paths:
                stem = p.stem
                idx = stem.split("_", 1)[0] if "_" in stem else ""
                meta = VideoMeta.read(p)
                group_key, seg_num = (
                    (None, None)
                    if meta is not None and meta.split_info is None and not meta.is_split_segment
                    else _parse_segment_info(stem)
                )
                group = index.lookup(compressed_stem=stem)
                orig = _find_original_for_compressed(stem, proj_dir, comp_dir, project_dir=proj_dir)
                # Only filter when selection is non-empty. Empty videos.json still
                # allows browsing existing compressed artifacts.
                if selected_set:
                    if not _compressed_matches_selection(p, stem, selected_set, orig):
                        continue
                match_file = None
                abs_match = None
                match_missing = False
                if orig:
                    op = Path(orig)
                    match_file = op.name
                    try:
                        if op.is_absolute() or op.is_file():
                            resolved = op.resolve() if op.exists() else op
                            abs_match = str(resolved)
                            match_missing = not op.exists() and not Path(abs_match).exists()
                        else:
                            cand = (proj_dir / op).resolve()
                            abs_match = str(cand)
                            match_missing = not cand.exists()
                    except OSError:
                        abs_match = str(op)
                        match_missing = True
                text_json = group.texts[0].path.name if group and group.texts else None
                cover_file = (
                    group.cover.path.name if group and group.cover else _cover_for_text_json(text_json, cover_map, idx)
                )
                v: dict[str, Any] = {
                    "file": p.name,
                    "source": "compressed",
                    "index": idx,
                    "title": group.texts[0].title if group and group.texts else text_titles.get(idx, ""),
                    "text_json": text_json,
                    "cover_file": cover_file,
                    "script_json": group.script.path.name if group and group.script else None,
                    "transcript_file": group.transcript.stem if group and group.transcript else None,
                    "match": (
                        {
                            "source": "original",
                            "file": match_file,
                            "abs_path": abs_match,
                            "missing": match_missing,
                        }
                        if orig
                        else None
                    ),
                    "group_key": group_key,
                    "segment_label": None,
                }
                if group_key is not None and seg_num is not None:
                    group_members.setdefault(group_key, []).append((idx, seg_num))
                videos.append(v)

            # Pass 2: compute totals, fill segment labels and offsets
            # resolve ffprobe once for segment offset computation
            try:
                _ffprobe = resolve_binary(cfg.paths.ffprobe, "ffprobe")
            except Exception:
                _ffprobe = None
            for gk, members in group_members.items():
                members.sort(key=lambda x: x[1])
                total = len(members)
                groups[gk] = {
                    "original_stem": gk,
                    "indices": [m[0] for m in members],
                    "total": total,
                }
                offsets: dict[str, float] = {}
                durations: dict[str, float] = {}
                for member_idx, seg_num in members:
                    for v in videos:
                        if v["index"] == member_idx:
                            comp_file = comp_dir / v["file"]
                            meta = VideoMeta.read(comp_file)
                            if meta and meta.split_info:
                                offsets[member_idx] = meta.split_info.offset_sec
                                durations[member_idx] = meta.split_info.segment_duration_sec
                            break
                missing = [m for m in members if m[0] not in offsets]
                if missing and total > 1 and _ffprobe:
                    orig_video: Path | None = None
                    for ext in VIDEO_EXTS:
                        candidate = proj_dir / f"{gk}{ext}"
                        if candidate.is_file():
                            orig_video = candidate
                            break
                    if orig_video is not None:
                        try:
                            dur = get_duration_sec(orig_video, _ffprobe)
                            seg_dur = dur / total
                            for i, (member_idx, _) in enumerate(missing):
                                offsets[member_idx] = round(i * seg_dur, 1)
                                durations[member_idx] = round(seg_dur, 1)
                        except Exception:
                            pass
                for member_idx, seg_num in members:
                    for v in videos:
                        if v["index"] == member_idx:
                            v["segment_label"] = f"{seg_num}/{total}"
                            v["offset_sec"] = offsets.get(member_idx, 0.0)
                            v["duration_sec"] = durations.get(member_idx, 0.0)
                            break
    else:  # original
        # resolve ffprobe once for segment offset computation
        try:
            cfg = handler._get_config(proj_dir)
            _ffprobe = resolve_binary(cfg.paths.ffprobe, "ffprobe")
        except Exception:
            _ffprobe = None

        if selected_set is not None:
            # May be empty (new project). Offline/missing paths still listed so UI can show them.
            video_paths = sorted(selected_set, key=lambda p: p.name.lower())
        else:
            # No videos.json → empty list (do not scan project_dir)
            video_paths = []
        for p in video_paths:
            try:
                online = p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
            except OSError:
                online = False
            if not online:
                # Offline or non-video entry: still surface so UI can relink/remove.
                try:
                    abs_shown = str(p.resolve())
                except OSError:
                    abs_shown = str(p)
                videos.append(
                    {
                        "file": p.name,
                        "source": "original",
                        "index": None,
                        "match": None,
                        "abs_path": abs_shown,
                        "missing": True,
                    }
                )
                continue
            abs_path = str(p.resolve())
            rel_name = p.name
            proj_abs = proj_dir.resolve()
            try:
                rel_name = p.relative_to(proj_abs).as_posix()
            except ValueError:
                rel_name = p.name
            comp = _find_compressed_for_original(p.stem, comp_dir)
            if not comp:
                orig_groups = index.lookup(original_stem=p.stem)
                has_transcript = any(g.transcript is not None for g in (orig_groups or []))
                v = {
                    "file": rel_name,
                    "source": "original",
                    "index": None,
                    "match": None,
                    "transcript_file": p.stem if has_transcript else None,
                }
                if abs_path:
                    v["abs_path"] = abs_path
                videos.append(v)
                continue
            # Compute per-segment offsets
            seg_offsets: dict[str, float] = {}
            if len(comp) > 1 and _ffprobe:
                try:
                    dur = get_duration_sec(p, _ffprobe)
                    seg_dur = dur / len(comp)
                    for i, (_, idx) in enumerate(comp):
                        seg_offsets[idx] = round(i * seg_dur, 1)
                except Exception:
                    pass
            segment_matches = [{"source": "compressed", "file": cf, "index": ci} for cf, ci in comp]
            for c_file, c_idx in comp:
                group = index.lookup(compressed_stem=Path(c_file).stem)
                text_json = group.texts[0].path.name if group and group.texts else None
                cover_file = (
                    group.cover.path.name
                    if group and group.cover
                    else _cover_for_text_json(text_json, cover_map, c_idx)
                )
                seg_v: dict[str, Any] = {
                    "file": f"{c_idx}_{rel_name}",
                    "source": "original",
                    "index": c_idx,
                    "title": text_titles.get(c_idx, ""),
                    "offset_sec": seg_offsets.get(c_idx, 0.0),
                    "text_json": text_json,
                    "cover_file": cover_file,
                    "script_json": group.script.path.name if group and group.script else None,
                    "transcript_file": group.transcript.stem if group and group.transcript else None,
                    "match": {"source": "compressed", "file": c_file, "index": c_idx},
                }
                if abs_path:
                    seg_v["abs_path"] = abs_path
                if len(segment_matches) > 1:
                    seg_v["segment_matches"] = segment_matches
                videos.append(seg_v)
    videos.sort(key=_video_sort_key)
    return {"videos": videos, "source": source, "groups": groups}


def _video_sort_key(video: dict[str, Any]) -> tuple[str, int, int, str]:
    match_file = ""
    if isinstance(video.get("match"), dict):
        match_file = str(video["match"].get("file") or "")
    source_name = match_file if video.get("source") == "compressed" and match_file else str(video.get("file") or "")
    stem = Path(source_name).stem
    if "_" in stem and stem.split("_", 1)[0].isdigit():
        stem = stem.split("_", 1)[1]
    stem = re.sub(r"_(?:seg|part|pt|chunk)\d+$", "", stem, flags=re.IGNORECASE)
    seg = 0
    label = str(video.get("segment_label") or "")
    if "/" in label:
        try:
            seg = int(label.split("/", 1)[0])
        except ValueError:
            seg = 0
    elif video.get("offset_sec"):
        seg = int(float(video.get("offset_sec") or 0) * 1000)
    idx = str(video.get("index") or "")
    try:
        idx_num = int(idx)
    except ValueError:
        idx_num = 0
    return (stem.lower(), seg, idx_num, str(video.get("file") or "").lower())


def handle_get_video(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/video. Sends video range response directly."""

    proj_dir = handler._resolve_project_dir(qs)
    proj_out = handler._get_project_output(proj_dir)
    fname = qs.get("file", [""])[0]
    source = qs.get("source", ["compressed"])[0]
    if source == "original":
        abspath = qs.get("abspath", [None])[0]
        if abspath:
            vp = Path(abspath).resolve()
            if not vp.is_file() or vp.suffix.lower() not in VIDEO_EXTENSIONS:
                return handler.send_error(HTTPStatus.NOT_FOUND)
            selected = load_selected_videos(proj_dir)
            allowed: set[Path] = set()
            for p in selected:
                try:
                    allowed.add(p.resolve())
                except OSError:
                    allowed.add(p)
            if vp not in allowed:
                return handler.send_error(HTTPStatus.FORBIDDEN)
        else:
            vp = _find_selected_original_for_request(proj_dir, fname)
            if vp is None:
                return handler.send_error(HTTPStatus.FORBIDDEN)
    else:
        if not _is_safe_basename(fname):
            return handler.send_error(HTTPStatus.FORBIDDEN)
        vp = proj_out / "compressed" / fname
    if not vp.is_file() or vp.suffix.lower() not in VIDEO_EXTS:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    handler._send_video_range(vp)


def handle_get_vmeta(handler: HandlerProtocol, qs: dict[str, Any], stem: str) -> None:
    """Handle GET /api/vmeta/{stem} → .vmeta JSON content."""
    if not stem:
        return handler._send_json({"ok": False, "error": "missing stem"}, 400)
    proj_dir = handler._resolve_project_dir(qs)
    proj_out = handler._get_project_output(proj_dir)
    comp_dir = proj_out / "compressed"
    for p in comp_dir.glob(f"{stem}.*"):
        if p.suffix.lower() in VIDEO_EXTS:
            meta = VideoMeta.read(p)
            if meta is not None:
                from clio.vmeta import _meta_to_dict

                return handler._send_json(_meta_to_dict(meta))
    handler._send_json({"ok": False, "error": "not found"}, 404)


def handle_get_videos_selected(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/videos/selected — load videos.json."""
    proj_dir = handler._resolve_project_dir(qs)
    videos = load_selected_videos(proj_dir)
    data = [str(p) for p in videos]
    handler._send_json({"videos": data})


def handle_put_videos_selected(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/videos/selected — save to videos.json.

    Offline paths are kept: videos.json is an allowlist. Only wrong extensions /
    unparseable values are rejected. Existing files are stored as resolved paths.
    """
    proj_dir = handler._resolve_project_dir(qs)
    raw = obj.get("videos", [])
    if not isinstance(raw, list):
        return handler._send_json({"ok": False, "error": "videos must be a list"}, 400)
    paths: list[Path] = []
    rejected: list[str] = []
    for item in raw:
        if item is None or str(item).strip() == "":
            rejected.append(str(item))
            continue
        p = Path(str(item)).expanduser()
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
            rejected.append(str(item))
            continue
        # Prefer resolved path when the file exists; otherwise keep offline path.
        if resolved.is_file():
            paths.append(resolved)
        else:
            paths.append(resolved if resolved.is_absolute() else p)
    # Dedup while preserving order (compare resolved string form)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).replace("\\", "/").lower()
        if key not in seen:
            seen.add(key)
            unique.append(p)
    save_selected_videos(proj_dir, unique)
    _invalidate_videos_cache(proj_dir)
    payload: dict[str, Any] = {"ok": True, "count": len(unique)}
    if rejected:
        payload["rejected"] = rejected[:20]
        payload["rejected_count"] = len(rejected)
    handler._send_json(payload)


def handle_put_videos_relink(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/videos/relink — replace a broken path in videos.json with a new one."""
    proj_dir = handler._resolve_project_dir(qs)
    old_path = obj.get("old_path")
    new_path = obj.get("new_path")
    if not old_path or not new_path:
        return handler._send_json({"ok": False, "error": "需要提供 old_path 和 new_path"}, 400)
    new = Path(new_path).expanduser()
    try:
        resolved = new.resolve()
    except OSError:
        return handler._send_json({"ok": False, "error": f"无法解析新路径: {new_path}"}, 400)
    if not resolved.is_file() or resolved.suffix.lower() not in VIDEO_EXTS:
        return handler._send_json({"ok": False, "error": f"新路径不是有效的视频文件: {new_path}"}, 400)
    videos = load_selected_videos(proj_dir)
    old_str = str(Path(old_path).expanduser().resolve())
    matched_idx = None
    for idx, v in enumerate(videos):
        try:
            if str(v.resolve()) == old_str:
                matched_idx = idx
                break
        except OSError:
            if str(v) == old_str or str(v) == str(Path(old_path)):
                matched_idx = idx
                break
    if matched_idx is None:
        old_name = Path(old_path).name
        matches = [(i, v) for i, v in enumerate(videos) if Path(v).name == old_name]
        if len(matches) == 1:
            matched_idx = matches[0][0]
        elif len(matches) > 1:
            return handler._send_json({"ok": False, "error": f"找到多个同名文件 ({old_name})，请指定完整路径"}, 400)
        else:
            return handler._send_json({"ok": False, "error": f"未在项目视频列表中找到: {old_path}"}, 404)
    videos[matched_idx] = resolved
    save_selected_videos(proj_dir, videos)
    _invalidate_videos_cache(proj_dir)
    handler._send_json({"ok": True, "path": str(resolved)})

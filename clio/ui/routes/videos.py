"""Route handlers: /api/videos, /api/video, /api/videos/selected"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from clio._constants import VIDEO_EXTENSIONS, VIDEO_EXTS
from clio.i18n import t
from clio.identity import prefer_canonical_compressed
from clio.index import ArtifactIndex
from clio.tasks._helpers import _path_key, _stem_variants
from clio.tasks._video_loader import load_selected_videos, save_selected_videos
from clio.ui.services.file_service import (
    _find_compressed_for_original,
    _find_original_for_compressed,
    _find_texts_dirs,
    _is_safe_basename,
)
from clio.utils import (
    JsonValue,
    get_duration_sec,
    probe_media_details,
    resolve_binary,
    safe_basename,
    validate_within_root,
)
from clio.vmeta import VideoMeta, _meta_to_dict

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_SEG_RE = re.compile(r"^(.+)_(?:seg|part|pt|chunk)(\d+)$", re.IGNORECASE)
_MEDIA_PROBE_ERRORS = (OSError, subprocess.SubprocessError, TypeError, ValueError)
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
        write_json_atomic(_disk_cache_path(proj_out, source), cast(JsonValue, data))
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


def _compressed_matches_selection(
    compressed: Path,
    stem: str,
    selected_set: set[Path],
    orig: str | None,
) -> bool:
    """True if this compressed artifact belongs to the curated selection.

    Absolute source paths win. A basename/stem fallback is kept for artifacts
    without sidecar metadata and for relinked sources, but it only applies when
    exactly one selected path shares the stem — never when two originals in
    different directories collide.
    """
    selected_keys = {_path_key(path) for path in selected_set}
    candidates: list[Path] = []
    meta = VideoMeta.read(compressed)
    if meta is not None and meta.source_path:
        candidates.append(Path(meta.source_path))
    if orig:
        candidates.append(Path(orig))

    for cand in candidates:
        if _path_key(cand) in selected_keys:
            return True
    if any(cand.is_absolute() and cand.is_file() for cand in candidates):
        # The artifact names a live source outside the curated selection.
        return False

    fallback_stems: set[str] = set()
    for cand in candidates:
        fallback_stems.update(_stem_variants(cand.stem))
    suffix = stem.split("_", 1)[1] if "_" in stem else stem
    fallback_stems.update(_stem_variants(suffix, include_segment_base=True))
    hits = {path for path in selected_set if _stem_variants(path.stem, include_segment_base=True) & fallback_stems}
    return len(hits) == 1


def _load_videos_json_selection(proj_dir: Path) -> list[Path] | None:
    """Load videos.json selection in registration order.

    Returns None only when videos.json is absent (legacy project).
    Empty list / offline paths still yield a list so selection mode stays active.
    """
    if not (proj_dir / "videos.json").is_file():
        return None
    return load_selected_videos(proj_dir)


def _selection_path_set(selected: list[Path] | None) -> set[Path] | None:
    if selected is None:
        return None
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
    selected_ordered = _load_videos_json_selection(proj_dir)
    selected_set = _selection_path_set(selected_ordered)
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

    payload = _build_videos_payload(
        handler,
        proj_dir,
        proj_out,
        comp_dir,
        source,
        cfg,
        selected_set=selected_set,
        selected_ordered=selected_ordered,
    )
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
        # Distinguish offline paths in the signature (external media status).
        return ("missing", str(path))


def _artifact_dirs(cfg: Any, proj_out: Path) -> tuple[Path, Path, Path]:
    """Resolve texts/scripts/transcripts dirs from AppConfig with safe fallbacks."""

    def _path_attr(name: str) -> Path | None:
        val = getattr(cfg, name, None)
        return val if isinstance(val, Path) else None

    def _subdir(section: Any, name: str, default: str) -> str:
        val = getattr(section, name, None) if section is not None else None
        if isinstance(val, str) and val.strip():
            leaf = Path(val).name
            if leaf and leaf not in (".", ".."):
                return leaf
        return default

    texts = _path_attr("texts_dir") or (proj_out / _subdir(getattr(cfg, "analyze", None), "texts_subdir", "texts"))
    scripts = _path_attr("scripts_dir") or (
        proj_out / _subdir(getattr(cfg, "script", None), "scripts_subdir", "scripts")
    )
    transcripts = _path_attr("transcripts_dir") or (
        proj_out / _subdir(getattr(cfg, "whisper", None), "transcripts_subdir", "transcripts")
    )
    return texts, scripts, transcripts


def _videos_cache_signature(proj_dir: Path, proj_out: Path, comp_dir: Path, cfg: Any) -> tuple[Any, ...]:
    texts_dir, scripts_dir, transcripts_dir = _artifact_dirs(cfg, proj_out)
    text_dirs = tuple(_find_texts_dirs(proj_out, preferred_subdir=texts_dir.name))
    videos_json = proj_dir / "videos.json"
    selected_videos = load_selected_videos(proj_dir)
    selected_fingerprints: list[tuple[str, tuple[Any, ...]]] = []
    for video in selected_videos:
        # Source files may live outside the project (including on a NAS), so
        # project-dir fingerprints alone cannot invalidate an offline payload.
        selected_fingerprints.append((str(video), _file_fingerprint(video)))
    whisper_subdir = getattr(getattr(cfg, "whisper", None), "transcripts_subdir", None)
    if not isinstance(whisper_subdir, str):
        whisper_subdir = transcripts_dir.name
    ffprobe = getattr(getattr(cfg, "paths", None), "ffprobe", None)
    return (
        _file_fingerprint(videos_json),  # must invalidate when selection changes
        tuple(selected_fingerprints),  # must invalidate when external media returns
        _dir_fingerprint(proj_dir, video_only=True),
        _dir_fingerprint(comp_dir),
        tuple((str(td), _dir_fingerprint(td, json_only=True)) for td in text_dirs),
        _dir_fingerprint(scripts_dir, json_only=True),
        _dir_fingerprint(transcripts_dir, json_only=True),
        _dir_fingerprint(proj_out / "covers"),  # AI cover thumbs
        whisper_subdir,
        ffprobe,
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


def _build_artifact_index(
    proj_dir: Path,
    proj_out: Path,
    comp_dir: Path,
    cfg: Any,
) -> tuple[ArtifactIndex, dict[str, str], dict[str, str]]:
    texts_dir, scripts_dir, transcripts_dir = _artifact_dirs(cfg, proj_out)
    index = ArtifactIndex(
        output_dir=proj_out,
        project_dir=proj_dir,
        compressed_dir=comp_dir,
        texts_dir=texts_dir,
        scripts_dir=scripts_dir,
        transcripts_dir=transcripts_dir,
    )
    index.build()
    text_titles = {g.compressed.index: g.texts[0].title for g in index.all_groups() if g.texts}
    return index, text_titles, _cover_map(proj_out)


def _compressed_match(orig: str | None, proj_dir: Path) -> dict[str, Any] | None:
    if not orig:
        return None
    op = Path(orig)
    abs_match: str | None = None
    match_missing = False
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
    return {
        "source": "original",
        "file": op.name,
        "abs_path": abs_match,
        "missing": match_missing,
    }


def _compressed_video_entry(
    p: Path,
    idx: str,
    group: Any,
    orig: str | None,
    proj_dir: Path,
    text_titles: dict[str, str],
    cover_map: dict[str, str],
) -> tuple[dict[str, Any], str | None, int | None]:
    meta = VideoMeta.read(p)
    group_key, seg_num = (
        (None, None)
        if meta is not None and meta.split_info is None and not meta.is_split_segment
        else _parse_segment_info(p.stem)
    )
    text_json = group.texts[0].path.name if group and group.texts else None
    cover_file = group.cover.path.name if group and group.cover else _cover_for_text_json(text_json, cover_map, idx)
    video: dict[str, Any] = {
        "file": p.name,
        "source": "compressed",
        "index": idx,
        "title": group.texts[0].title if group and group.texts else text_titles.get(idx, ""),
        "text_json": text_json,
        "cover_file": cover_file,
        "script_json": group.script.path.name if group and group.script else None,
        "transcript_file": group.transcript.stem if group and group.transcript else None,
        "match": _compressed_match(orig, proj_dir),
        "group_key": group_key,
        "segment_label": None,
    }
    return video, group_key, seg_num


def _fill_compressed_group_metadata(
    videos: list[dict[str, Any]],
    group_members: dict[str, list[tuple[str, int, str]]],
    comp_dir: Path,
    proj_dir: Path,
    cfg: Any,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    try:
        ffprobe = resolve_binary(cfg.paths.ffprobe, "ffprobe")
    except _MEDIA_PROBE_ERRORS:
        ffprobe = None
    by_file = {video["file"]: video for video in videos}
    for group_key, members in group_members.items():
        members.sort(key=lambda item: item[1])
        total = len(members)
        groups[group_key] = {
            "original_stem": group_key,
            "indices": [member[0] for member in members],
            "total": total,
        }
        offsets: dict[str, float] = {}
        durations: dict[str, float] = {}
        for member_idx, _seg_num, fname in members:
            meta = VideoMeta.read(comp_dir / fname)
            if meta and meta.split_info:
                offsets[member_idx] = meta.split_info.offset_sec
                durations[member_idx] = meta.split_info.segment_duration_sec
        missing = [member for member in members if member[0] not in offsets]
        if missing and total > 1 and ffprobe:
            found = _find_original_for_compressed(f"000_{group_key}", proj_dir, comp_dir, project_dir=proj_dir)
            if found:
                try:
                    duration = get_duration_sec(Path(found), ffprobe)
                    segment_duration = duration / total
                    for member_idx, seg_num, _fname in missing:
                        offsets[member_idx] = round((seg_num - 1) * segment_duration, 1)
                        durations[member_idx] = round(segment_duration, 1)
                except _MEDIA_PROBE_ERRORS:
                    pass
        for member_idx, seg_num, fname in members:
            video = by_file.get(fname)
            if video is not None:
                video["segment_label"] = f"{seg_num}/{total}"
                video["offset_sec"] = offsets.get(member_idx, 0.0)
                video["duration_sec"] = durations.get(member_idx, 0.0)
    return groups


def _build_compressed_payload(
    proj_dir: Path,
    comp_dir: Path,
    cfg: Any,
    index: ArtifactIndex,
    text_titles: dict[str, str],
    cover_map: dict[str, str],
    selected_set: set[Path] | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    videos: list[dict[str, Any]] = []
    group_members: dict[str, list[tuple[str, int, str]]] = {}
    if not comp_dir.is_dir():
        return videos, {}
    compressed_paths = prefer_canonical_compressed(
        sorted(p for p in comp_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    )
    for p in compressed_paths:
        stem = p.stem
        idx = stem.split("_", 1)[0] if "_" in stem else ""
        group = index.lookup(compressed_stem=stem)
        orig = _find_original_for_compressed(stem, proj_dir, comp_dir, project_dir=proj_dir)
        if selected_set and not _compressed_matches_selection(p, stem, selected_set, orig):
            continue
        video, group_key, seg_num = _compressed_video_entry(p, idx, group, orig, proj_dir, text_titles, cover_map)
        if group_key is not None and seg_num is not None:
            group_members.setdefault(group_key, []).append((idx, seg_num, p.name))
        videos.append(video)
    groups = _fill_compressed_group_metadata(videos, group_members, comp_dir, proj_dir, cfg)
    return videos, groups


def _original_video_paths(
    selected_ordered: list[Path] | None,
    selected_set: set[Path] | None,
) -> tuple[list[Path], bool]:
    if selected_ordered is not None:
        return list(selected_ordered), True
    if selected_set is not None:
        return sorted(selected_set, key=lambda p: p.name.lower()), False
    return [], False


def _offline_original_entry(p: Path) -> dict[str, Any]:
    try:
        abs_shown = str(p.resolve())
    except OSError:
        abs_shown = str(p)
    return {
        "file": p.name,
        "source": "original",
        "index": None,
        "match": None,
        "abs_path": abs_shown,
        "missing": True,
    }


def _original_segment_offsets(p: Path, comp: list[tuple[str, str]], ffprobe: str | None) -> dict[str, float]:
    offsets: dict[str, float] = {}
    if len(comp) <= 1 or not ffprobe:
        return offsets
    try:
        duration = get_duration_sec(p, ffprobe)
        segment_duration = duration / len(comp)
        for i, (_, idx) in enumerate(comp):
            offsets[idx] = round(i * segment_duration, 1)
    except _MEDIA_PROBE_ERRORS:
        pass
    return offsets


def _original_segment_entry(
    c_file: str,
    c_idx: str,
    rel_name: str,
    abs_path: str,
    seg_offsets: dict[str, float],
    segment_matches: list[dict[str, str]],
    index: ArtifactIndex,
    text_titles: dict[str, str],
    cover_map: dict[str, str],
) -> dict[str, Any]:
    group = index.lookup(compressed_stem=Path(c_file).stem)
    text_json = group.texts[0].path.name if group and group.texts else None
    cover_file = group.cover.path.name if group and group.cover else _cover_for_text_json(text_json, cover_map, c_idx)
    entry: dict[str, Any] = {
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
        entry["abs_path"] = abs_path
    if len(segment_matches) > 1:
        entry["segment_matches"] = segment_matches
    return entry


def _build_original_payload(
    handler: HandlerProtocol,
    proj_dir: Path,
    comp_dir: Path,
    index: ArtifactIndex,
    text_titles: dict[str, str],
    cover_map: dict[str, str],
    selected_set: set[Path] | None,
    selected_ordered: list[Path] | None,
) -> tuple[list[dict[str, Any]], bool]:
    try:
        cfg = handler._get_config(proj_dir)
        ffprobe = resolve_binary(cfg.paths.ffprobe, "ffprobe")
    except _MEDIA_PROBE_ERRORS:
        ffprobe = None
    video_paths, preserve_registration_order = _original_video_paths(selected_ordered, selected_set)
    videos: list[dict[str, Any]] = []
    proj_abs = proj_dir.resolve()
    for p in video_paths:
        try:
            online = p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
        except OSError:
            online = False
        if not online:
            videos.append(_offline_original_entry(p))
            continue
        abs_path = str(p.resolve())
        try:
            rel_name = p.relative_to(proj_abs).as_posix()
        except ValueError:
            rel_name = p.name
        comp = _find_compressed_for_original(p.stem, comp_dir)
        if not comp:
            orig_groups = index.lookup(original_stem=p.stem)
            has_transcript = any(g.transcript is not None for g in (orig_groups or []))
            video: dict[str, Any] = {
                "file": rel_name,
                "source": "original",
                "index": None,
                "match": None,
                "transcript_file": p.stem if has_transcript else None,
            }
            if abs_path:
                video["abs_path"] = abs_path
            videos.append(video)
            continue
        seg_offsets = _original_segment_offsets(p, comp, ffprobe)
        segment_matches = [{"source": "compressed", "file": cf, "index": ci} for cf, ci in comp]
        for c_file, c_idx in comp:
            videos.append(
                _original_segment_entry(
                    c_file,
                    c_idx,
                    rel_name,
                    abs_path,
                    seg_offsets,
                    segment_matches,
                    index,
                    text_titles,
                    cover_map,
                )
            )
    return videos, preserve_registration_order


def _build_videos_payload(
    handler: HandlerProtocol,
    proj_dir: Path,
    proj_out: Path,
    comp_dir: Path,
    source: str,
    cfg: Any,
    *,
    selected_set: set[Path] | None = None,
    selected_ordered: list[Path] | None = None,
) -> dict[str, Any]:
    index, text_titles, cover_map = _build_artifact_index(proj_dir, proj_out, comp_dir, cfg)
    if source == "compressed":
        videos, groups = _build_compressed_payload(proj_dir, comp_dir, cfg, index, text_titles, cover_map, selected_set)
        preserve_registration_order = False
    else:
        videos, preserve_registration_order = _build_original_payload(
            handler,
            proj_dir,
            comp_dir,
            index,
            text_titles,
            cover_map,
            selected_set,
            selected_ordered,
        )
        groups = {}
    if not preserve_registration_order:
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
    if source == "cut":
        day = qs.get("day", [""])[0]
        if not _is_safe_basename(day) or not _is_safe_basename(fname):
            return handler.send_error(HTTPStatus.FORBIDDEN)
        cuts_dir = proj_out / "cuts" / safe_basename(day)
        try:
            vp = validate_within_root(cuts_dir / fname, proj_out)
        except (OSError, ValueError):
            return handler.send_error(HTTPStatus.FORBIDDEN)
        if vp.parent != cuts_dir.resolve():
            return handler.send_error(HTTPStatus.FORBIDDEN)
    elif source == "original":
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
            selected_original = _find_selected_original_for_request(proj_dir, fname)
            if selected_original is None:
                return handler.send_error(HTTPStatus.FORBIDDEN)
            vp = selected_original
    else:
        if not _is_safe_basename(fname):
            return handler.send_error(HTTPStatus.FORBIDDEN)
        try:
            vp = validate_within_root(proj_out / "compressed" / fname, proj_out)
        except ValueError:
            return handler.send_error(HTTPStatus.FORBIDDEN)
    if not vp.is_file() or vp.suffix.lower() not in VIDEO_EXTS:
        return handler.send_error(HTTPStatus.NOT_FOUND)
    handler._send_video_range(vp)


def handle_get_vmeta(handler: HandlerProtocol, qs: dict[str, Any], stem: str) -> None:
    """Handle GET /api/vmeta/{stem} → .vmeta JSON content."""
    if not stem:
        return handler._send_json({"ok": False, "error": "missing stem"}, 400)
    if not _is_safe_basename(stem):
        return handler._send_json({"ok": False, "error": "forbidden"}, 403)
    proj_dir = handler._resolve_project_dir(qs)
    proj_out = handler._get_project_output(proj_dir)
    comp_dir = proj_out / "compressed"

    for p in sorted(comp_dir.glob(f"{stem}.*")):
        if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            try:
                validate_within_root(p, proj_out)
            except ValueError:
                continue
            meta = VideoMeta.read(p)
            if meta is not None:
                return handler._send_json(_meta_to_dict(meta))
    handler._send_json({"ok": False, "error": "not found"}, 404)


def _resolve_info_target(
    qs: dict[str, Any],
    proj_dir: Path,
    proj_out: Path,
    source: str,
    fname: str,
) -> Path | None:
    """Resolve a video list row to a concrete path, mirroring /api/video rules."""
    if source == "original":
        abspath = (qs.get("abspath", [""])[0] or "").strip()
        selected = {_video_key(p) for p in load_selected_videos(proj_dir)}
        if abspath:
            cand = Path(abspath)
            try:
                cand = cand.resolve()
            except OSError:
                cand = Path(abspath)
            if cand.suffix.lower() in VIDEO_EXTENSIONS and _video_key(cand) in selected:
                return cand
        if fname:
            return _find_selected_original_for_request(proj_dir, fname)
        return None

    if not _is_safe_basename(fname):
        return None
    try:
        cand = validate_within_root(proj_out / "compressed" / fname, proj_out)
    except (OSError, ValueError):
        return None
    if cand.suffix.lower() not in VIDEO_EXTS:
        return None
    return cand


def _compression_info(metas: list[VideoMeta]) -> dict[str, Any] | None:
    if not metas:
        return None
    base = metas[0]
    target_size = sum(max(0, meta.target_size) for meta in metas)
    target_duration = round(sum(max(0.0, meta.target_duration_sec) for meta in metas), 3)
    ratio: float | None = None
    if base.source_size > 0 and target_size > 0:
        ratio = round(target_size / base.source_size, 4)
    return {
        "source_path": base.source_path,
        "source_size_bytes": base.source_size,
        "target_size_bytes": target_size,
        "ratio": ratio,
        "source_duration_sec": base.source_duration_sec,
        "target_duration_sec": target_duration,
        "settings": base.compress_settings or {},
    }


def _read_compression_metas(target: Path, source: str, comp_dir: Path) -> list[VideoMeta]:
    if source == "compressed":
        meta = VideoMeta.read(target)
        return [meta] if meta is not None else []
    matches = _find_compressed_for_original(target.stem, comp_dir)
    metas: list[VideoMeta] = []
    for fname, _index in matches or []:
        meta = VideoMeta.read(comp_dir / fname)
        if meta is not None:
            metas.append(meta)
    return metas


def handle_get_video_info(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/video/info — stat + .vmeta + cached ffprobe details."""
    proj_dir = handler._resolve_project_dir(qs)
    proj_out = handler._get_project_output(proj_dir)
    comp_dir = proj_out / "compressed"
    source = "original" if (qs.get("source", ["compressed"])[0] or "") == "original" else "compressed"
    fname = qs.get("file", [""])[0] or ""

    target = _resolve_info_target(qs, proj_dir, proj_out, source, fname)
    if target is None:
        return handler._send_json({"ok": False, "error": "not found"}, 404)

    try:
        st = target.stat() if target.is_file() else None
    except OSError:
        st = None

    payload: dict[str, Any] = {
        "ok": True,
        "source": source,
        "file": fname,
        "abs_path": str(target),
        "exists": st is not None,
        "size_bytes": st.st_size if st is not None else None,
        "mtime": int(st.st_mtime) if st is not None else None,
    }

    metas = _read_compression_metas(target, source, comp_dir) if st is not None else []
    compression = _compression_info(metas)
    if compression is not None:
        payload["compression"] = compression
    if st is not None and metas:
        payload["duration_sec"] = metas[0].source_duration_sec if source == "original" else metas[0].target_duration_sec

    probe_error: str | None = None
    if st is not None:
        try:
            cfg = handler._get_config(proj_dir)
            ffprobe = resolve_binary(cfg.paths.ffprobe, "ffprobe")
        except _MEDIA_PROBE_ERRORS:
            ffprobe = None
        if ffprobe:
            try:
                probed = probe_media_details(target, ffprobe)
            except _MEDIA_PROBE_ERRORS as exc:
                probe_error = str(exc)
            else:
                # Keep stat/.vmeta fallbacks when ffprobe omits a field.
                for key, value in probed.items():
                    if value:
                        payload[key] = value
        else:
            probe_error = "ffprobe unavailable"
    if probe_error:
        payload["probe_error"] = probe_error

    handler._send_json(payload)


def handle_get_videos_selected(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    """Handle GET /api/videos/selected — load videos.json."""
    proj_dir = handler._resolve_project_dir(qs)
    videos = load_selected_videos(proj_dir)
    data = [str(p) for p in videos]
    handler._send_json({"videos": data})


def _video_key(path: Path) -> str:
    """Canonical comparison key for a stored video path.

    Case- and separator-insensitive so the same file added twice (different
    slash style, letter case, or with ``.``/``..`` segments) is deduplicated.
    """
    try:
        return str(path.resolve()).lower()
    except OSError:
        return str(path).replace("\\", "/").lower()


def _coerce_video_path(item: Any) -> Path | None:
    """Resolve a raw videos.json entry to the path we should store.

    Returns None when the value is empty, unparseable, or has a non-video
    extension. Offline paths are kept as-is because videos.json is an
    allowlist (the file may live on a detached drive).
    """
    if item is None or str(item).strip() == "":
        return None
    p = Path(str(item)).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    if resolved.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    if resolved.is_file():
        return resolved
    return resolved if resolved.is_absolute() else p


def handle_put_videos_selected(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/videos/selected — save to videos.json.

    Offline paths are kept: videos.json is an allowlist. Only wrong extensions /
    unparseable values are rejected. Existing files are stored as resolved paths.

    When the caller also sends ``candidates`` (the paths the user just picked),
    the response carries authoritative ``added`` / ``already`` counts computed
    against the previously stored list, so the UI never guesses.
    """
    proj_dir = handler._resolve_project_dir(qs)
    raw = obj.get("videos", [])
    if not isinstance(raw, list):
        return handler._send_json({"ok": False, "error": "videos must be a list"}, 400)
    previous_keys = {_video_key(p) for p in load_selected_videos(proj_dir)}
    paths: list[Path] = []
    rejected: list[str] = []
    for item in raw:
        stored = _coerce_video_path(item)
        if stored is None:
            rejected.append(str(item))
        else:
            paths.append(stored)
    # Dedup while preserving order (compare the shared normalized key)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = _video_key(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    save_selected_videos(proj_dir, unique)
    _invalidate_videos_cache(proj_dir)
    payload: dict[str, Any] = {"ok": True, "count": len(unique)}
    candidates = obj.get("candidates")
    if isinstance(candidates, list):
        added = 0
        already = 0
        counted: set[str] = set()
        for item in candidates:
            stored = _coerce_video_path(item)
            if stored is None:
                continue
            key = _video_key(stored)
            if key in counted:
                continue
            counted.add(key)
            if key in previous_keys:
                already += 1
            else:
                added += 1
        payload["added"] = added
        payload["already"] = already
    if rejected:
        payload["rejected"] = rejected[:20]
        payload["rejected_count"] = len(rejected)
    handler._send_json(payload)


def handle_put_videos_relink(handler: HandlerProtocol, qs: dict[str, Any], obj: dict) -> None:
    """Handle PUT /api/videos/relink — replace a broken path in videos.json with a new one."""
    proj_dir = handler._resolve_project_dir(qs)
    old_path = obj.get("old_path")
    new_path = obj.get("new_path")
    if not isinstance(old_path, str) or not isinstance(new_path, str):
        return handler._send_json({"ok": False, "error": t("api.relink_missing_paths")}, 400)
    old_path = old_path.strip()
    new_path = new_path.strip()
    if not old_path or not new_path:
        return handler._send_json({"ok": False, "error": t("api.relink_missing_paths")}, 400)
    new = Path(new_path).expanduser()
    try:
        resolved = new.resolve()
    except (OSError, ValueError):
        return handler._send_json({"ok": False, "error": t("api.relink_unresolvable", {"path": new_path})}, 400)
    if not resolved.is_file() or resolved.suffix.lower() not in VIDEO_EXTS:
        return handler._send_json({"ok": False, "error": t("api.relink_invalid_video", {"path": new_path})}, 400)
    videos = load_selected_videos(proj_dir)
    try:
        old_str = str(Path(old_path).expanduser().resolve())
    except (OSError, ValueError):
        old_str = old_path
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
            return handler._send_json(
                {"ok": False, "error": t("api.relink_ambiguous", {"name": old_name})},
                400,
            )
        else:
            return handler._send_json({"ok": False, "error": t("api.video_not_in_project", {"path": old_path})}, 404)
    new_key = _video_key(resolved)
    for idx, video in enumerate(videos):
        if idx == matched_idx:
            continue
        if _video_key(video) == new_key:
            return handler._send_json(
                {"ok": False, "error": t("api.video_already_in_project", {"path": resolved})},
                409,
            )
    videos[matched_idx] = resolved
    save_selected_videos(proj_dir, videos)
    _invalidate_videos_cache(proj_dir)
    handler._send_json({"ok": True, "path": str(resolved)})

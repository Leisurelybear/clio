"""JianYing Pro (剪映专业版) draft export.

Generates draft_content.json from plan.json output.
Target format: JianYing 5.9 (plain JSON, unencrypted).
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from clio.config.models import CANVAS_PRESETS
from clio.cut import parse_time_range
from clio.identity import legacy_segment_offset_sec, load_identity
from clio.plan_readiness import expand_index_keys
from clio.utils import get_duration_sec, write_text_atomic

logger = logging.getLogger("clio.export.jianying")


def _to_microseconds(seconds: float) -> int:
    return int(seconds * 1_000_000)


def _resolve_video_by_prefix(
    index: str,
    videos: list[Path],
    ffprobe: str | None = None,
    index_width: int = 3,
) -> tuple[Path, int] | None:
    """Fallback: find video by {index}_ prefix (e.g. '001' → '001_GL010683.mp4').

    Works for compressed files but not originals.
    """
    if ffprobe is None:
        return None
    index_keys = expand_index_keys(index, index_width=index_width)
    for v in videos:
        if v.stem.split("_", 1)[0] in index_keys:
            try:
                duration = get_duration_sec(v, ffprobe)
            except Exception:
                return None
            return v.resolve(), _to_microseconds(duration)
    return None


def _build_index_to_source(texts_dir: Path, *, index_width: int = 3) -> dict[str, str]:
    """Read text JSONs to build {index_str: source_stem} mapping.

    Text JSON contains 'index' (int or str) and 'source_file' (e.g. 'GL010695.mp4').
    Returns mapping like {'001': 'GL010695', '1': 'GL010695', ...}.

    For v2 artifacts, prefers media_identity.original_stem over source_file.
    """
    mapping: dict[str, str] = {}
    if not texts_dir.is_dir():
        logger.debug("texts_dir 不存在: %s", texts_dir)
        return mapping
    for p in sorted(texts_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("读取失败 %s: %s", p, e)
            continue
        raw_idx = data.get("index")
        if raw_idx is None:
            logger.debug("跳过(无index): %s", p.name)
            continue

        # Prefer media_identity.original_stem for v2, fall back to source_file for v1
        identity = load_identity(data)
        if identity is not None:
            stem = identity.original_stem
            logger.debug("文件=%s, index=%r, media_identity.original_stem=%r", p.name, raw_idx, stem)
        else:
            source = data.get("source_file", "")
            logger.debug("文件=%s, index=%r, source_file=%r", p.name, raw_idx, source)
            if not source:
                logger.debug("跳过: 缺少 source_file 且无 media_identity")
                continue
            stem = Path(source).stem

        for key in expand_index_keys(raw_idx, index_width=index_width):
            mapping[key] = stem
        logger.debug("映射: %s -> %s", sorted(expand_index_keys(raw_idx, index_width=index_width)), stem)
    return mapping


def _build_index_to_offset(texts_dir: Path, *, index_width: int = 3) -> dict[str, float]:
    """Read segment offsets from analysis JSON media_identity blocks.

    Returns {index_str: offset_sec} for split clips, empty dict for non-split.
    """
    offsets: dict[str, float] = {}
    if not texts_dir.is_dir():
        return offsets
    for p in sorted(texts_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        identity = load_identity(data)
        if identity is None:
            continue
        off = legacy_segment_offset_sec(identity)
        if off:
            for key in expand_index_keys(identity.index, index_width=index_width):
                offsets[key] = off
    return offsets


def _resolve_video(
    source_stem: str,
    videos: list[Path],
    ffprobe: str | None = None,
) -> tuple[Path, int] | None:
    """Find original video by source_stem (e.g. 'GL010695').

    Returns (absolute_path, duration_us) or None if not found.
    """
    if ffprobe is None:
        print("  [跳过] ffprobe 未配置，无法读取视频时长")
        return None
    # Case-insensitive stem match
    target_lower = source_stem.lower()
    for v in videos:
        if v.stem.lower() == target_lower:
            try:
                duration = get_duration_sec(v, ffprobe)
            except Exception:
                return None
            return v.resolve(), _to_microseconds(duration)
    return None


def _build_materials(
    plan_data: dict,
    source_videos: list[Path],
    ffprobe: str | None = None,
    index_to_source: dict[str, str] | None = None,
    index_width: int = 3,
) -> tuple[dict, dict[str, str], dict[int, str]]:
    """Build materials.videos and materials.texts.

    index_to_source maps plan index to source_stem (from texts/*.json source_file).
    Falls back to {index}_ prefix matching if mapping unavailable.

    Returns (materials_dict, index_to_material_id, seq_text_ids).
    """
    videos: list[dict] = []
    texts: list[dict] = []
    index_to_material_id: dict[str, str] = {}
    seen_indices: set[str] = set()
    seq_text_ids: dict[int, str] = {}

    index_to_source = index_to_source or {}

    for i, seg in enumerate(plan_data.get("sequence", [])):
        idx = seg.get("index", "")
        if not idx:
            continue

        # Video material (one per unique index)
        if idx not in seen_indices:
            seen_indices.add(idx)
            source_stem = index_to_source.get(idx)
            if source_stem:
                resolved = _resolve_video(source_stem, source_videos, ffprobe)
            else:
                # Fallback: try matching by {index}_ prefix (works for compressed files)
                resolved = _resolve_video_by_prefix(idx, source_videos, ffprobe, index_width=index_width)
            if resolved is None:
                print(f"  [跳过] 视频素材 [{idx}] 未找到，跳过相关片段")
                continue
            vid_path, duration_us = resolved
            mat_id = str(uuid.uuid4())
            index_to_material_id[idx] = mat_id
            videos.append(
                {
                    "id": mat_id,
                    "type": "video",
                    "path": str(vid_path),
                    "duration": duration_us,
                }
            )

        # Text material: on-screen captions come from plan.subtitle only.
        # voiceover_hint is narration/preview and must not drive CapCut text tracks (P2-P51).
        caption = (seg.get("subtitle") or "").strip()
        if caption:
            text_id = str(uuid.uuid4())
            content = {
                "text": caption,
                "font_color": "#FFFFFF",
                "font_size": 18,
                "bold": False,
            }
            texts.append(
                {
                    "id": text_id,
                    "content": json.dumps(content, ensure_ascii=False),
                }
            )
            seq_text_ids[i] = text_id

    return (
        {
            "videos": videos,
            "texts": texts,
            "audios": [],
            "stickers": [],
            "video_effects": [],
            "material_animations": [],
            "transitions": [],
            "masks": [],
            "common_masks": [],
            "canvases": [],
            "speeds": [],
            "audio_fades": [],
            "placeholder_infos": [],
            "vocal_separations": [],
        },
        index_to_material_id,
        seq_text_ids,
    )


def _build_tracks(
    plan_data: dict,
    index_to_material_id: dict[str, str],
    seq_text_ids: dict[int, str] | None = None,
    index_to_offset: dict[str, float] | None = None,
) -> list[dict]:
    """Build video and text tracks from plan sequence."""
    video_segments: list[dict] = []
    text_segments: list[dict] = []
    accumulated_us = 0
    skipped_count = 0
    index_to_offset = index_to_offset or {}

    for i, seg in enumerate(plan_data.get("sequence", [])):
        idx = seg.get("index", "")
        if idx not in index_to_material_id:
            skipped_count += 1
            continue

        timeline_str = (seg.get("use_timeline") or "").strip()
        try:
            start_sec, end_sec = parse_time_range(timeline_str)
        except (ValueError, TypeError):
            skipped_count += 1
            print(f"  [跳过] 片段 [{idx}] 时间格式无效: {timeline_str}")
            continue

        duration_us = _to_microseconds(end_sec - start_sec)
        if duration_us <= 0:
            skipped_count += 1
            print(f"  [跳过] 片段 [{idx}] 时长为 0: {timeline_str}")
            continue

        offset = index_to_offset.get(idx, 0.0)
        material_id = index_to_material_id[idx]
        seg_uuid = str(uuid.uuid4())

        video_segments.append(
            {
                "id": seg_uuid,
                "material_id": material_id,
                "target_timerange": {
                    "start": accumulated_us,
                    "duration": duration_us,
                },
                "source_timerange": {
                    "start": _to_microseconds(start_sec + offset),
                    "duration": duration_us,
                },
            }
        )

        # Text segment for voiceover
        text_mat_id = (seq_text_ids or {}).get(i)
        if text_mat_id:
            text_segments.append(
                {
                    "id": str(uuid.uuid4()),
                    "material_id": text_mat_id,
                    "target_timerange": {
                        "start": accumulated_us,
                        "duration": duration_us,
                    },
                }
            )

        accumulated_us += duration_us

    tracks = []
    if video_segments:
        tracks.append(
            {
                "id": str(uuid.uuid4()),
                "type": "video",
                "segments": video_segments,
            }
        )
    if text_segments:
        tracks.append(
            {
                "id": str(uuid.uuid4()),
                "type": "text",
                "segments": text_segments,
            }
        )

    if skipped_count:
        print(f"  [导出] {skipped_count} 个片段因素材缺失或时间格式错误被跳过")

    return tracks


def export_plan_to_jianying(
    plan_path: Path,
    output_dir: Path,
    media_dir: Path | None = None,
    day_label: str = "day1",
    ffprobe: str | None = None,
    texts_dir: Path | None = None,
    canvas_ratio: str = "16:9",
    index_width: int = 3,
    *,
    project_dir: Path | None = None,
    input_dir: Path | None = None,
) -> Path:
    """Generate JianYing draft from plan JSON.

    texts_dir is used to resolve plan index → source_file → original video.
    Falls back to {index}_ prefix matching if not provided.

    Preferred media source: project_dir/videos.json.
    Legacy: media_dir / input_dir scanned with find_videos when project_dir is unset.

    Returns path to the output draft directory.
    """
    if not plan_path.is_file():
        raise FileNotFoundError(f"plan 文件不存在: {plan_path}")

    plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    sequence = plan_data.get("sequence", [])
    if not sequence:
        print(f"  [警告] plan 文件为空序列: {plan_path}")

    index_to_source = _build_index_to_source(texts_dir, index_width=index_width) if texts_dir else {}
    index_to_offset = _build_index_to_offset(texts_dir, index_width=index_width) if texts_dir else {}
    logger.debug("texts_dir=%s, index_to_source=%s", texts_dir, index_to_source)
    if texts_dir:
        logger.debug("texts_dir exists=%s, files=%s", texts_dir.is_dir(), list(texts_dir.glob("*.json")))

    legacy_media = input_dir if input_dir is not None else media_dir
    if project_dir:
        from clio.tasks._video_loader import load_selected_videos

        videos = list(load_selected_videos(project_dir))
        if not videos:
            print(
                f"  [警告] 项目 videos.json 为空或不存在: {project_dir / 'videos.json'}。"
                f" 请先在 UI「添加视频」或运行 python main.py migrate"
            )
    else:
        from clio.utils import find_videos

        videos = list(find_videos(legacy_media, recursive=True)) if legacy_media else []
        if not videos:
            print("  [警告] 未找到源视频（未设置 project_dir 且 media 扫描为空）")
    materials, index_to_material_id, seq_text_ids = _build_materials(
        plan_data, videos, ffprobe, index_to_source, index_width=index_width
    )
    tracks = _build_tracks(plan_data, index_to_material_id, seq_text_ids, index_to_offset)

    if sequence and not materials["videos"]:
        raise RuntimeError(
            f"[导出失败] plan 有 {len(sequence)} 个片段，但没有任何视频素材可解析"
            "（压缩文件缺失或 videos.json 未匹配），已阻止生成空草稿"
        )

    total_duration_us = 0
    for track in tracks:
        for seg in track.get("segments", []):
            seg_end = seg["target_timerange"]["start"] + seg["target_timerange"]["duration"]
            if seg_end > total_duration_us:
                total_duration_us = seg_end

    draft = {
        "id": str(uuid.uuid4()),
        "name": plan_data.get("day_title", day_label),
        "duration": total_duration_us,
        "fps": 30,
        "canvas_config": CANVAS_PRESETS.get(canvas_ratio, CANVAS_PRESETS["16:9"]),
        "platform": {
            "app_source": "lv",
            "app_version": "5.9.0",
            "os": "windows",
        },
        "materials": materials,
        "tracks": tracks,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    draft_path = output_dir / "draft_content.json"
    write_text_atomic(draft_path, json.dumps(draft, indent=2, ensure_ascii=False))
    print(f"  [导出] JianYing 草稿已生成: {output_dir}")
    total_sec = total_duration_us / 1_000_000
    print(f"  [导出] 共 {len(sequence)} 个片段，{len(materials['videos'])} 个视频素材，{total_sec:.1f} 秒")

    return output_dir

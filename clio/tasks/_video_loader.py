from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clio.config.models import AppConfig


def load_selected_videos(project_dir: Path | None) -> list[Path]:
    """从 project_dir/videos.json 读取选中视频列表。

    Offline / missing paths are kept — videos.json is an allowlist, not a live
    scan. Callers that need existing files must filter with ``Path.is_file()``.
    """
    if project_dir is None:
        return []
    video_file = Path(project_dir) / "videos.json"
    if not video_file.is_file():
        return []
    try:
        data = json.loads(video_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    paths: list[Path] = []
    for item in data:
        if item is None or item == "":
            continue
        paths.append(Path(str(item)))
    return paths


def save_selected_videos(project_dir: Path, videos: list[Path]) -> None:
    """保存选中视频列表到 project_dir/videos.json（原子写入）。"""
    video_file = Path(project_dir) / "videos.json"
    video_file.parent.mkdir(parents=True, exist_ok=True)
    data = [str(p) for p in videos]
    suffix = os.urandom(4).hex()
    tmp = video_file.with_suffix(f".json.tmp.{suffix}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(video_file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def source_videos(config: AppConfig) -> list[Path]:
    """项目原始视频列表：优先 videos.json，缺失时返回空列表。

    调用方应始终通过 AppConfig.project_dir 使用本函数，不再扫描 input_dir。
    """
    return load_selected_videos(getattr(config, "project_dir", None))


def stem_to_path_map(videos: list[Path]) -> dict[str, Path | None]:
    """Build {stem_lower: path}; duplicate stems map to None (ambiguous)."""
    out: dict[str, Path | None] = {}
    for path in videos:
        if not path.suffix:
            continue
        key = path.stem.lower()
        if key in out and out[key] != path:
            out[key] = None
        else:
            out[key] = path
    return out

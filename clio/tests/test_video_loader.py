from __future__ import annotations

import json
from pathlib import Path

from clio.tasks._video_loader import load_selected_videos, save_selected_videos


def test_save_and_load(tmp_path: Path) -> None:
    videos = [Path("D:/GoPro/GH010001.MP4"), Path("E:/phone/video.mp4")]
    save_selected_videos(tmp_path, videos)
    loaded = load_selected_videos(tmp_path)
    assert loaded == videos


def test_load_missing_file(tmp_path: Path) -> None:
    loaded = load_selected_videos(tmp_path)
    assert loaded == []


def test_load_empty_array(tmp_path: Path) -> None:
    (tmp_path / "videos.json").write_text("[]", encoding="utf-8")
    loaded = load_selected_videos(tmp_path)
    assert loaded == []


def test_save_atomicity(tmp_path: Path) -> None:
    v1 = [Path("A.mp4")]
    save_selected_videos(tmp_path, v1)
    content = (tmp_path / "videos.json").read_text(encoding="utf-8")
    assert json.loads(content) == ["A.mp4"]


def test_load_non_list_json_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "videos.json").write_text('{"videos": ["a.mp4"]}', encoding="utf-8")
    assert load_selected_videos(tmp_path) == []


def test_load_skips_null_and_empty_entries(tmp_path: Path) -> None:
    (tmp_path / "videos.json").write_text(
        json.dumps([None, "", "D:/ok.mp4"], ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = load_selected_videos(tmp_path)
    assert loaded == [Path("D:/ok.mp4")]


def test_load_accepts_utf8_bom(tmp_path: Path) -> None:
    (tmp_path / "videos.json").write_text(
        json.dumps(["D:/ok.mp4"], ensure_ascii=False),
        encoding="utf-8-sig",
    )
    assert load_selected_videos(tmp_path) == [Path("D:/ok.mp4")]

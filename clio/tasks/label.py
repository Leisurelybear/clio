"""Label task — burn sequence numbers onto compressed videos."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTS
from clio.config import AppConfig
from clio.log import timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.tasks._helpers import _eta_line, _matches_selected_artifact, _selected_stems
from clio.utils import format_index, resolve_binary, run_ffmpeg


def escape_drawtext_path(path: str | Path) -> str:
    """Escape a filesystem path for use in ffmpeg drawtext fontfile= value."""
    s = Path(path).expanduser().resolve().as_posix()
    # Filter options are colon-separated; drive letters and other colons need \:
    s = s.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    return s


def resolve_drawtext_font() -> Path:
    """Pick a TTF/TTC that freetype can open without Fontconfig.

    Winget/static Windows ffmpeg builds often ship with Fontconfig enabled but
    no default config, so drawtext without fontfile fails (or segfaults).
    """
    candidates: list[Path] = []
    if sys.platform == "win32":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        fonts = windir / "Fonts"
        candidates = [
            fonts / "arial.ttf",
            fonts / "segoeui.ttf",
            fonts / "calibri.ttf",
            fonts / "consola.ttf",
            fonts / "msyh.ttc",
            fonts / "simhei.ttf",
        ]
    elif sys.platform == "darwin":
        candidates = [
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/Library/Fonts/Arial.ttf"),
            Path("/System/Library/Fonts/Helvetica.ttc"),
        ]
    else:
        candidates = [
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
            Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError(
        "找不到可用于 drawtext 的字体文件。Windows 请确认 C:\\Windows\\Fonts\\arial.ttf（或 segoeui.ttf）存在。"
    )


def build_label_drawtext_vf(label: str, font_path: str | Path | None = None) -> str:
    """Build drawtext filter that burns an index watermark with an explicit font."""
    font = Path(font_path) if font_path is not None else resolve_drawtext_font()
    if not font.is_file():
        raise FileNotFoundError(f"字体文件不存在: {font}")
    safe_label = str(label).replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:")
    font_esc = escape_drawtext_path(font)
    return (
        f"drawtext=fontfile='{font_esc}':text='{safe_label}':"
        f"fontsize=36:fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=8:x=20:y=20"
    )


def run_label_videos(
    config: AppConfig,
    tracker: ProgressTracker | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    **kwargs: Any,
) -> None:
    """用 ffmpeg 在压缩视频上烧录序号（便于剪映对照）。"""
    ffmpeg = resolve_binary(config.paths.ffmpeg, "ffmpeg")
    labeled_dir = config.paths.output_dir / "labeled"
    labeled_dir.mkdir(parents=True, exist_ok=True)
    state = ProcessingState(config.paths.output_dir)
    font_path = resolve_drawtext_font()

    json_files = sorted(config.texts_dir.glob("*.json"))
    if files is not None:
        selected = _selected_stems(files)
        json_files = [f for f in json_files if _matches_selected_artifact(f, selected)]
    if tracker:
        tracker.update(phase="label", total=len(json_files), current=0, message=f"烧录序号（{len(json_files)} 个）...")
    with timed(f"run_label_videos（{len(json_files)} 个）"):
        completed = 0
        elapsed_total = 0.0
        for i, json_file in enumerate(json_files, start=1):
            if cancel_event and cancel_event.is_set():
                print("[取消] label 步骤被用户终止")
                break
            data = json.loads(json_file.read_text(encoding="utf-8"))
            raw_idx = data.get("index")
            try:
                idx = (
                    format_index(int(raw_idx), config.naming.index_width) if raw_idx is not None else json_file.stem[:3]
                )
            except (ValueError, TypeError):
                idx = json_file.stem[:3]
            compressed = None
            for f in config.compressed_dir.glob(f"{idx}_*"):
                if f.suffix.lower() not in VIDEO_EXTS:
                    continue
                compressed = f
                break
            orig_stem = Path(data.get("source_file") or data.get("compressed_file") or json_file.stem).stem
            if not compressed or not compressed.exists():
                print(f"[跳过] 找不到压缩文件: {idx}")
                state.mark(orig_stem, "label", "skipped")
                if tracker:
                    tracker.next(message=f"跳过 {idx}（无压缩文件）")
                continue

            out = labeled_dir / f"{json_file.stem}_labeled.mp4"
            if not overwrite and config.analyze.skip_existing and out.exists():
                print(f"[跳过标注] {out.name} (已存在)")
                state.mark(orig_stem, "label", "skipped")
                if tracker:
                    tracker.next(message=f"跳过 {out.name}")
                continue

            print(_eta_line("标注", i, len(json_files), json_file.stem, completed, elapsed_total))
            if tracker:
                tracker.next(message=f"标注 {json_file.stem}")
            t0 = time.monotonic()
            label = idx.replace("'", "")
            vf = build_label_drawtext_vf(label, font_path)
            try:
                run_ffmpeg(["-i", str(compressed), "-vf", vf, "-an", "-y", str(out)], ffmpeg, cancel_event=cancel_event)
                state.mark(orig_stem, "label", "done")
            except Exception:
                if out.exists():
                    out.unlink(missing_ok=True)
                state.mark(orig_stem, "label", "error")
                raise
            elapsed_total += time.monotonic() - t0
            completed += 1
            if tracker:
                tracker.log(f"标号 {json_file.stem} ✓")
            print(f"  -> {out.name}")

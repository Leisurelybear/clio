"""Video metadata sidecar file (.vmeta / .vindex) read/write module."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import cast

from clio.utils import JsonValue, write_json_atomic

VMETA_EXT = ".vmeta"
VINDEX_EXT = ".vindex"
VMETA_VERSION = 1


@dataclass
class SplitInfo:
    original_stem: str
    segment_index: int
    total_segments: int
    offset_sec: float
    segment_duration_sec: float


@dataclass
class VideoMeta:
    source_path: str
    target_path: str
    is_original: bool
    is_split_segment: bool
    split_info: SplitInfo | None

    source_modifyTime: int
    source_size: int
    target_modifyTime: int
    target_size: int
    source_duration_sec: float
    target_duration_sec: float

    compress_settings: dict = field(default_factory=dict)
    verify: str = ""
    version: int = VMETA_VERSION

    @staticmethod
    def build(
        source: Path,
        target: Path,
        source_duration: float,
        target_duration: float,
        compress_settings: dict | None = None,
        split_info: SplitInfo | None = None,
        is_original: bool = False,
    ) -> VideoMeta:
        return VideoMeta(
            source_path=str(source.resolve()),
            target_path=target.name,
            is_original=is_original,
            is_split_segment=split_info is not None,
            split_info=split_info,
            source_modifyTime=int(source.stat().st_mtime),
            source_size=source.stat().st_size,
            target_modifyTime=int(target.stat().st_mtime),
            target_size=target.stat().st_size,
            source_duration_sec=round(source_duration, 3),
            target_duration_sec=round(target_duration, 3),
            compress_settings=compress_settings or {},
            verify=_quick_hash(target),
        )

    def write(self, compressed_path: Path) -> Path:
        meta_path = compressed_path.with_suffix(VMETA_EXT)
        write_json_atomic(meta_path, _meta_to_dict(self))
        return meta_path

    @staticmethod
    def read(compressed_path: Path) -> VideoMeta | None:
        meta_path = compressed_path.with_suffix(VMETA_EXT)
        if not meta_path.is_file():
            return None
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            data = raw.get("data", raw)
            si = data.get("split_info")
            return VideoMeta(
                source_path=data["source_path"],
                target_path=data["target_path"],
                is_original=data.get("is_original", False),
                is_split_segment=data.get("is_split_segment", False),
                split_info=SplitInfo(**si) if si else None,
                source_modifyTime=raw["source_modifyTime"],
                source_size=raw["source_size"],
                target_modifyTime=raw["target_modifyTime"],
                target_size=raw["target_size"],
                source_duration_sec=raw.get("source_duration_sec", 0.0),
                target_duration_sec=raw.get("target_duration_sec", 0.0),
                compress_settings=raw.get("compress_settings", {}),
                verify=raw.get("verify", ""),
                version=raw.get("version", VMETA_VERSION),
            )
        except Exception:
            return None

    def source_path_obj(self) -> Path:
        return Path(self.source_path)

    def is_stale(self, source: Path, target: Path | None = None) -> bool:
        try:
            st = source.stat()
            if int(st.st_mtime) != self.source_modifyTime or st.st_size != self.source_size:
                return True
            if target is None:
                return False
            target_st = target.stat()
            if int(target_st.st_mtime) != self.target_modifyTime or target_st.st_size != self.target_size:
                return True
            return bool(self.verify and _quick_hash(target) != self.verify)
        except OSError:
            return True


@dataclass
class SegmentEntry:
    index: str
    filename: str
    offset_sec: float
    duration_sec: float
    segment_number: int
    total_segments: int


@dataclass
class VideoIndex:
    source_stem: str
    source_path: str
    source_size: int
    source_modifyTime: int
    source_duration_sec: float
    is_split: bool
    segments: list[SegmentEntry]
    version: int = VMETA_VERSION

    @staticmethod
    def build(
        source: Path,
        source_duration: float,
        segments: list[SegmentEntry],
    ) -> VideoIndex:
        return VideoIndex(
            source_stem=source.stem,
            source_path=str(source.resolve()),
            source_size=source.stat().st_size,
            source_modifyTime=int(source.stat().st_mtime),
            source_duration_sec=round(source_duration, 3),
            is_split=len(segments) > 1,
            segments=segments,
        )

    def write(self, compressed_dir: Path) -> Path:
        index_path = compressed_dir / f"{self.source_stem}{VINDEX_EXT}"
        data = {
            "version": self.version,
            "source_stem": self.source_stem,
            "source_path": self.source_path,
            "source_size": self.source_size,
            "source_modifyTime": self.source_modifyTime,
            "source_duration_sec": self.source_duration_sec,
            "is_split": self.is_split,
            "segments": [asdict(s) for s in self.segments],
        }
        write_json_atomic(index_path, cast(JsonValue, data))
        return index_path

    @staticmethod
    def read(source_stem: str, compressed_dir: Path) -> VideoIndex | None:
        index_path = compressed_dir / f"{source_stem}{VINDEX_EXT}"
        if not index_path.is_file():
            return None
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            segs = [SegmentEntry(**s) for s in raw.get("segments", [])]
            return VideoIndex(
                source_stem=raw["source_stem"],
                source_path=raw["source_path"],
                source_size=raw["source_size"],
                source_modifyTime=raw["source_modifyTime"],
                source_duration_sec=raw.get("source_duration_sec", 0.0),
                is_split=raw.get("is_split", len(segs) > 1),
                segments=segs,
                version=raw.get("version", VMETA_VERSION),
            )
        except Exception:
            return None

    def compressed_paths(self, compressed_dir: Path) -> list[Path]:
        return [
            compressed_dir / s.filename
            for s in sorted(self.segments, key=lambda x: x.segment_number)
            if (compressed_dir / s.filename).is_file()
        ]

    def declared_paths(self, compressed_dir: Path) -> list[Path]:
        """All declared segment paths, whether or not they exist.

        Unlike compressed_paths() this never filters missing entries; used by
        verification so a removed segment is reported instead of silently
        passing.
        """
        root = compressed_dir.resolve()
        result = []
        for s in sorted(self.segments, key=lambda x: x.segment_number):
            p = (compressed_dir / s.filename).resolve()
            try:
                p.relative_to(root)
            except ValueError:
                continue
            result.append(p)
        return result

    def is_stale(self, source: Path) -> bool:
        try:
            st = source.stat()
            return int(st.st_mtime) != self.source_modifyTime or st.st_size != self.source_size
        except OSError:
            return True


def _quick_hash(path: Path, chunk: int = 1024 * 1024) -> str:
    try:
        h = hashlib.sha256()
        size = path.stat().st_size
        mtime_ns = path.stat().st_mtime_ns
        h.update(f"{size}:{mtime_ns}".encode())
        with path.open("rb") as f:
            h.update(f.read(chunk))
            if size > chunk * 2:
                f.seek(size // 2)
                h.update(f.read(chunk // 4))
                f.seek(-chunk, 2)
                h.update(f.read(chunk))
        return h.hexdigest()
    except OSError:
        return ""


def _meta_to_dict(meta: VideoMeta) -> dict:
    return {
        "version": meta.version,
        "data": {
            "source_path": meta.source_path,
            "target_path": meta.target_path,
            "is_original": meta.is_original,
            "is_split_segment": meta.is_split_segment,
            "split_info": asdict(meta.split_info) if meta.split_info else None,
        },
        "source_modifyTime": meta.source_modifyTime,
        "source_size": meta.source_size,
        "target_modifyTime": meta.target_modifyTime,
        "target_size": meta.target_size,
        "source_duration_sec": meta.source_duration_sec,
        "target_duration_sec": meta.target_duration_sec,
        "compress_settings": meta.compress_settings,
        "verify": meta.verify,
    }

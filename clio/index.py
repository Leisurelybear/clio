"""Artifact index — unified lookup for all project artifacts.

Build once per project, query many times. Maps between compressed videos,
texts, scripts, transcripts, and covers by compressed_stem, original_stem,
or index.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from clio.identity import MediaIdentity, _extract_original_stem, load_identity
from clio.plan_readiness import expand_index_keys
from clio.vmeta import VideoMeta

# ── Data types ──────────────────────────────────────────────────────────


@dataclass
class VideoEntry:
    path: Path
    stem: str
    index: str
    identity: MediaIdentity | None


@dataclass
class TextEntry:
    path: Path
    stem: str
    title: str
    identity: MediaIdentity | None


@dataclass
class ScriptEntry:
    path: Path
    stem: str


@dataclass
class TranscriptEntry:
    path: Path
    stem: str
    identity: MediaIdentity | None


@dataclass
class CoverEntry:
    path: Path
    stem: str


@dataclass
class ArtifactGroup:
    """All artifacts associated with a single compressed video."""

    compressed: VideoEntry
    texts: list[TextEntry]
    script: ScriptEntry | None
    transcript: TranscriptEntry | None
    cover: CoverEntry | None


# ── Extracting identity helpers ─────────────────────────────────────────


def _stem_to_index(stem: str) -> str | None:
    """Extract zero-padded index prefix from a compressed file stem.
    '001_GL010683' -> '001', 'GL010683' -> None.
    """
    m = re.match(r"^(\d+)_", stem)
    return m.group(1) if m else None


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    """Read and parse a JSON file, returning None on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _stems_for_index(index: str, stems: list[str]) -> list[str]:
    """Resolve all stems matching a complete numeric index token."""
    wanted = expand_index_keys(index)
    return [stem for stem in stems if expand_index_keys(_stem_to_index(stem)) & wanted]


# ── Index ────────────────────────────────────────────────────────────────


class ArtifactIndex:
    """Scan artifact directories once, then provide lookup queries.

    Usage::
        index = ArtifactIndex(output_dir=..., project_dir=..., ...)
        index.build()
        group = index.lookup(compressed_stem="001_GL010683")
    """

    def __init__(
        self,
        output_dir: Path,
        project_dir: Path | None = None,
        compressed_dir: Path | None = None,
        texts_dir: Path | None = None,
        scripts_dir: Path | None = None,
        transcripts_dir: Path | None = None,
        covers_dir: Path | None = None,
        *,
        input_dir: Path | None = None,
    ):
        self._output_dir = output_dir
        # project_dir kept for API clarity; directory scans use output artifacts only
        self._project_dir = project_dir if project_dir is not None else input_dir
        self._compressed_dir = compressed_dir or output_dir / "compressed"
        self._texts_dir = texts_dir or output_dir / "texts"
        self._scripts_dir = scripts_dir or output_dir / "scripts"
        self._transcripts_dir = transcripts_dir or output_dir / "transcripts"
        self._covers_dir = covers_dir or output_dir / "covers"

        # Built maps
        self._by_compressed_stem: dict[str, ArtifactGroup] = {}
        self._by_original_stem: dict[str, list[ArtifactGroup]] = {}
        self._groups: list[ArtifactGroup] = []
        self._ambiguous_artifacts: list[Path] = []

    # ── Public API ──────────────────────────────────────────────────────

    def build(self) -> None:
        """Scan all artifact directories and build the index."""
        self._by_compressed_stem.clear()
        self._by_original_stem.clear()
        self._groups = []
        self._ambiguous_artifacts = []
        self._scan_compressed()
        self._scan_texts()
        self._scan_scripts()
        self._scan_transcripts()
        self._scan_covers()
        self._build_original_stem_index()
        self._groups = list(self._by_compressed_stem.values())

    def all_groups(self) -> list[ArtifactGroup]:
        """Return all artifact groups in the project."""
        return self._groups

    def ambiguous_artifacts(self) -> tuple[Path, ...]:
        """Artifacts skipped because a legacy index maps to multiple videos."""
        return tuple(self._ambiguous_artifacts)

    def lookup(
        self,
        *,
        compressed_stem: str | None = None,
        original_stem: str | None = None,
    ) -> ArtifactGroup | list[ArtifactGroup] | None:
        """Look up artifacts by compressed stem or original stem.

        Returns a single ArtifactGroup for compressed_stem lookup,
        or a list of groups for original_stem lookup (one per segment).
        """
        if compressed_stem is not None:
            return self._by_compressed_stem.get(compressed_stem.lower())
        if original_stem is not None:
            return self._by_original_stem.get(original_stem.lower())
        return None

    # ── Scan helpers ────────────────────────────────────────────────────

    def _get_or_create_group(self, compressed_stem: str) -> ArtifactGroup:
        key = compressed_stem.lower()
        if key not in self._by_compressed_stem:
            self._by_compressed_stem[key] = ArtifactGroup(
                compressed=VideoEntry(path=Path(), stem="", index="", identity=None),
                texts=[],
                script=None,
                transcript=None,
                cover=None,
            )
        return self._by_compressed_stem[key]

    def _scan_compressed(self) -> None:
        if not self._compressed_dir.is_dir():
            return
        VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".mts", ".m2ts", ".m4v", ".webm"}
        for p in sorted(self._compressed_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
                continue
            stem = p.stem
            index = _stem_to_index(stem) or ""
            meta = VideoMeta.read(p)
            identity: MediaIdentity | None = None
            if meta is not None:
                si = meta.split_info
                identity = MediaIdentity(
                    original_stem=si.original_stem if si else "",
                    original_path=meta.source_path,
                    compressed_stem=stem,
                    compressed_path=str(p.resolve()),
                    index=index,
                    segment_index=si.segment_index if si else None,
                    segment_offset_sec=si.offset_sec if si else 0.0,
                    segment_duration_sec=si.segment_duration_sec if si else None,
                )
            group = self._get_or_create_group(stem)
            group.compressed = VideoEntry(path=p, stem=stem, index=index, identity=identity)

    def _scan_texts(self) -> None:
        if not self._texts_dir.is_dir():
            return
        for p in sorted(self._texts_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() != ".json":
                continue
            stem = p.stem
            data = _read_json_safe(p)
            if data is None:
                continue
            identity = load_identity(data)
            # Skip voiceover files (handled by _scan_scripts)
            if stem.endswith("_voiceover"):
                continue
            title = data.get("title", stem)
            # Determine compressed stem from identity first, then compressed_file, then index
            cs = identity.compressed_stem if identity else ""
            if not cs:
                cs = Path(data.get("compressed_file", "")).stem
            if not cs:
                idx = _stem_to_index(stem) or ""
                if idx:
                    matches = _stems_for_index(idx, list(self._by_compressed_stem))
                    if len(matches) == 1:
                        cs = matches[0]
                    elif len(matches) > 1:
                        self._ambiguous_artifacts.append(p)
            if cs:
                group = self._get_or_create_group(cs)
                group.texts.append(TextEntry(path=p, stem=stem, title=title, identity=identity))

    def _scan_scripts(self) -> None:
        if not self._scripts_dir.is_dir():
            return
        for p in sorted(self._scripts_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() != ".json":
                continue
            stem = p.stem
            # Derive text stem by stripping _voiceover
            if stem.endswith("_voiceover"):
                text_stem = stem[: -len("_voiceover")]
            else:
                text_stem = stem
            group_key = None
            # Find the group that has a text with this stem
            for key, g in self._by_compressed_stem.items():
                for t in g.texts:
                    if t.stem == text_stem:
                        group_key = key
                        break
                if group_key:
                    break
            if group_key:
                self._by_compressed_stem[group_key].script = ScriptEntry(path=p, stem=stem)
            else:
                # Create a new group for orphan scripts (fallback)
                pass

    def _scan_transcripts(self) -> None:
        if not self._transcripts_dir.is_dir():
            return
        for p in sorted(self._transcripts_dir.iterdir()):
            if not p.is_file() or p.suffix.lower() != ".json":
                continue
            stem = p.stem
            data = _read_json_safe(p)
            if data is None:
                continue
            identity = load_identity(data)
            # Transcript filename: {compressed_stem}_transcript.json
            cs = identity.compressed_stem if identity else ""
            if not cs:
                if stem.endswith("_transcript"):
                    cs = stem[: -len("_transcript")]
            if cs:
                group = self._get_or_create_group(cs)
                group.transcript = TranscriptEntry(path=p, stem=stem, identity=identity)

    def _scan_covers(self) -> None:
        if not self._covers_dir.is_dir():
            return
        for p in sorted(self._covers_dir.iterdir()):
            if not p.is_file():
                continue
            stem = p.stem
            # Cover filename: {text_stem}.jpg — find the text with matching stem
            for key, g in self._by_compressed_stem.items():
                for t in g.texts:
                    if t.stem == stem:
                        g.cover = CoverEntry(path=p, stem=stem)
                        break

    def _build_original_stem_index(self) -> None:
        """Build the original_stem -> groups map from compressed identity info."""
        self._by_original_stem.clear()
        for group in self._by_compressed_stem.values():
            identity = group.compressed.identity
            if identity and identity.original_stem:
                key = identity.original_stem.lower()
                self._by_original_stem.setdefault(key, []).append(group)
            else:
                # Fallback: derive original stem from compressed stem
                stem = group.compressed.stem
                orig = _extract_original_stem(stem)
                if orig:
                    self._by_original_stem.setdefault(orig.lower(), []).append(group)

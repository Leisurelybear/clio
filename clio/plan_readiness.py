"""Export/cut readiness checks for Plan (error vs warning tiers)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTS
from clio.cut import parse_time_range
from clio.identity import load_identity
from clio.plan_model import Plan, PlanIssue
from clio.utils import format_index


@dataclass
class ReadinessResult:
    errors: list[PlanIssue] = field(default_factory=list)
    warnings: list[PlanIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


def expand_index_keys(raw: Any, *, index_width: int = 3) -> set[str]:
    """All membership forms for a plan/media index (raw, int, zero-padded).

    Plan sequence uses format_index → "001"; texts often store index as int → "1".
    Exact string equality between those forms produced false index_missing errors.
    """
    if raw is None:
        return set()
    s = str(raw).strip()
    if not s:
        return set()
    keys = {s}
    if s.isdigit():
        n = int(s)
        keys.add(str(n))
        width = max(1, int(index_width or 3))
        keys.add(format_index(n, width))
        # Also admit common pad widths so "01" matches "001" / "1"
        for w in (2, 3, 4):
            keys.add(format_index(n, w))
    return keys


def index_in_set(idx: str, candidates: set[str] | None, *, index_width: int = 3) -> bool:
    if not candidates:
        return False
    return bool(expand_index_keys(idx, index_width=index_width) & candidates)


def _index_width_from_cfg(cfg: Any) -> int:
    naming = getattr(cfg, "naming", None)
    width = getattr(naming, "index_width", 3) if naming is not None else 3
    try:
        return max(1, int(width or 3))
    except (TypeError, ValueError):
        return 3


def check_plan_export_readiness(
    plan: Plan,
    *,
    known_indices: set[str] | None = None,
    offline_indices: set[str] | None = None,
    source: str = "compressed",
    index_width: int = 3,
) -> ReadinessResult:
    del source  # reserved for original-vs-compressed nuance in callers
    result = ReadinessResult()
    if not plan.sequence:
        result.errors.append(
            PlanIssue(level="error", code="sequence_empty", message="规划 sequence 为空，无法导出/裁剪")
        )
        return result

    try:
        tes = float(plan.total_estimated_sec)
    except (TypeError, ValueError):
        tes = 180
    if tes < 30:
        result.warnings.append(PlanIssue(level="warning", code="duration_short", message=f"预估总时长过短（{tes} 秒）"))
    if tes > 1800:
        result.warnings.append(PlanIssue(level="warning", code="duration_long", message=f"预估总时长过长（{tes} 秒）"))

    # Distinguish "not scanned" (None) from "scanned and found nothing" (empty
    # set). An empty discovery means every referenced index is missing media; a
    # None discovery means we simply don't know yet and must not guess.
    known: set[str] | None
    if known_indices is None:
        known = None
    else:
        known = set()
        for x in known_indices:
            known.update(expand_index_keys(x, index_width=index_width))

    offline: set[str] = set()
    if offline_indices:
        for x in offline_indices:
            offline.update(expand_index_keys(x, index_width=index_width))

    for i, seg in enumerate(plan.sequence):
        idx = (seg.index or "").strip()
        if not idx:
            result.errors.append(
                PlanIssue(level="error", code="index_empty", message=f"第 {i + 1} 段缺少 index", segment_index=i)
            )
        elif known is not None and not index_in_set(idx, known, index_width=index_width):
            result.errors.append(
                PlanIssue(
                    level="error",
                    code="index_missing",
                    message=f"第 {i + 1} 段视频 [{idx}] 在项目中不存在",
                    segment_index=i,
                )
            )
        elif index_in_set(idx, offline, index_width=index_width):
            result.warnings.append(
                PlanIssue(
                    level="warning",
                    code="video_offline",
                    message=f"第 {i + 1} 段视频 [{idx}] 当前离线",
                    segment_index=i,
                )
            )

        tl = (seg.use_timeline or "").strip()
        if not tl:
            result.warnings.append(
                PlanIssue(
                    level="warning",
                    code="timeline_empty",
                    message=f"第 {i + 1} 段未填写 use_timeline",
                    segment_index=i,
                )
            )
        else:
            try:
                parse_time_range(tl)
            except ValueError as e:
                result.errors.append(
                    PlanIssue(
                        level="error",
                        code="timeline_invalid",
                        message=f"第 {i + 1} 段时间轴无效: {e}",
                        segment_index=i,
                    )
                )

        if not (seg.title or "").strip():
            result.warnings.append(
                PlanIssue(level="warning", code="title_empty", message=f"第 {i + 1} 段标题为空", segment_index=i)
            )
        if not (seg.reason or "").strip():
            result.warnings.append(
                PlanIssue(level="warning", code="reason_empty", message=f"第 {i + 1} 段理由为空", segment_index=i)
            )

    return result


def _register_stem_indices(
    stem_to_indices: dict[str, set[str]],
    stem: str | None,
    idx_keys: set[str],
) -> None:
    if not stem or not idx_keys:
        return
    key = stem.strip().lower()
    if not key:
        return
    stem_to_indices.setdefault(key, set()).update(idx_keys)


def collect_project_indices(cfg: Any) -> tuple[set[str], set[str]]:
    """Build (known_indices, offline_indices) from compressed + texts + videos.json.

    known/offline sets include expanded index key forms ("1", "001", …).
    offline_indices are plan indexes whose original media is listed in videos.json
    but currently missing on disk (mapped via texts media_identity / source_file
    or compressed filename stems).
    """
    width = _index_width_from_cfg(cfg)
    known: set[str] = set()
    offline: set[str] = set()
    stem_to_indices: dict[str, set[str]] = {}

    comp = getattr(cfg, "compressed_dir", None)
    if isinstance(comp, Path) and comp.is_dir():
        try:
            for p in comp.iterdir():
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                    stem = p.stem
                    idx = stem.split("_", 1)[0]
                    if idx:
                        keys = expand_index_keys(idx, index_width=width)
                        known.update(keys)
                        # Map original-like suffix after index prefix: 001_GL010683 → GL010683
                        rest = stem.split("_", 1)[1] if "_" in stem else ""
                        if rest:
                            # Drop legacy _segNN tail for stem matching
                            base = rest
                            for marker in ("_seg", "_part", "_pt", "_chunk"):
                                pos = base.lower().rfind(marker)
                                if pos > 0 and base[pos + len(marker) :].isdigit():
                                    base = base[:pos]
                                    break
                            _register_stem_indices(stem_to_indices, base, keys)
        except OSError:
            pass

    texts = getattr(cfg, "texts_dir", None)
    if isinstance(texts, Path) and texts.is_dir():
        try:
            for jf in texts.glob("*.json"):
                try:
                    data = json.loads(jf.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                raw_idx = data.get("index")
                if raw_idx is None:
                    continue
                keys = expand_index_keys(raw_idx, index_width=width)
                known.update(keys)
                identity = load_identity(data)
                if identity is not None:
                    _register_stem_indices(stem_to_indices, identity.original_stem, keys)
                    _register_stem_indices(stem_to_indices, identity.compressed_stem, keys)
                    if identity.index:
                        known.update(expand_index_keys(identity.index, index_width=width))
                source = data.get("source_file") or data.get("compressed_file")
                if source:
                    _register_stem_indices(stem_to_indices, Path(str(source)).stem, keys)
        except OSError:
            pass

    project_dir = getattr(cfg, "project_dir", None)
    if project_dir is not None:
        try:
            from clio.tasks._video_loader import load_selected_videos

            for p in load_selected_videos(project_dir):
                if p.is_file():
                    continue
                # Offline: map path stem → plan indices
                for stem_candidate in (p.stem, Path(str(p)).stem):
                    mapped = stem_to_indices.get(stem_candidate.lower())
                    if mapped:
                        offline.update(mapped)
                        break
        except Exception:
            pass

    return known, offline


def readiness_block_payload(result: ReadinessResult, *, force: bool) -> dict[str, Any] | None:
    """If export/cut should be blocked, return JSON body (caller sends 400). Else None."""
    if result.errors:
        return {
            "ok": False,
            "error": result.errors[0].message,
            "issues": result.to_dict(),
        }
    if result.warnings and not force:
        return {
            "ok": False,
            "error": "规划存在警告，确认后请传 force: true",
            "issues": result.to_dict(),
            "needs_force": True,
        }
    return None

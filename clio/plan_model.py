"""Plan domain model — load/dump, structural mutate, save validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clio.cut import parse_time_range
from clio.i18n import t

_SEGMENT_KNOWN = frozenset({"index", "title", "reason", "use_timeline", "voiceover_hint", "subtitle"})
_PLAN_KNOWN = frozenset(
    {
        "day_title",
        "theme",
        "total_estimated_sec",
        "opening_tip",
        "ending_tip",
        "sequence",
        "_confidence",
        "confidence",
        "_schema_version",
    }
)


@dataclass
class PlanIssue:
    level: str  # "error" | "warning"
    code: str
    message: str
    segment_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"level": self.level, "code": self.code, "message": self.message}
        if self.segment_index is not None:
            d["segment_index"] = self.segment_index
        return d


@dataclass
class PlanSegment:
    index: str
    title: str = ""
    reason: str = ""
    use_timeline: str = ""
    voiceover_hint: str = ""
    subtitle: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanSegment:
        if not isinstance(data, dict):
            data = {}
        extras = {k: v for k, v in data.items() if k not in _SEGMENT_KNOWN}
        raw_idx = data.get("index", "")
        if raw_idx is None:
            index = ""
        else:
            index = str(raw_idx).strip()
        return cls(
            index=index,
            title=str(data.get("title") or ""),
            reason=str(data.get("reason") or ""),
            use_timeline=str(data.get("use_timeline") or "").strip(),
            voiceover_hint=str(data.get("voiceover_hint") or ""),
            subtitle=str(data.get("subtitle") or ""),
            extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        d = {
            "index": self.index,
            "title": self.title,
            "reason": self.reason,
            "use_timeline": self.use_timeline,
            "voiceover_hint": self.voiceover_hint,
            "subtitle": self.subtitle,
        }
        d.update(self.extras)
        return d


@dataclass
class Plan:
    day_title: str = ""
    theme: str = ""
    total_estimated_sec: int | float = 180
    opening_tip: str = ""
    ending_tip: str = ""
    sequence: list[PlanSegment] = field(default_factory=list)
    confidence: float | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Plan:
        if not isinstance(data, dict):
            data = {}
        seq_raw = data.get("sequence", [])
        if not isinstance(seq_raw, list):
            seq_raw = []
        sequence = [PlanSegment.from_dict(s if isinstance(s, dict) else {}) for s in seq_raw]
        conf = data.get("_confidence", data.get("confidence"))
        try:
            confidence = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            confidence = None
        extras = {k: v for k, v in data.items() if k not in _PLAN_KNOWN}
        tes = data.get("total_estimated_sec", 180)
        try:
            total_estimated_sec: int | float = float(tes) if tes is not None else 180
        except (TypeError, ValueError):
            total_estimated_sec = 180
        return cls(
            day_title=str(data.get("day_title") or ""),
            theme=str(data.get("theme") or ""),
            total_estimated_sec=total_estimated_sec,
            opening_tip=str(data.get("opening_tip") or ""),
            ending_tip=str(data.get("ending_tip") or ""),
            sequence=sequence,
            confidence=confidence,
            extras=extras,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "day_title": self.day_title,
            "theme": self.theme,
            "total_estimated_sec": self.total_estimated_sec,
            "opening_tip": self.opening_tip,
            "ending_tip": self.ending_tip,
            "sequence": [s.to_dict() for s in self.sequence],
        }
        if self.confidence is not None:
            d["_confidence"] = self.confidence
        d.update(self.extras)
        return d

    def reorder(self, from_i: int, to_i: int) -> None:
        n = len(self.sequence)
        if not (0 <= from_i < n and 0 <= to_i < n):
            raise IndexError(f"reorder out of range: {from_i}->{to_i} (n={n})")
        item = self.sequence.pop(from_i)
        self.sequence.insert(to_i, item)

    def remove_at(self, i: int) -> PlanSegment:
        if not (0 <= i < len(self.sequence)):
            raise IndexError(f"remove_at out of range: {i}")
        return self.sequence.pop(i)

    def validate_for_save(self) -> list[PlanIssue]:
        issues: list[PlanIssue] = []
        for i, seg in enumerate(self.sequence):
            if not (seg.index or "").strip():
                issues.append(
                    PlanIssue(
                        level="error",
                        code="index_empty",
                        message=t("plan.validate.index_empty", {"index": i + 1}),
                        segment_index=i,
                    )
                )
            tl = (seg.use_timeline or "").strip()
            if tl:
                try:
                    parse_time_range(tl)
                except ValueError as e:
                    issues.append(
                        PlanIssue(
                            level="error",
                            code="timeline_invalid",
                            message=t("plan.validate.timeline_invalid", {"index": i + 1, "detail": e}),
                            segment_index=i,
                        )
                    )
        return issues

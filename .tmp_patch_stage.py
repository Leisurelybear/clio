import io, sys
from pathlib import Path

path = Path("clio/ui/services/run_preview.py")
text = path.read_text(encoding="utf-8")

old_block_start = "_RUN_PREVIEW_STAGES = (\n"
idx = text.index(old_block_start)
end_marker = "\n\ndef build_run_preview(\n"
end = text.index(end_marker)

new_block = '''_RUN_PREVIEW_STAGES = (
    ("source_prep", ("compress", "transcribe")),
    ("ai_analysis", ("analyze",)),
    ("plan_alignment", ("consistency", "voiceover", "plan")),
    ("voiceover_delivery", ("plan_voiceover", "label")),
)

# Runtime order. Preview rows follow what the pipeline will actually execute so
# cascade review cannot report a downstream artifact as safely reusable.
PIPELINE_PREVIEW_ORDER = (
    "compress",
    "transcribe",
    "analyze",
    "consistency",
    "voiceover",
    "plan",
    "plan_voiceover",
    "label",
)

# Upstream step -> steps whose cached artifacts its rerun can invalidate.
_PIPELINE_CASCADE_EDGES = {
    "compress": ("analyze", "transcribe"),
    "transcribe": ("analyze",),
    "analyze": ("consistency", "voiceover", "plan"),
    "consistency": ("plan",),
    "voiceover": ("plan",),
    "plan": ("plan_voiceover",),
}


def _ordered_steps(steps: Iterable[str]) -> list[str]:
    """Return unique step names in canonical pipeline order; unknown steps last."""
    unique: list[str] = []
    for step in steps:
        if step not in unique:
            unique.append(step)
    in_order = [step for step in PIPELINE_PREVIEW_ORDER if step in unique]
    return in_order + [step for step in unique if step not in PIPELINE_PREVIEW_ORDER]


def _cascade_sources(config: AppConfig, *, use_transcripts: bool) -> dict[str, tuple[str, ...]]:
    """Reverse the cascade graph, dropping edges that do not apply to this run.

    Transcript audio comes from the compressed video only while compression keeps
    audio; with ``remove_audio`` transcription reads the original footage, so a
    compress rerun cannot invalidate it.
    """
    sources: dict[str, list[str]] = {}
    for upstream, targets in _PIPELINE_CASCADE_EDGES.items():
        for target in targets:
            if upstream == "compress" and target == "transcribe" and config.compress.remove_audio:
                continue
            if upstream == "transcribe" and not use_transcripts:
                continue
            sources.setdefault(target, []).append(upstream)
    return {target: tuple(items) for target, items in sources.items()}


def _apply_run_cascade(
    config: AppConfig,
    *,
    use_transcripts: bool,
    selected_steps: list[str],
    preview_steps: list[dict[str, Any]],
) -> set[str]:
    """Mark reusable artifacts that same-run upstream work may invalidate.

    ``will_run`` stays the confirmed minimum. A cascaded row keeps its reusable
    count in ``will_skip`` as a maximum, so ``will_run_max``/``will_skip_min``
    describe the real range instead of pretending the cache is final. Only a
    selected step that may actually run propagates the risk.
    """
    rows_by_name = {str(row.get("name")): row for row in preview_steps}
    selected = set(selected_steps)
    sources = _cascade_sources(config, use_transcripts=use_transcripts)
    may_run: set[str] = set()
    for name in PIPELINE_PREVIEW_ORDER:
        row = rows_by_name.get(name)
        if row is None:
            continue
        will_run = int(row.get("will_run") or 0)
        will_skip = int(row.get("will_skip") or 0)
        upstream = tuple(src for src in sources.get(name, ()) if src in selected and src in may_run)
        if upstream and will_skip > 0 and not row.get("disabled") and int(row.get("total") or 0) > 0:
            row["cascade_from"] = list(upstream)
            row["cascade_assumed"] = will_skip
            row["will_run_max"] = will_run + will_skip
            row["will_skip_min"] = 0
            row["estimate_is_minimum"] = True
        if will_run > 0 or row.get("cascade_assumed"):
            may_run.add(name)
    return may_run


def _defer_plan_voiceover(
    preview_steps: list[dict[str, Any]],
    *,
    selected_steps: list[str],
    day_labels: list[str],
) -> None:
    """Treat a missing plan as a wait when planning runs in the same request.

    Planned voiceover cannot read its input yet, but the same run produces it, so
    reporting a final blocker would be wrong. Volume stays a maximum because the
    plan sequence only exists after planning finishes.
    """
    if "plan" not in selected_steps or "plan_voiceover" not in selected_steps:
        return
    rows_by_name = {str(row.get("name")): row for row in preview_steps}
    plan_row = rows_by_name.get("plan")
    voiceover_row = rows_by_name.get("plan_voiceover")
    if plan_row is None or voiceover_row is None or voiceover_row.get("disabled"):
        return
    plan_may_run = int(plan_row.get("will_run") or 0) > 0 or bool(plan_row.get("cascade_assumed"))
    if not plan_may_run:
        return
    total = len(day_labels)
    if int(voiceover_row.get("total") or 0) == 0 and voiceover_row.get("warnings") and total > 0:
        voiceover_row.update(
            {
                "total": total,
                "will_run": 0,
                "will_skip": 0,
                "will_run_max": total,
                "will_skip_min": 0,
                "warnings": [],
                "deferred": True,
                "waiting_on": ["plan"],
                "estimate_is_minimum": True,
            }
        )
    elif int(voiceover_row.get("will_run") or 0) > 0:
        # Per-segment calls follow the new plan's sequence length.
        voiceover_row["estimate_is_minimum"] = True


def _build_stage_summary(selected_steps: list[str], preview_steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Project step previews into the four user-facing stages.

    The detailed step rows remain in the response for custom execution and
    compatibility. This compact projection gives the guided UI one authoritative
    status/next-action contract without making it repeat cache rules locally.
    """
    rows_by_name = {str(row.get("name")): row for row in preview_steps}
    selected = set(selected_steps)
    stages: list[dict[str, Any]] = []
    blockers: list[str] = []
    for key, stage_steps in _RUN_PREVIEW_STAGES:
        steps = [step for step in stage_steps if step in selected]
        rows = [rows_by_name[step] for step in steps if step in rows_by_name]
        required_rows = [row for row in rows if not row.get("disabled")]
        deferred_rows = [row for row in required_rows if row.get("deferred")]
        total = sum(int(row.get("total") or 0) for row in rows)
        will_run = sum(int(row.get("will_run") or 0) for row in rows)
        will_skip = sum(int(row.get("will_skip") or 0) for row in rows)
        cascade_assumed = sum(int(row.get("cascade_assumed") or 0) for row in rows)
        warnings = [str(item) for row in rows for item in (row.get("warnings") or [])]
        # Optional steps that are disabled for this run must not block the stage:
        # they carry an explanatory warning but no work or artifact requirement.
        required_warnings = [str(item) for row in required_rows for item in (row.get("warnings") or [])]
        waiting_on = sorted({str(item) for row in deferred_rows for item in (row.get("waiting_on") or [])})
        known = len(rows) == len(steps)
        reusable = (
            bool(steps)
            and known
            and not cascade_assumed
            and all(
                int(row.get("total") or 0) > 0
                and int(row.get("will_run") or 0) == 0
                and int(row.get("will_skip") or 0) == int(row.get("total") or 0)
                and not row.get("warnings")
                and not row.get("cascade_assumed")
                for row in required_rows
            )
        )
        # Same-run planning satisfies planned voiceover later, so waiting is not a
        # blocker; only a genuinely missing input stays blocked.
        waiting = bool(required_rows) and will_run == 0 and len(deferred_rows) == len(required_rows)
        missing = list(required_warnings)
        if not steps:
            status = "excluded"
        elif not known:
            status = "unknown"
        elif waiting:
            status = "waiting"
        elif will_run == 0 and (
            any(
                int(row.get("total") or 0) == 0 for row in required_rows if not row.get("deferred")
            )
            or (required_warnings and not reusable)
        ):
            status = "blocked"
        elif reusable:
            status = "reusable"
        else:
            status = "ready"
        if status in {"unknown", "blocked"}:
            blockers.extend(item for item in missing if item not in blockers)
        usage = combine_usage(rows)
        output_steps = [str(row["name"]) for row in required_rows]
        output_target = {
            "source_prep": "video",
            "ai_analysis": "texts",
            "plan_alignment": ("plan" if "plan" in steps else "voiceover" if "voiceover" in steps else "texts"),
            "voiceover_delivery": "plan" if "plan_voiceover" in steps else "video",
        }[key]
        stages.append(
            {
                "key": key,
                "steps": steps,
                "status": status,
                "total": total,
                "will_run": will_run,
                "will_run_max": will_run + cascade_assumed,
                "will_skip": will_skip,
                "will_skip_min": max(0, will_skip - cascade_assumed),
                "warnings": warnings,
                "blockers": missing if status in {"unknown", "blocked"} else [],
                "estimate": usage,
                "estimate_is_minimum": bool(cascade_assumed or deferred_rows),
                "deferred": bool(deferred_rows) and len(deferred_rows) == len(required_rows),
                "waiting_on": waiting_on,
                "output": {"target": output_target, "steps": output_steps} if output_steps else None,
            }
        )
    next_stage = next(
        (stage["key"] for stage in stages if stage["status"] not in {"excluded", "reusable", "waiting"}),
        None,
    )
    return {
        "stages": stages,
        "next_stage": next_stage,
        "blockers": blockers,
        "outputs": [stage["output"] for stage in stages if stage["status"] == "reusable" and stage["output"]],
    }
'''

text = text[:idx] + new_block + text[end:]
path.write_text(text, encoding="utf-8", newline="")
print("stage block replaced")

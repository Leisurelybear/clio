from pathlib import Path

path = Path("clio/plan_readiness.py")
text = path.read_text(encoding="utf-8")

old_import = "from clio.plan_model import Plan, PlanIssue\n"
new_import = (
    "from clio.plan_model import Plan, PlanIssue\n"
    "from clio.plan_pacing import (\n"
    "    MAX_REPORTED_SHORT_SEGMENTS,\n"
    "    MIN_SEGMENT_SEC,\n"
    "    duration_floor,\n"
    "    notable_fragments,\n"
    "    segment_durations,\n"
    ")\n"
)
assert text.count(old_import) == 1
text = text.replace(old_import, new_import)

start = text.index("def _append_duration_window_warnings(")
end = text.index("def _append_source_issues(")
new_block = """def _append_duration_window_warnings(
    result: ReadinessResult,
    tes: float,
    sequence: Sequence[Any] | None = None,
    target_duration_sec: float | None = None,
) -> None:
    floor = duration_floor(target_duration_sec)
    if tes < floor:
        result.warnings.append(
            PlanIssue(
                level="warning",
                code="duration_short",
                message=t(
                    "plan.readiness.duration_short",
                    {"seconds": f"{tes:.1f}", "minimum": f"{floor:.1f}"},
                ),
            )
        )
    if tes > 1800:
        result.warnings.append(
            PlanIssue(
                level="warning", code="duration_long", message=t("plan.readiness.duration_long", {"seconds": tes})
            )
        )
    durations = segment_durations(sequence or ())
    fragments = notable_fragments(durations)
    if fragments:
        shown = fragments[:MAX_REPORTED_SHORT_SEGMENTS]
        result.warnings.append(
            PlanIssue(
                level="warning",
                code="duration_fragments",
                message=t(
                    "plan.readiness.duration_fragments",
                    {
                        "positions": ", ".join(str(position) for position in shown),
                        "count": len(fragments),
                        "min": f"{MIN_SEGMENT_SEC:.0f}",
                    },
                ),
            )
        )


"""
text = text[:start] + new_block + text[end:]

old_call = "    _append_duration_window_warnings(result, tes, target_duration_sec)\n"
new_call = "    _append_duration_window_warnings(result, tes, plan.sequence, target_duration_sec)\n"
assert text.count(old_call) == 1
text = text.replace(old_call, new_call)

path.write_text(text, encoding="utf-8")
print("readiness updated")

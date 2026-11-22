import re
from pathlib import Path

path = Path("clio/analyze.py")
text = path.read_text(encoding="utf-8")

start = text.index("PLAN_MIN_TOTAL_DURATION_RATIO = 0.8")
end = text.index("def _plan_validation_issues(")
new_block = '''def _plan_pacing_issues(result: dict, target_duration_sec: float) -> list[str]:
    """Report pacing problems that are advisory for the review pass.

    These issues never fail the plan on their own: short days may genuinely have
    little material. They are fed to the review pass so the model extends or
    merges existing cuts instead of shipping a string of 5-second fragments.
    """
    sequence = [segment for segment in result.get("sequence", []) if isinstance(segment, dict)]
    if not sequence:
        return []
    durations = segment_durations(sequence)
    total = sum(durations)
    issues: list[str] = []

    if target_duration_sec > 0:
        minimum = target_duration_sec * MIN_TOTAL_DURATION_RATIO
        if total + 0.05 < minimum:
            issues.append(
                t(
                    "analyze.plan_issue_total_short",
                    {
                        "total": f"{total:.1f}",
                        "target": f"{target_duration_sec:.1f}",
                        "minimum": f"{minimum:.1f}",
                    },
                )
            )

    fragments = notable_fragments(durations)
    if fragments:
        shown = fragments[:MAX_REPORTED_SHORT_SEGMENTS]
        issues.append(
            t(
                "analyze.plan_issue_fragments",
                {
                    "positions": ", ".join(str(position) for position in shown),
                    "count": len(fragments),
                    "min": f"{MIN_SEGMENT_SEC:.0f}",
                },
            )
        )
    return issues


'''
text = text[:start] + new_block + text[end:]

old_total = "_plan_total_seconds(valid_plan)"
text = text.replace('f"{_plan_total_seconds(valid_plan):.1f}"', "f\"{total_seconds(valid_plan['sequence']):.1f}\"")

path.write_text(text, encoding="utf-8")
print("rewritten")

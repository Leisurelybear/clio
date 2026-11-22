from pathlib import Path

path = Path("clio/analyze.py")
text = path.read_text(encoding="utf-8")
old = "from clio.prompt_overrides import format_prompt_template, resolve_prompt_template\n"
new = (
    "from clio.plan_pacing import (\n"
    "    MAX_REPORTED_SHORT_SEGMENTS,\n"
    "    MIN_SEGMENT_SEC,\n"
    "    MIN_TOTAL_DURATION_RATIO,\n"
    "    notable_fragments,\n"
    "    segment_durations,\n"
    "    total_seconds,\n"
    ")\n"
    "from clio.prompt_overrides import format_prompt_template, resolve_prompt_template\n"
)
assert text.count(old) == 1
path.write_text(text.replace(old, new), encoding="utf-8")
print("imports updated")

from pathlib import Path

path = Path("clio/tests/test_plan_readiness.py")
text = path.read_text(encoding="utf-8")

old = """    fragment = next(issue for issue in result.warnings if issue.code == "duration_fragments")
    assert "12" in fragment.message


def test_readiness_fragment_warning_needs_more_than_quarter_of_segments():
    sequence = _paced_segments(6, "00:00-00:30") + _paced_segments(3, "00:00-00:05")
    plan = _plan(sequence, total_estimated_sec=195)

    result = check_plan_export_readiness(plan, known_indices={"001"}, target_duration_sec=180)

    assert not any(issue.code == "duration_fragments" for issue in result.warnings)
"""
new = """    fragment = next(issue for issue in result.warnings if issue.code == "duration_fragments")
    # The opening segment is hook-exempt, so 11 of the 12 segments are fragments.
    assert "11" in fragment.message


def test_readiness_fragment_warning_needs_more_than_quarter_of_segments():
    within_tolerance = _plan(
        _paced_segments(7, "00:00-00:30") + _paced_segments(2, "00:00-00:05"),
        total_estimated_sec=220,
    )
    beyond_tolerance = _plan(
        _paced_segments(6, "00:00-00:30") + _paced_segments(3, "00:00-00:05"),
        total_estimated_sec=195,
    )

    within = check_plan_export_readiness(within_tolerance, known_indices={"001"}, target_duration_sec=180)
    beyond = check_plan_export_readiness(beyond_tolerance, known_indices={"001"}, target_duration_sec=180)

    assert not any(issue.code == "duration_fragments" for issue in within.warnings)
    assert any(issue.code == "duration_fragments" for issue in beyond.warnings)
"""
assert text.count(old) == 1
path.write_text(text.replace(old, new), encoding="utf-8")
print("fixed")

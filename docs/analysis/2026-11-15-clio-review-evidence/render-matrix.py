"""Render the review's Markdown/CSV matrix from its single data source."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORTS = HERE.parent


def source_link(location: str) -> str:
    parts = []
    for item in location.split("; "):
        path, separator, line = item.partition(":")
        anchor = f"#L{line}" if separator else ""
        parts.append(f"[`{item}`](../../{path}{anchor})")
    return "; ".join(parts)


def main() -> None:
    findings = json.loads((HERE / "findings.json").read_text(encoding="utf-8"))
    assert len({row["id"] for row in findings}) == len(findings)
    root = HERE.parents[2]
    for row in findings:
        for item in row["location"].split("; "):
            path, separator, line = item.partition(":")
            target = root / path
            assert target.exists(), item
            if separator:
                assert 1 <= int(line) <= len(target.read_text(encoding="utf-8-sig").splitlines()), item
    csv_path = REPORTS / "2026-11-15-clio-review-matrix.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(findings[0]))
        writer.writeheader()
        writer.writerows(findings)
    lines = [
        "# CLIO Review Issue and Optimization Matrix (2026-11-15)",
        "",
        "Baseline: `aef5b0a96692a93893f680158a30044dfc867e53`. All findings remain open; this review changes no production behavior.",
        "",
        "Read with the [final assessment](2026-11-15-clio-final-review.md), [module review](2026-11-15-clio-module-review.md), and [verification record](2026-11-15-clio-review-evidence/verification.md). A [CSV version](2026-11-15-clio-review-matrix.csv) supports sorting and assignment.",
        "",
        "## Interpretation",
        "",
        "- P0: demonstrated broad catastrophic loss or critical exploitable compromise; none established in this audit.",
        "- P1: data integrity, artifact access boundary, or active-run reliability; address before widening multi-project/concurrent use.",
        "- P2: incorrect supported behavior or a material validation/reproducibility gap.",
        "- P3: maintainability, observability, documentation, or incremental assurance improvement.",
        "- R rows are reproduced defects. O rows are measured/static improvements or explicit validation gaps; they are not additional reproduced failures.",
        "- S/M/L are relative implementation effort, not calendar estimates. Assign an owner and a focused implementation plan before scheduling.",
        "- Python/Frontend probe IDs refer to the evidence directory. These probes assert the current defect and must be inverted into normal regression expectations when fixing it.",
        "- This matrix uses local R01-R17/O01-O10 identifiers; historical R001/OPT identifiers belong to their own dated tracker. O01/O02 overlap its two open items and must not be counted twice.",
        "",
    ]
    for kind, title in (("defect", "Confirmed defects"), ("optimization", "Optimization and validation backlog")):
        selected = [row for row in findings if row["kind"] == kind]
        counts = Counter(row["priority"] for row in selected)
        lines += [
            f"## {title}",
            "",
            f"{len(selected)} rows: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)) + ".",
            "",
            "| ID | Priority | Area | Problem | Code location | Effort |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in selected:
            lines.append(
                f"| [{row['id']}](#{row['id'].lower()}) | {row['priority']} | {row['area']} | {row['problem']} | {source_link(row['location'])} | {row['effort']} |"
            )
        lines.append("")
    lines += ["## Detailed repair and acceptance contracts", ""]
    for row in findings:
        lines += [
            f"### {row['id']}",
            "",
            f"**{row['priority']} — {row['area']}**. {row['status']}.",
            "",
            f"**Problem:** {row['problem']}",
            "",
            f"**Location:** {source_link(row['location'])}",
            "",
            f"**Evidence and limits:** {row['evidence']}",
            "",
            f"**Recommended repair:** {row['recommendation']}",
            "",
            f"**Acceptance criteria:** {row['acceptance']}",
            "",
            f"**Coordination / effort:** {row['dependency']}; {row['effort']}.",
            "",
        ]
    lines += [
        "## Suggested repair sequence",
        "",
        "1. Preserve editor and destination content: R03-R06, R10; enforce the artifact boundary in R08.",
        "2. Stabilize cache and active-run behavior: R01/R02/R11, then R15/R16 with legacy compatibility tests.",
        "3. Complete supported workflows: R07/R17, R09, R12-R14.",
        "4. Pair O03 regressions with each fix, then address release/product acceptance O04/O05. Apply O01/O02/O06 incrementally; keep O07-O10 as measured maintenance work.",
        "",
        "Each independent fix should use its own small Conventional Commit, update this review's status/evidence, run focused regressions and the relevant full suite, and preserve unrelated working-tree changes. No remote push is authorized by this review request.",
        "",
    ]
    (REPORTS / "2026-11-15-clio-review-matrix.md").write_text("\n".join(lines), encoding="utf-8")
    print(
        json.dumps(
            {
                "rows": len(findings),
                "defects": sum(r["kind"] == "defect" for r in findings),
                "counts": dict(Counter(r["priority"] for r in findings)),
            }
        )
    )


if __name__ == "__main__":
    main()

# Analysis Evaluation Suite v1

**Status:** completed real-footage baseline, 2026-11-13 (commit `98b4f4d`).

This suite contains 12 reviewed cases from real project footage. The manifest records each source
filename and exact duration; every reference was checked against a 12-frame contact sheet and every
prediction is a sanitized analysis artifact. No absolute paths, media identity, transcript detail, or
cover path is committed.

## Baseline

| Metric | Result |
|---|---:|
| Scored cases | 12 |
| Case errors | 0 |
| Mean score | 96.92 |
| Minimum score | 87.51 |
| Minimum-score gate | 70.00 passed |

Nine cases scored 100.00. The three lower scores are genuine long-clip coverage gaps: `case-01-urban-walk`
(269.533s, 37.56% coverage), `case-06-night-scene` (168.867s, 38.49%), and `case-10-action-sports`
(272.067s, 39.05%). Fact recall, key moments, density, and structure remain 100.00 for those cases.
The baseline is intentionally retained rather than relabeled to hide the long-timeline weakness.

Generated reports are committed as [`evaluation.json`](evaluation.json) and
[`evaluation.md`](evaluation.md).

## Layout

```text
2026-11-12-analysis-evaluation-suite-v1/
|-- manifest.json
|-- evaluation.json
|-- evaluation.md
|-- predictions/
|   `-- case-*.json (12)
|-- references/
|   `-- case-*.json (12)
`-- 2026-11-12-analysis-evaluation-suite-v1.md
```

## Reproduce the baseline

```powershell
python main.py evaluate-analysis `
  --manifest docs/analysis/2026-11-12-analysis-evaluation-suite-v1/manifest.json `
  --predictions docs/analysis/2026-11-12-analysis-evaluation-suite-v1/predictions `
  --out-dir output/analysis-evaluation `
  --min-score 70
```

The command must report 12 scored cases, 0 errors, mean 96.92, minimum 87.51, and a passing gate.

## Maintenance

1. When an analysis prompt, model binding, or long-window strategy changes, regenerate sanitized
   predictions for the same 12 source clips.
2. Re-check duration, aliases, and key-moment intervals only when the underlying footage changes.
3. Keep labels reviewed before scoring; the harness rejects unreviewed references.
4. Compare the new mean and per-case coverage against this baseline, then commit the regenerated
   reports without removing genuine coverage failures.

## Schema v1

Manifest fields:

- `_schema_version`: `1`
- `suite`: stable suite name
- `cases[]`: `id`, `prediction`, `reference`, `duration_sec`, optional `description`

Reference fields:

- `_schema_version`: `1`
- `review_status`: must be `reviewed` before scoring
- `duration_sec`: real media duration
- `required_facts[]`: `id`, `description`, `aliases`
- `key_moments[]`: `id`, `label`, `start`, `end`, optional `min_overlap_ratio`
- `density`: `min_coverage_ratio`, `min_entries_per_min`, `max_entries_per_min`
- optional `required_structure`: defaults to `title`, `summary`, `location`, `mood`, `timeline`,
  `highlights`, `suggested_use`, and `cover_timestamp`

Manifest `prediction` and `reference` values must be relative paths and must not escape the manifest
directory. `--predictions` may override the prediction root; the harness first checks
`<predictions>/<case-id>.json`, then the safely reduced basename from the manifest.

## Scoring

| Metric | Weight | Behavior |
|---|---:|---|
| Coverage | 20% | Unioned valid timeline coverage against the reference target |
| Density | 15% | Timeline entries per minute within reference bounds |
| Fact recall | 25% | Required aliases found anywhere in prediction string values |
| Key moments | 25% | Best timeline overlap for each reference moment |
| Structure | 15% | Required fields and valid highlights/cover timestamp |

Reports are written as `evaluation.json` and `evaluation.md`. The CLI exits `1` when a case errors
or the mean score is below `--min-score`; malformed manifests and invalid options exit `2`.

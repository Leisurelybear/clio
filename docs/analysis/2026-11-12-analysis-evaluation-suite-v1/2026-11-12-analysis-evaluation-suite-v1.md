# Analysis Evaluation Suite v1

**Status:** harness and versioned scaffold implemented, 2026-11-12. This directory contains 12
versioned test slots, not a completed evaluation baseline. No real project footage, prediction JSON,
reviewed labels, reports, or scores are committed here yet.

The harness deliberately rejects every reference whose `review_status` is not `reviewed`, so the
placeholder labels cannot accidentally produce a misleading score. A baseline is valid only after
the duration, facts, aliases, and key moments have been checked against the selected real videos.

## Layout

```text
2026-11-12-analysis-evaluation-suite-v1/
??? manifest.json
??? references/
?   ??? case-01-urban-walk.json
?   ??? ... 12 cases total
??? 2026-11-12-analysis-evaluation-suite-v1.md
```

Prediction files are expected under `predictions/<case-id>.json`. That directory is intentionally
absent until real analysis output is selected and reviewed.

## Complete the suite

1. Select a short, representative clip for each case and record its real duration.
2. Generate analysis JSON for each clip and place it under this directory as
   `predictions/<case-id>.json`.
3. Review every reference against the footage. Replace each `REPLACE` marker and placeholder alias,
   set accurate key-moment intervals, then change `review_status` from `pending-real-footage` to
   `reviewed`.
4. Run the harness with a temporary output directory while calibrating:

   ```powershell
   python main.py evaluate-analysis `
     --manifest docs/analysis/2026-11-12-analysis-evaluation-suite-v1/manifest.json `
     --predictions docs/analysis/2026-11-12-analysis-evaluation-suite-v1/predictions `
     --out-dir output/analysis-evaluation `
     --min-score 70
   ```

5. Commit a first baseline only after all 12 references are reviewed and the source media identity is
   recorded in the change notes. The committed baseline must include the exact manifest, references,
   and generated `evaluation.json` / `evaluation.md`; it must not reuse these placeholder scores.

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

# Capture-Day Scope Audit - `D:\vlog_projects\202610_Italy`

**Date:** 2026-11-19
**Type:** Read-only regression audit (no project mutation)
**Design:** [`../superpowers/specs/2026-11-19-capture-day-scopes-design.md`](../superpowers/specs/2026-11-19-capture-day-scopes-design.md)
**Plan:** [`../superpowers/plans/2026-11-19-capture-day-scopes-plan.md`](../superpowers/plans/2026-11-19-capture-day-scopes-plan.md)

## Why this audit exists

The reference project reports two symptoms in guided Run:

1. "同日对齐 + 剪辑方案" (stage `plan_alignment`) stays `待执行` with counts like
   `待执行 1-1, 跳过 363-363`.
2. "口播与交付" (stage `voiceover_delivery`) shows counts like
   `待执行 181-181, 跳过 182-182`, and the user cannot preview the produced artifact.

This document records, read-only, what the artifacts on disk actually contain and which
counters they feed. It does not rewrite, move, or delete any project file.

## On-disk inventory

| Directory | Files |
| --- | --- |
| `output/compressed` | 181 `.mp4` (+181 `.vmeta`, 181 `.vindex`) |
| `output/texts` | 362 `.json` |
| `output/transcripts` | 182 `.json` |
| `output/scripts` | 364 `.json` + 364 `.md` |
| `output/labeled` | 181 `.mp4` |
| `output/plans` | `day1_plan.{json,md}`, `day1_entity_consistency.{json,md}`, `test_plan.*`, `trip_plan.json` |
| `output/cuts` | absent |

## Analysis JSON analysis (362 files)

- 362 analysis JSON files.
- 181 unique `source_file` values.
- 181 duplicates: exactly two analysis JSON files per original source.
- 362 files contain `media_identity`; 0 missing.
- 180 files have no `day_label` / `day` / `dayLabel`.
- Persisted label distribution:

  | label | count |
  | --- | --- |
  | `<missing>` | 180 |
  | `day1` | 21 |
  | `day2` | 17 |
  | `day3` | 29 |
  | `day4` | 11 |
  | `day5` | 30 |
  | `day6` | 17 |
  | `day7` | 24 |
  | `day8` | 26 |
  | `day9` | 7 |

### Duplicate pairs use different compressed identities

Example original `GX011275.MP4`:

- stale analysis `016_游览罗马斗兽场.json`: `index=16`, `media_identity.compressed_stem="016_GX011275"`,
  `compressed_file="016_GX011275.mp4"`.
- actual compressed file on disk: `output/compressed/197_GX011275.mp4`.

The original was first analysed under a `001..181` numbering, then re-compressed under a
`182..362` numbering. The file is identical; only the index prefix moved. The stale analysis
still points at a compressed filename that no longer exists.

`output/compressed` filenames run `182_GX011260.mp4` .. `362_GX011439.mp4`, while the analyses
and the plan use `001..181` / `day1..day9`. `output/labeled` uses the `182..362` numbering.
Deduplication therefore cannot key on the numeric index, the compressed stem, or the analysis
filename; it must key on the original media identity (`media_identity.original_stem` /
`source_file`).

## What the guided Run counters count

`clio/ui/services/run_preview.py` builds each stage from the step rows:

- `voiceover` and `plan_voiceover` use `_analysis_jsons()` (every `texts/*.json`) or the day count.
- `label` iterates every analysis JSON, then `_label_has_compressed_input()` skips those whose
  index has no matching compressed video.
- `consistency` and `plan` are day-scoped and use `all_analysis_jsons`.

So the reported `跳过 362-362` / `待执行 181-181` totals are the **362 raw analysis JSON files**,
not the **181 media clips the user selected**. `_analysis_jsons()` returns 362 entries because
both the stale (`001..181`) and current (`182..362`) analyses exist side by side. This is exactly
the counting gap the design calls out: the guided UI shows a number that does not correspond to
the media the user actually has.

Before commit `3a45cb0`, `label` reported the stale `001..181` analyses as runnable even though
their compressed input is gone (giving the `待执行 181` in the report). After that commit those
rows are reported as skipped, but the totals are still derived from the raw 362 JSON files.

## Plan and preview artifact mismatch

`output/plans/day1_plan.json` (12 sequence entries) references indexes such as `016`, `022`, `025`
- the **stale** numbering. `GET /api/plan/preview-media` maps those entries to clips under
`output/cuts/<day>/`, which does not exist, and the frontend falls back to a source/compressed
video with the same `index`. Because no compressed video uses index `016`, the fallback cannot
resolve media and the plan timeline has nothing to play. The stale numbering recorded in the plan
is why the produced artifact cannot be previewed, despite the underlying clip existing as
`197_GX011275.mp4`.

The same mismatch makes plan export/cut readiness report `index_missing` for the `001..181`
indexes, which is the issue the working-tree `clio/plan_readiness.py` change addresses by no longer
treating text-only analyses as "known" media.

## AI call errors observed (separate from scope identity)

The task center records the recent guided runs:

- `85b5e085...` (steps `consistency,voiceover,plan`, phase `plan`) failed with
  `API https://hub.oaifree.com/v1 返回 401: {"error":{"type":"Unauthorized","message":"Invalid API key"}}`.
  The project's `project.yaml.bak` shows `vlog_plan -> linux_do / gpt-6.1-sol` at that time; the
  provider was later changed to `Agnes`.
- `59f17180...` (steps `analyze`) failed with
  `AI 分析未完整完成（180 个成功，1 个失败）... GX011330.MP4: 503 UNAVAILABLE`.

So the first symptom ("stuck" at plan-alignment) was a genuine upstream AI error (invalid API key,
then a 503), not a stage-gating bug. The scope-identity duplication described above is a separate,
independent defect that makes the counts and preview wrong even when the AI calls succeed.

## Expected behaviour after the fix (acceptance target)

- The reference project reports **181** deduplicated clips, not **362** raw analysis files.
- `day1..day9` resolve to stable scopes; unlabeled/undated media is not silently assigned to
  `day1`.
- A plan is generated from, and judged reusable against, the same deduplicated identity set, so
  the plan preview can resolve media for every sequence entry.
- A successful `consistency + plan` run advances `next_stage` instead of leaving the stage ready.

## Reproduce (read-only)

```powershell
$p = 'D:\vlog_projects\202610_Italy\output'
(Get-ChildItem "$p\texts" -Filter *.json).Count            # 362
(Get-ChildItem "$p\compressed" -Filter *.mp4).Count        # 181
(Get-ChildItem "$p\compressed" -Filter *.mp4 | Select-Object -First 1).Name  # 182_GX011260.mp4
```

The synthetic regression fixture lives in `clio/tests/test_run_scope_identity.py`, and the
delivery-stage counter regression lives in `clio/tests/test_run_preview.py`.

# Run Preview Cascade Correctness Plan (2026-11-18)

## Scope

This checkpoint records three related Run-tab defects before implementation:

- `RP-01`: Run preview checks each selected step against disk independently, so it does not
  account for artifacts invalidated by another step selected in the same run.
- `RP-02`: Planned voiceover is reported as a final blocker when planning is selected in the
  same run and will produce its required input first.
- `RP-03`: Entity consistency lineage includes generated clip voiceover text, while the
  pipeline runs consistency before voiceover. A same-run voiceover rerun can therefore
  invalidate the consistency report that planning just consumed.

Repo baseline at checkpoint: `31a5347`, existing `clio/tests/test_run_preview.py` at 87 passed.

## RP-01: Same-run cascade

### Problem

`build_run_preview()` asks every selected step to evaluate its current disk cache. Those
answers are then added together even though runtime executes the canonical pipeline order:

```text
compress -> transcribe -> analyze -> consistency -> voiceover -> plan -> plan_voiceover -> label
```

If an upstream step will run, a downstream artifact currently on disk is not guaranteed to
remain reusable. Reporting it as a definite skip understates work, AI calls, cost, and failure
surface.

Examples observed before the checkpoint:

- analyze selected and rerunning, while voiceover and plan still show reusable;
- compress selected and rerunning, while analyze still shows reusable even though compressed
  media identity changes;
- a real project preview showing `will_run=725`, `will_skip=183`, but only `planned_calls=299`.

### Required semantics

1. Backend preview is authoritative; the frontend does not recompute lineage.
2. Steps are evaluated and reported in canonical pipeline order.
3. Cascade only originates from a selected step that the preview says will run.
4. An unselected upstream step must never trigger cascade.
5. A skipped artifact affected by cascade is not a definite skip. It remains a maximum
   reuse count, while confirmed execution remains the minimum.
6. Preview stays read-only: no directory creation, no artifact writes, no task invocation.
7. Unknown/custom step behavior remains compatible.

### Step contract

Cascade fields are added only when relevant:

```json
{
  "cascade_from": ["analyze"],
  "cascade_assumed": 12,
  "will_run_max": 15,
  "will_skip_min": 3,
  "estimate_is_minimum": true
}
```

Definitions:

- `will_run`: confirmed minimum execution count.
- `will_run_max`: confirmed execution plus currently reusable items that upstream work may
  invalidate.
- `will_skip`: maximum skip count if same-run upstream output happens to remain reusable.
- `will_skip_min`: minimum skip count after cascade risk.
- `cascade_assumed`: portion of `will_skip` treated as at risk.
- `estimate_is_minimum`: current AI-call/cost/upload estimate is a lower bound.

Totals expose the same aggregate fields plus `estimate_is_minimum`. Existing fields remain for
older UI clients.

### Cascade edges

Direct edges are kept explicit and intentionally small:

```text
compress    -> analyze, transcribe (only when transcript audio comes from compressed media)
transcribe  -> analyze (when transcript context is enabled)
analyze     -> consistency, voiceover, plan
consistency -> plan
voiceover   -> plan
plan        -> plan_voiceover
```

`label` is not cascade-invalidated by this change because runtime currently reuses its output
by existence only. That cache policy can be reviewed separately.

### Risk estimate

`analysis_rerun_risk()` must also report analyze rows made uncertain by same-run compress or
transcribe work. The confirmation dialog must not imply analysis is safely reusable when the
preview already knows its input will change.

## RP-02: Deferred planned voiceover

When `plan` is selected and will run (or is itself cascade-invalidated), `plan_voiceover` may
proceed after planning completes. It must not enter final blockers.

Step contract when deferred:

```json
{
  "deferred": true,
  "waiting_on": ["plan"],
  "total": 1,
  "will_run": 0,
  "will_run_max": 1,
  "warnings": [],
  "estimate_is_minimum": true
}
```

The stage contract adds `waiting` as a non-actionable status. `next_stage` skips waiting-only
stages; blockers exclude deferred warnings. When planning is not selected and no valid plan
exists, `plan_voiceover` remains a blocker as today.

## RP-03: Consistency/voiceover ordering

Entity consistency should analyze source evidence (titles, place, timeline, moments), not the
generated clip voiceover script. The current projection through `build_plan_clips()` includes
`voiceover`, which creates this loop:

```text
old voiceover -> consistency -> new voiceover -> stale consistency -> plan ambiguity
```

Add a consistency-specific projection that removes `voiceover` while preserving the existing
bounded plan projection. Do not remove voiceover from normal plan input, because planning does
use it.

Acceptance: changing only a clip voiceover artifact does not invalidate entity consistency;
the plan still invalidates because voiceover is part of its own lineage.

## UI contract

- Preview totals describe confirmed and possible work separately.
- Cascaded rows show which upstream steps may invalidate them.
- The preview explains that estimates are minimums when cascade or deferred work exists.
- Waiting rows show that planning must finish first.
- Stage statuses are `excluded`, `unknown`, `blocked`, `waiting`, `reusable`, `ready`, `active`,
  `done` as applicable.
- All dynamic text is escaped; zh-CN and en locale key sets stay identical.

## Tests

Backend:

1. compress rerun prevents analyze from being reported as definitively reusable;
2. analyze rerun cascades to voiceover and plan;
3. transcribe rerun cascades to analyze when transcript use is enabled;
4. selecting only downstream steps does not cascade;
5. existing no-ffprobe conservative compress behavior remains;
6. plan_voiceover is deferred when plan runs in the same preview and is not a blocker;
7. plan_voiceover still warns when plan is absent and not selected;
8. cascade totals and minimum-estimate flags are exposed;
9. preview remains read-only;
10. consistency remains reusable after only voiceover text changes, while plan invalidates.

Frontend:

1. minimum and possible counts render for cascade rows;
2. cascade source is shown and escaped;
3. deferred planned voiceover renders as waiting, not blocked;
4. waiting stages do not become `next_stage`;
5. existing disabled-transcribe and blocker behavior remains unchanged.

## Verification

Focused commands:

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe -m pytest clio\tests\test_run_preview.py clio\tests\test_consistency.py -v
npm run typecheck
npm test
git diff --check
```

Before handoff, also run the repository-wide checks required by `AGENTS.md`.

## Separate follow-up

Transcript cache validation against compressed media identity is intentionally excluded. Its
correctness depends on `compress.remove_audio`: with audio retained, a changed compressed file
can change transcription; with audio removed, transcription comes from the original video.
That policy needs its own defect record, tests, and commit.

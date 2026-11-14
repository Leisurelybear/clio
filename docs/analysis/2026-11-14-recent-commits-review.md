# Recent commits review (2026-11-14)

Scope: commits dated November 12 through November 14, 2026 (Asia/Shanghai), ending
at `044f1b8`. The history contains 424 commits and changes 322 files. This review
inspected the cumulative changes and focused on execution boundaries, cache reuse,
overwrite protection, provider calls, and the new guided runner. It is not a
line-by-line certification of every changed file. Existing unrelated untracked
files were preserved. No production code was changed.

## Findings

### P1: Guided execution trusts incomplete cache predictions

Locations: `clio/ui/services/run_preview.py:829-939`,
`clio/ui/static/src/runner-stage-model.js:26-30`.

Plan, consistency, and planned-voiceover preview rows mostly check JSON/Markdown
existence rather than the runtime lineage predicates. Voiceover similarly checks
file existence. The new runner treats those rows as proof of reusable output and
omits the stage entirely. Changing trip context, prompts, model settings, or
upstream analysis can therefore leave stale downstream artifacts in use.

Reproduction: create an existing plan with `_lineage: "stale"` and a Markdown
sidecar, then preview with a new context. Preview returns `will_run=0`,
`will_skip=1`, no warnings, and zero AI calls. Calling the runtime
`_try_reuse_existing_plan()` with the same context returns `None` (regeneration
required).

Use shared read-only reuse predicates for preview and execution, including scope,
lineage, overwrite mode, and `skip_existing`. Add a regression connecting real
preview output to guided stage selection.

### P1: All-day execution captures day labels before analysis creates them

Location: `clio/pipeline.py:191`, with consumers in `_run_step()`.

The pipeline discovers labels once, before the step loop. A new project's
`analyze` step creates the first day labels, but later consistency and planned
voiceover steps iterate the earlier empty list. The plan step independently
rediscovers days, so plans can exist without either accompanying output while the
pipeline reports success. Newly added or relabeled days have the same problem.

Reproduction: discovery initially returns `[]`; simulate analysis creating
`day1`/`day2`. Discovery is called only once, neither day-scoped callback runs, and
both steps are recorded as successful with zero processed outputs.

Discover current days immediately before each day-scoped step. Test a fresh
all-day pipeline whose analysis step creates the artifacts used by discovery.

### P2: Turning off transcripts blocks the guided runner

Locations: `clio/ui/static/src/runner-stage-model.js:26-30`,
`clio/ui/services/run_preview.py:723-725`.

When `use_transcripts=false`, preview represents transcription as a zero-total
warning row. Once compression is reusable, the preparation stage has zero pending
work plus that warning, so it becomes `blocked`. `nextIncompleteStage()` chooses
it and the Start button is disabled even when analysis is ready. Users must
manually alter the hidden custom step selection to proceed.

Reproduction with the actual backend row shape confirms preparation is selected
as blocked while analysis is ready. Represent disabled optional steps explicitly
and exclude them from blocking requirements. Preserve real missing-input errors.

### P2: Analysis ignores the transcript option used by preview

Locations: `clio/pipeline.py:79-105`, `clio/tasks/analyze.py:1574`.

Run routes persist the checkbox in `cfg.plan.use_transcripts`, and preview passes
it into analysis cache checks. Pipeline kwargs never forward it to
`run_analyze_all()`, whose default is `True`. Analysis therefore still injects
existing transcripts and computes a different lineage when the user turned them
off. This also undermines overwrite preflight accuracy.

Reproduction: run the analysis step with `cfg.plan.use_transcripts=False`; the
step receives no `use_transcripts` argument and uses its default `True`.

Forward the effective option explicitly and verify preview, overwrite checks, and
the actual provider prompt agree for both values.

### P2: A failed review call discards a locally valid planning draft

Location: `clio/analyze.py:1816-1835`.

`_parse_plan_review()` handles malformed returned JSON, but `call_stage()` is
evaluated before that fallback. A review timeout, provider error, or invalid
correction after retries aborts planning despite an already validated draft. This
does not meet the documented review-failure fallback behavior.

Reproduction: return an outline and a locally valid shot-selection draft, then
raise `TimeoutError("review timeout")` from the review call. `plan_daily_vlog()`
propagates the exception instead of returning the draft.

Retain a locally valid candidate and fall back to it for review failures; continue
to propagate cancellation. Do not fall back to a draft that failed validation.

## Verification

- Python 3.11.9 from `.venv/Scripts/python.exe`: full suite, **3390 passed,
  12 skipped**, including the real-ffmpeg smoke test.
- Vitest: full suite, **945 passed across 90 files**.
- `npm run typecheck`: passed. Its configured scope excludes the new runner
  modules, so this is not evidence of their type safety.
- `git diff --check` across the reviewed range: passed.
- Additional in-memory reproductions confirmed all five findings. They used
  temporary directories or patched provider/step boundaries and did not call
  live AI services.

## Assessment

The changes improve modularity, failure diagnostics, regression coverage, and SSE
shutdown behavior. Existing tests are green. Further fixes are required before
calling the batch complete: the most consequential gaps occur where the guided
UI turns approximate previews into execution decisions and where pipeline stages
consume state created by earlier stages.

Prioritize shared preview/runtime cache predicates and fresh all-day discovery,
then fix transcript propagation, disabled-step handling, and planning fallback.
Add behavioral regressions at those boundaries rather than more idealized
per-function mocks. No push or commit was performed during this review.

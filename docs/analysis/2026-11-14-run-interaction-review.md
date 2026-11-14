# Run interaction review (2026-11-14)

Reviewed commit: `044f1b8`.

## Resolution (2026-11-15)

All four findings below are addressed:

- Finding 1: `35e461a` shares runtime reuse checks with preview. The follow-up regression
  changes the actual analysis artifact after stamping current plan, consistency, clip-script,
  and planned-voiceover caches; all four previews correctly return to pending execution.
- Finding 2: `6a362a2` defines overwrite as a single submission option, resets it only on
  success, then chooses the next stage from a non-forced preview. Failure/cancellation
  retain the option for an explicit retry.
- Finding 3: `7b35fd1` handles explicitly disabled preview steps. `cfe93e3` additionally
  provides a guided skip action when ASR is unavailable or produces no transcript. It
  deselects generation while preserving existing transcript reuse, with no backend changes.
- Finding 4: `2ecc9b6` resolves the shared runtime phase from active task steps and the
  preceding explicit phase, covering guided delivery and full custom runs.

Verification: 951 Vitest tests across 90 files, 70 run-preview pytest cases using the project
Python 3.11 environment, frontend typecheck, focused Ruff check/format, and diff checks passed.
No live AI pipeline was started. The review below is retained as historical evidence.

## Requirements assessment

The presentation goal is clear: four stages, explicit next actions, folded custom controls,
live progress, and explicit result navigation within Run. Acceptance criteria remain incomplete
for optional transcription, forced reruns, cache freshness after upstream changes, persisted
custom mode, mixed project-wide/episode scope, and completion output evidence. The implementation
does not yet meet the reliable guided-progression requirement.

## Findings

1. **P1 — Existing output is treated as valid downstream cache.**
   `runner-stage-model.js:26` makes an all-skipped preview stage reusable and `:36` omits it
   from next actions. Consistency and plan previews check existence/JSON readability, whereas
   execution checks lineage (`run_preview.py:842`, `:891`; `tasks/plan.py:496`). After analysis
   changes, stale downstream files can therefore suppress planning entirely. The new guide
   promotes an estimate into an execution gate. It must not certify cache freshness without
   authoritative evidence, or must explicitly offer downstream execution after upstream changes.

2. **P1 — Overwrite mode repeats the first stage indefinitely.**
   `runner.js:383` clears completion state on every idle preview; `:794` triggers that preview
   immediately after completion. With overwrite still checked, every stage remains pending and
   next selection returns to preparation. Preserve completion within an explicitly scoped run
   session, or define overwrite as a single-action option.

3. **P1 — Optional transcription can prevent progression.**
   With compressed output present and `use_transcripts=false`, preview returns a zero-input
   transcription warning. Aggregation marks preparation blocked (`runner-stage-model.js:30`),
   and `runner.js:324` disables its action rather than advancing to analysis. When ASR itself is
   disabled or unavailable, execution may return success without producing transcripts and the
   refreshed guide repeats preparation. Model disabled optional work separately from missing
   mandatory inputs, with an explicit skip/recovery action.

4. **P2 — Plan voiceover highlights the wrong stage.**
   `runner.js:701` uses the raw phase to select a stage. Planned voiceover execution emits
   `phase="voiceover"` (`tasks/scripts.py:804`), which maps to planning rather than delivery.
   Resolve the phase against the active task steps or otherwise disambiguate the two operations
   within the frontend.

## Verification and coverage gaps

Three temporary Vitest cases reproduced findings 2–4 through production UI entry points;
all three passed assertions describing the defective behavior. The temporary file was removed.
A direct stage-model reproduction confirmed that existing downstream preview skips yield no
next action after an updated analysis preview becomes reusable. No live AI task was started.

The committed tests use uniformly runnable synthetic preview rows, and do not cover full
progression across successive previews with overwrite, disabled ASR, or stale downstream files.
Completion currently offers generic result/folder actions rather than the proposed verified
output list and consistency-report view. Processing-state skip diagnostics are historical,
not a task-scoped list. Clarify these acceptance criteria before calling the redesign complete.

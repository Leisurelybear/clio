# Pipeline UX, Reliability, and Recovery Review (2026-11-15)

Review baseline: `558696b`, including the existing working tree. Remediation continued on
`adf9ffa` on November 15, 2026. Unrelated credential-management and comprehensive-review work
was preserved. The user authorized implementation after the assessment. No live AI calls were made.

## Delivered behavior

The ten reproduced defects below have production fixes and behavioral regressions. Managed
pipeline analysis now fails honestly on incomplete processing and stops dependent steps. A
completed task preserves the editor's unsaved buffer and offers explicit result navigation.
Task Center retries hand off to Run, terminal handling is idempotent, and failure/cancellation
refresh the preview with forced overwrite cleared.

Recovery remains artifact-based continuation: a restart marks active records `interrupted`;
the user retries to continue. The retry clears overwrite and old confirmations, checks the
persisted non-secret configuration digest, and validates existing outputs again. Changed
configuration, model, source selection, or prompts require a fresh submission. Intervening
manual edits require new overwrite approval through Run. Credential rotation is allowed.

| Finding | Resolution and regression evidence |
| --- | --- |
| U01 (also R01/R02) | Preview uses runtime's compression scan/reuse predicates; codec and effective CRF enter metadata. Twelve cache regressions compare preview counts with actual orchestration for valid, stale, small, unplayable, hash-changed and same-basename outputs. Size-budget mode ignores inactive CRF changes. |
| U02 | Managed execution uses strict analysis outcomes; failed task/event persistence retains result counts and bounded diagnostic reasons; downstream voiceover is not called. Existing CLI non-strict mode remains available. |
| U03 | Retry preparation clears all overwrite approvals, forces valid-cache reuse, validates configuration/prompt/source-selection identity before submission and again under task ownership. Tests cover manager recreation, persisted interrupted records, config drift between submission and execution, manual-edit gates, legacy records, secret rotation and secret-free history. |
| U04 | Completion reloads lists without replacing the shared plan buffer, changing day, saving the project or navigating automatically. Tests preserve object identity and dirty state for compression and planning completion. |
| U05/U06 | A task-session module handles handoff, stream sequence ordering, stale snapshots and one finalization per task. Fast terminal retries and repeated terminal events are covered. Reopening Run restores controls without replaying completion side effects. |
| U07 | Day, all-day scope and steps come from the task. Explicit result navigation uses that scope. Pending submissions, previews and completion/list refreshes are guarded against project switches. |
| U08 | Error/cancellation refresh videos and preview, clearing one-shot overwrite. Both terminal outcomes are covered. |
| U09 | All live ProcessingState instances for one resolved output path share data and a lock. Delayed flushes and concurrent mutations preserve updates; the matrix remains historical diagnostics, not a recovery checkpoint. |
| U10 | Explicit empty steps fail before reindex/output creation or task submission; omitted steps still mean the complete pipeline. |

Metadata that lacks codec, or effective CRF in CRF mode, is conservatively stale and will be
re-encoded once. Legacy whole-file outputs without sidecars retain their existing read-only
compatibility; playable duration cannot prove their historical source/settings provenance.

## Original assessment (before remediation)

The pipeline has useful reliability foundations: SQLite task/event persistence, explicit
state transitions, project artifact serialization for managed tasks, cooperative cancellation,
atomic JSON writes, overwrite gates, and lineage-based reuse for several AI outputs.
However, it cannot currently promise reliable recovery or consistent guided execution.
The Run page, task records, processing matrix, preview estimates, and actual artifacts can
disagree. A successful task is not proof that all requested outputs were produced.

Restart recovery marks old active tasks `interrupted`; it does not resume execution.
Retry creates a new task with the old persisted request. It relies on individual steps to
reuse artifacts, retains forced overwrite and previous confirmation booleans, and reloads
current configuration instead of reproducing the original private configuration snapshot.
Requests using temporary context/prompts correctly disable generic retry because those inputs
are not persisted. These are real limitations of recovery, not automatic loss of all outputs.

## Original findings (before remediation)

IDs below are local to this review. U01 overlaps R01 in the comprehensive review; do not count
that defect twice. Previous Run interaction fixes remain valid within their tested scope.

| ID | Priority | Finding and observable impact | Source |
| --- | --- | --- | --- |
| U01 | P1 | Compression preview certifies reuse by matching stems, while execution checks metadata, changed settings and playable duration. The guide can omit needed compression. Codec/CRF are also absent from the runtime fingerprint (existing R02). | `clio/ui/services/run_preview.py:773`; `clio/tasks/compress.py:56`, `:95` |
| U02 | P1 | Managed pipeline execution uses non-strict defaults. An analysis result with `failed=1` still runs downstream work; TaskManager persists `succeeded` with `result_summary.error_count=1`. Run drops that summary and displays success. Partial processing may be intentional, but complete success is misleading and failed/interrupted-only retry controls are unavailable for this task. | `clio/pipeline.py:150`; `clio/ui/routes/run.py:98`; `clio/task_center/manager.py:392`; `clio/ui/static/src/runner.js:574` |
| U03 | P1 | Retry retains `overwrite=true` and old overwrite confirmations, but loses the original private `_config`. A reproduced retry changes effective width from 640 to 800 and still forces overwrite. This is replay under current configuration, not resume of remaining work; successful work can be repeated and newly changed artifacts are not covered by fresh request-level confirmation. Per-artifact runtime guards still exist. | `clio/task_center/manager.py:194`; `clio/ui/routes/run.py:85`, `:594` |
| U04 | P1 | Any successful pipeline task clears and reloads the shared plan buffer, including compression-only tasks. If the user edits a plan during the background task, the completion handler replaces unsaved in-memory work without checking dirty state. A later `selectPlan` navigation guard runs after replacement. | `clio/ui/static/src/runner.js:789` |
| U05 | P2 | The Run event subscriber retains the old task ID and rejects all other IDs. A Task Center retry/new task can run in the backend while the still-mounted Run page ignores it. Returning to Run may recover an active task via the list query, but that is not continuous task handoff. | `clio/ui/static/src/runner.js:603` |
| U06 | P2 | Completion processing is not idempotent. Two different events containing the same terminal task snapshot trigger plan loading and completion side effects twice. The backend stream joins each event to a current task snapshot, so a batch can legitimately contain several terminal snapshots. Sequence deduplication in Task Center does not deduplicate terminal processing by task ID. | `clio/ui/routes/tasks.py:141`; `clio/ui/static/src/runner.js:776` |
| U07 | P2 | Reattachment reads the task phase but does not restore its day into `_lastRunDay`. A page on day1 can attach to a running day2 task and load `/api/plan?day=day1` on completion. Opening Run during an existing task also resets `_lastRunDay` from current page state. | `clio/ui/static/src/runner.js:91`, `:574`, `:626` |
| U08 | P2 | Failure and cancellation refresh the video list but do not refresh the execution preview. Completed partial outputs are therefore absent from the next-action estimate until another preview trigger. The old forced overwrite choice is retained. | `clio/ui/static/src/runner.js:825`, `:840` |
| U09 | P2 | ProcessingState instances cache independent copies and asynchronously replace the same JSON file. A later step can read before an earlier instance flushes, then write the older status back. The probe ends with compression `running` after it was marked `done`. Atomic replacement prevents torn JSON but does not prevent this lost update. This matrix is historical diagnostic data, not an execution checkpoint. | `clio/processing_state.py:48`, `:83`, `:131` |
| U10 | P2 | Empty step lists are accepted by request validation, but `run_pipeline_steps(steps=[])` falls back to every registered step. The normal UI guards empty submissions; API callers still have a surprising execution contract. | `clio/ui/routes/run.py:340`; `clio/pipeline.py:194` |

## Further UX work (not delivered by this repair)

The four-stage display is useful, but it currently groups technical steps without a durable
run session or a single authoritative execution plan. Custom selections persist globally,
whereas custom mode, all-day selection and temporary context do not survive page rendering.
The per-file matrix only displays six of the eight canonical steps and is not task-scoped.
Progress percentages describe the current step and can reset at the next one. The delivery
stage creates planned voiceover and numbered media; cutting and JianYing export remain
separate actions. The UI should make that remaining work explicit.

Recommended user flow:

1. Choose target (current day/all days/selected media) and outputs before execution.
2. Show an execution summary listing reusable, stale, missing, optional and blocked work,
   including concrete reasons and overwrite consequences. Confirmations belong to that plan.
3. Offer **Run remaining work** and **Run next stage** with explicit meanings. Keep manual
   stage execution available; do not silently add costly work when custom dependencies are absent.
4. During execution, show overall stage position plus current-step progress, connection state,
   completed outputs, failed items and cancellation state. Distinguish waiting for an AI request
   from a disconnected progress stream. Current cancellation is cooperative and does not guarantee
   immediate interruption of an in-flight synchronous provider request.
5. On completion or interruption, show a verified output list and three distinct choices:
   **Continue remaining work**, **Retry failed items**, and **Regenerate selected outputs**.
   Preserve edited outputs and leave unrelated editor buffers untouched.

## Original repair order and acceptance criteria

### First: truthful results and safe lifecycle

- Resolve U02 with structured step outcomes (produced/reused/skipped/failed/cancelled), explicit
  dependency policy and an accurately rendered partial result. Decide whether strict failure or
  partial downstream output is appropriate per step. Do not infer success merely from no exception.
- Resolve U04 with artifact/project identity and dirty/version checks before any shared-buffer update.
- Resolve U05-U08 with explicit active-task handoff, task-scoped runtime context, one terminal
  finalization per task, ordered async processing and fresh preview after every terminal outcome.
- Resolve U01 by sharing a read-only compression reuse predicate with runtime, including source
  identity, metadata, settings and artifact validity. Add codec/CRF to the actual fingerprint.
- Reject an empty explicit step list while preserving `None` as the full-pipeline default (U10).

Acceptance: partial analysis cannot appear as complete success; compression completion cannot
replace a dirty plan; sequential tasks/retries and page reloads display the correct task/day;
each terminal task finalizes once; partially completed work changes the next-action estimate.

### Second: explicit recovery semantics

- Resolve U03 by separating resume from forced replay. Resume must recompute outstanding work
  and require new confirmation if overwrites or effective configuration changed.
- Persist a safe run manifest with project identity, scope, steps, effective non-secret settings,
  prompt/context version references and artifact lineage. Do not copy secrets into task history.
  Sensitive temporary inputs need an explicit persistence policy; otherwise retain the existing
  honest retry block.
- Record per-item committed outputs and failures. Verify artifacts on restart before deciding
  which items remain; an in-progress provider call itself is not a resumable checkpoint.
- Resolve U09 with serialized shared state or transactional task/item storage. Keep historical
  processing diagnostics separate from authoritative recovery state.

Acceptance: after interruption between two files, resume reuses valid committed output, retries
only outstanding work, detects configuration drift and protects intervening manual edits. A forced
regeneration remains an explicit separate action.

### Third: validate failure scenarios and refine presentation

Exercise abrupt process termination during ffmpeg, upload and AI generation; shutdown while a
provider call is active; disk-full/permission failures; corrupt artifacts; browser reload and SSE
reconnect; two tabs; project/day switching; optional ASR unavailable; and repeated export.
Existing JianYing destination protection defects R09/R10 remain relevant to end-to-end recovery
but were not re-executed in this focused review.

## Baseline verification

- Existing backend suites: **272 passed**, 24.20 s. Modules: pipeline, run preview/routes,
  Task Center manager/store/concurrency/routes, and shutdown.
- Existing frontend suites: **103 passed**, six files: runner, runner stages/preview,
  Task Center and its lifecycle/actions.
- Offline Python evidence: **five observations reproduced** (U01, U02, U03, U09, U10).
- Offline frontend evidence: **five observations reproduced** (U04-U08).
- Archived evidence files assert pre-fix defects. They are historical probes, superseded by
  production regression tests, and are not passing verification commands for the repaired tree.
- Python evidence Ruff check/format and repository `git diff --check` passed.

Run current regression coverage from the repository root:

```powershell
.venv/Scripts/python.exe -m pytest clio/tests/test_pipeline_recovery.py clio/tests/test_pipeline.py clio/tests/test_routes_run.py clio/tests/test_run_preview.py clio/tests/test_tasks_compress.py clio/tests/test_processing_state.py
npx vitest run clio/ui/static/src/__tests__/runner-recovery.test.js clio/ui/static/src/__tests__/runner.test.js clio/ui/static/src/__tests__/sidebar-data.test.js
```

## Remediation verification and limits

Current regressions use temporary files and mocked provider/media failures while exercising
production routes, task persistence, orchestration and UI handlers. The existing real-ffmpeg
compress-to-analysis smoke also runs in the full backend suite, with only the AI provider faked.
Frontend full suite: **976 passed, 92 files**. Typecheck, Ruff check/format (298 files), mypy
(298 source files), and local `main.py check` passed. The latter checks local configuration and
binary availability without a selected project; it does not validate provider connectivity.
Final backend full suite: **3472 passed, 12 skipped**, 128.69 s, using the same random seed
(`1562327747`) as the initially failing run. The real-ffmpeg smoke passed. The 12 skips are
environment/platform-dependent tests; they are not counted as passes. `git diff --check` passed.

The first full backend run found an existing order-dependent credential test leak: dotenv
tests left process environment and ownership state behind. It produced **3471 passed, one
failed, 12 skipped**; the failing CLI check passed alone. Test isolation now restores both
states, and the exact failing 102-test combination passes (**101 passed, one skipped**).
This changes test isolation only; the credential production implementation is preserved.

No live provider billing, production-footage run, browser-driven visual inspection, abrupt
process-kill/power-loss test, or disk-full fault injection was performed. Interrupted-record
recovery is simulated from durable SQLite state plus committed artifacts. An in-flight AI call
is restarted, not resumed. ProcessingState sharing is in-process; concurrent CLI processes do
not acquire a cross-process artifact lock. Transactional per-item checkpoints, automatic resume,
connection-state UI, and the separate export defects remain outside this repair. These limits
prevent an absolute zero-bug or arbitrary-failure recovery guarantee.

## Pushed-tree CI follow-up

The local counts above included working-tree changes omitted from `b9999e1`: the three
recovery locale keys and credential-test environment/ownership isolation. CI run `43706586669`
on `45ca135` exposed those omissions. It also found two preview fixtures relying on local
ffprobe discovery on Windows/macOS. The follow-up includes the omitted dependencies, updates
overwrite help to match all terminal outcomes, and mocks discovery in those cache fixtures.
An additional regression
checks that an unavailable probe leaves existing output pending and untouched.

Local working-tree success alone did not establish that the pushed tree passed. The follow-up
is verified from a separate checkout of the committed tree before push, then monitored in CI.

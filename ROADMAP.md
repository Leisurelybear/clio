# Roadmap

Feature tracking. Each feature is broken down into minimal executable sub-tasks (per
`AGENTS.md` one feature, one commit). Mark `[ ]` for planned, `[~]` for in-progress, and
`[!]` for blocked work. Completed delivery history is archived rather than duplicated here.

- Historical roadmap details: [`docs/archive/2026-11-12-roadmap-historical-details.md`](docs/archive/2026-11-12-roadmap-historical-details.md)
- Older archive: [`docs/archive/2026-07-01-roadmap-archive.md`](docs/archive/2026-07-01-roadmap-archive.md)
- Release notes and completed changes: [`CHANGELOG.md`](CHANGELOG.md)

## Active backlog (2026-11-13)

### AI provider model discovery (2026-11-16)

- `[x]` Load available models from configured Gemini and OpenAI-compatible providers in the
  AI settings editor, then let users select which models to keep in the task-binding registry.

### Run workflow simplification (2026-11-16)

- `[x]` Keep guided Run mode on the complete four-stage workflow and prevent a prior custom
  step selection from silently changing the next guided run.
- `[x]` Reduce the default Run surface to scope, stage status, and one next action while
  retaining custom steps and detailed execution controls behind expandable sections.
- `[x]` Add a stage-level preview contract so the backend is the authoritative source for
  `next_stage`, blockers, estimates, and output links.
- `[x]` Persist a resumable guided run session with explicit failure recovery and result
  navigation: mode, skipped steps, and scope survive reloads per project, failed runs keep
  the retry handoff, and reusable stages plus completion actions open their output.

### Run UX clarification (2026-11-18)

- `[x]` Show preview failures inside the preview pane with an inline retry action.
- `[x]` Explain blocked stages and the next-stage blockers on the guided Run page.
- `[x]` Add plain-language status descriptions to every stage card.
- `[x]` Label the guided next-stage card with progress and a reassurance line.
- `[x]` Surface execution-option details and cost estimates by default instead of hiding them.
- `[x]` Summarize run outputs on the completion card after a run finishes.
- `[x]` Preserve managed-task result summaries through the Run event adapter.
- `[x]` Label completed output counts as processed instead of pending.
- `[x]` Keep one visible inline preview retry and auto-open the preview on failure.
- `[x]` Unify run cost formatting across the next-stage card and preview.
- `[x]` Surface custom step mode with an inline guided-mode recovery action.
- `[x]` Propagate same-run upstream invalidation through guided Run preview.
- `[x]` Defer plan voiceover behind a same-run plan instead of reporting a final blocker.
- `[x]` Explain cascade risk and minimum estimates in the Run UI.
- `[x]` Confirm consistency/voiceover lineage ordering and fix if unintended.

### Run scope validation (2026-11-19)

- `[x]` Reject consistency and planning for a requested day label that has no analyzed media, and
  block guided Run preview with the requested and available day labels instead of silently
  completing the same work again.

### Pipeline UX and recovery review (2026-11-15)

- `[x]` Review guided execution, task lifecycle, partial failures and recovery semantics;
  publish the [assessment and offline evidence](docs/analysis/2026-11-15-clio-pipeline-ux-reliability-review.md).
- `[x]` Implement truthful outcomes, safe completion updates, task handoff and
  authoritative cache previews (local U01-U08; U01 overlaps comprehensive R01).
- `[x]` Separate guarded cache-based continuation from forced replay; serialize in-process
  processing state and reject explicit empty steps (local U03/U09/U10).
- `[x]` Verify persisted interruption, manager recreation, fast retries, repeated terminal events,
  task-day restoration, dirty-editor preservation and project-switch races.
- `[ ]` Add transactional per-item checkpoints, cross-process artifact ownership and live
  process-kill/provider/disconnection fault validation as a separate reliability project.

Local U01-U10 are repaired with behavioral regressions. Full backend: 3472 passed, 12 skipped;
full frontend: 976 passed. Typecheck, Ruff, mypy and local environment check passed. Evidence and
recovery boundaries are recorded in the assessment. Restart marks interruption; continuation
is explicit and reuses validated artifacts. Existing forced approvals are never replayed.

CI follow-up: the first pushed tree omitted recovery translations and credential-test
environment isolation. Those dependencies are now included; compression preview fixtures
also mock binary discovery so cache tests do not require a locally installed ffprobe.

### Comprehensive review (2026-11-15)

- `[x]` Complete the repository-wide module/architecture review and offline reproductions.
- `[x]` Publish the [final assessment](docs/analysis/2026-11-15-clio-final-review.md),
  [module report](docs/analysis/2026-11-15-clio-module-review.md), and
  [repair/optimization matrix](docs/analysis/2026-11-15-clio-review-matrix.md)
  ([CSV](docs/analysis/2026-11-15-clio-review-matrix.csv)).
- `[~]` Resolve the remaining 6 confirmed P1 defects (R03-R06/R08/R10) with focused regressions;
  R01 is fixed by the pipeline repair and R11 by credential retirement work.
- `[~]` Resolve the remaining 8 confirmed P2 defects (R07/R09/R12-R17); R02 is fixed by the
  pipeline cache repair.
- `[ ]` Schedule the 10 optimization/validation items; O01/O02 overlap historical OPT-022/OPT-023.

### Credential management design (2026-11-15)

- `[x]` Design a unified credential service with field-level UI operations, dotenv compatibility,
  and an OS keyring backend proposal ([design](docs/superpowers/specs/2026-11-15-credential-management-design.md)).
- `[x]` Review the current credential architecture, source precedence, UI/API boundaries, and
  provider cache lifetime ([review](docs/superpowers/reviews/2026-11-15-credential-management-architecture-review.md)).
- `[x]` Implement the credential service contract and dotenv-backed compatibility adapter.
- `[x]` Replace Provider dotenv read/modify/write and the non-functional reveal control with the
  credential API and shared credential field component.
- `[x]` Add OS keyring storage and explicit migration from legacy `api_key_env` / `.env` values.
- `[x]` Restore the shared credential manager's dedicated layout, action styling, and narrow-screen
  responsive behavior with frontend regressions.
- `[x]` Use the shared collapsible settings card for credentials and retain its state across
  inventory refreshes and settings rerenders (979 frontend tests and typecheck passed).

Credential delivery includes graceful provider retirement (R11), atomic reacquisition before
active calls, source/shadowing status, authenticated 30-second reveal, backend-specific cleanup,
and native desktop keyring packaging. Verification and limits:
[implementation record](docs/superpowers/reviews/2026-11-15-credential-management-implementation.md).

Post-implementation [code review](docs/superpowers/reviews/2026-11-15-credential-management-code-review.md)
reproduced five defects; all five are fixed with focused regressions:

- `[x]` Fix credential possession checks for implicit bindings and candidate metadata (CMR-01/02).
- `[x]` Prevent synthesized credential ID collisions between distinct providers (CMR-03).
- `[x]` Preserve effective credential selection during explicit-source migration (CMR-04).
- `[x]` Roll back backend mutations when metadata persistence fails (CMR-05).

The reviewed commits contain no detected real keys. A separately identified historical key from
2026-06-10 remains reachable; confirm revocation before considering any authorized history cleanup.

### AI provider settings usability (2026-11-15)

- `[x]` Allow testing an unsaved provider draft from the add/edit dialog.
- `[x]` Allow provider renaming and update default task bindings when a provider name changes.
- `[x]` Preserve credential-target confirmation semantics while generating the new environment binding on rename.

The comprehensive review baseline was `aef5b0a` and initially recorded 17 open defects.
R01/R02 are resolved by the pipeline remediation above; R11 was resolved by credential work.
Historical review-only verification: 3409 pytest passed / 12 skipped; final preview module 70 passed;
951 Vitest passed; 13 Python observations and 4 frontend defect probes reproduced; Ruff,
mypy, frontend typecheck and local `main.py check` passed. Full evidence and limits are in the
[verification record](docs/analysis/2026-11-15-clio-review-evidence/verification.md).
The dated matrix is authoritative for these new findings; the original 117-item tracker
retains its own historical scope and statuses.

### Follow-up review (2026-11-14)

Evidence and reproductions: [recent commits review](docs/analysis/2026-11-14-recent-commits-review.md).

- `[x]` Share cache-reuse predicates between guided previews and runtime execution.
- `[x]` Rediscover day labels after analysis before each all-day scoped step.
- `[x]` Allow disabled optional transcription without blocking guided progression.
- `[x]` Forward the effective transcript option to pipeline analysis.
- `[x]` Preserve a locally valid planning draft when optional review fails.

Completed verification: 3409 pytest passed (12 skipped), 946 Vitest passed;
frontend typecheck, Ruff check/format, mypy, and `main.py check` passed.
Commit-level evidence and remaining scope limits are recorded in the review report.

### Run interaction refresh (2026-11-14)

- `[x]` Resolve shared voiceover runtime phases against active task steps (2026-11-15).
- `[x]` Apply overwrite to one submission and refresh normal cache state after success (2026-11-15).
- `[x]` Offer an explicit optional-transcription skip when ASR cannot produce transcripts (2026-11-15).
- `[x]` Verify changed upstream analysis invalidates all four downstream preview caches (2026-11-15).

Follow-up verification: 951 Vitest tests, 70 run-preview pytest cases, frontend typecheck,
focused Ruff check/format, and diff checks passed. See the
[Run interaction review](docs/analysis/2026-11-14-run-interaction-review.md) for acceptance rules.

- `[x]` Group the eight existing steps into four guided stages, with one-stage next actions.
- `[x]` Keep custom selections, scope, overwrite confirmations, and detailed estimates available.
- `[x]` Show live stage progress and retain the Run page after completion with explicit result actions.
- `[x]` Cover stage selection, missing inputs, custom execution, preview recovery, and completion navigation with frontend tests.

### P0/P1/P2 review remediation

Historical source: [`docs/analysis/2026-11-05-P0-P2-Issues.md`](docs/analysis/2026-11-05-P0-P2-Issues.md)

Current source: [`docs/analysis/2026-11-13-clio-open-items.csv`](docs/analysis/2026-11-13-clio-open-items.csv).
Item-level status and evidence: [`docs/analysis/2026-11-13-clio-open-items-status.md`](docs/analysis/2026-11-13-clio-open-items-status.md).

The earlier 83-item review is complete. The 2026-11-13 review adds a fresh 117-item audit;
its tracker is authoritative for current remediation work.
O17 and O18 are complete as of this cleanup. The analysis document remains the source of truth for item-level
status and verification notes.

P1 items:

- `[x]` O1 native structured output (`response_schema` / `response_format`)
  - `[x]` add Pydantic schemas for analysis, voiceover, planning, and window summaries
  - `[x]` send Gemini JSON schema and OpenAI-compatible `response_format` with fallback
- `[x]` Q4 use GPMF capture time and GPS in planning by default (`7189ede`)
- `[x]` Q5 use Gemini video metadata and `media_resolution` (`304acd3`)
  - `[x]` send `VideoMetadata` start/end offsets and `fps=3` for logical analyze windows
  - `[x]` add per-project `analyze.media_resolution` (`low | medium | high`, default `medium`)
  - `[x]` remove physical slicing from the analyze path and invalidate stale window caches
- `[x]` Q6 shot-level structured analysis attributes and scene boundaries (`c5600d7`, `336824c`)
- `[x]` Q7 same-location entity and duplicate-scene consistency (`b141fb6`, `8292a0c`, `cfc3c81`, `535146b`)
  - `[x]` generate validated per-day entity and duplicate-scene JSON/Markdown reports
  - `[x]` inject current reports into planning and invalidate stale plan caches
  - `[x]` expose the consistency stage in run preview and the Web UI runner
- `[x]` Q11 outline -> shot selection -> review planning loop (`c3156ba`, `d47829d`)
  - `[x]` add typed outline and review schemas plus prompt override names
  - `[x]` normalize outlines into bounded narrative beats and candidate shot lists
  - `[x]` run shot selection from the outline, then validate and review the final plan with one correction retry
  - `[x]` invalidate plan lineage when any planning-stage prompt or the outline context changes
- `[x]` Q12 generate voiceover from the planned segments after planning (`89b9315`, `9da2483`)
- `[x]` R006 day-scoped planning defaults and all-day Web UI entry (`ed63199`, `ab690d1`)
- `[x]` F1 create a small scored evaluation set and scoring script (`98b4f4d`)
  - `[x]` define a versioned 12-case manifest and reference-label schema
  - `[x]` add deterministic scoring for coverage, density, fact recall, key moments, and structure
  - `[x]` emit JSON and Markdown reports with an optional minimum-score gate
  - `[x]` run the harness against real project footage and record the first baseline (`98b4f4d`; mean 96.92, minimum 87.51, gate 70 passed)

P2 items:

- `[x]` O8 split `ui/server.py` handler construction, auth, body parsing, and routes
  - `[x]` extract the declarative route table into `clio/ui/route_table.py`
  - `[x]` extract authentication and local-session gates into `clio/ui/handler_security.py`
  - `[x]` extract JSON body parsing into `clio/ui/request_body.py`
  - `[x]` extract handler construction from `make_handler` (`961fdb1`)
- `[x]` O14 add frontend type checking and decompose the largest JS modules (`e5903ac`)
  - `[x]` add `typescript`, `tsconfig.json`, `npm run typecheck`, and a CI gate for checked utility modules
  - `[x]` decompose `editor-config.js` into focused panes, a form renderer, and a page shell
  - `[x]` extract the plan insert dialog into `editor-plan-insert.js`
  - `[x]` extract the plan readiness panel into `editor-plan-readiness.js`
  - `[x]` extract run preview rendering and overwrite checks into `runner-preview.js`
  - `[x]` extract skipped diagnostics and processing-state rendering into `runner-diagnostics.js`
  - `[x]` extract plan deletion and save/config helpers into `editor-plan-persistence.js`
  - `[x]` extract plan undo/redo state wiring into `editor-plan-history.js`
  - `[x]` extract plan subtitle prefill into `editor-plan-subtitle.js`
  - `[x]` extract sidebar offline summary, filter chips, stage counts, and display helpers
  - `[x]` extract sidebar video-row rendering and action-menu behavior into `sidebar-video-row.js`
  - `[x]` extract runtime warning probes and actions into `runtime-warning-actions.js`
  - `[x]` extract viewer plan-accordion sync, waveform index keys, and subtitle settings
  - `[x]` keep every JavaScript module under 30 KB
- `[x]` O16 raise Vitest coverage around bug fixes and save flows (`5487304`)
- `[x]` Q13 snap cut points to shot boundaries and sentence endings (`0e1c9bf`)
- `[x]` F4 calculate AI output quality signals and surface warnings in the UI
  - `[x]` collect analyzed source timelines and detect empty/large-gap/out-of-bounds analysis coverage
  - `[x]` flag plan ranges outside analyzed content, duration drift, and missing voiceover guidance
  - `[x]` surface the new issues through the existing readiness panel and add regression tests
- `[x]` F9 review transition candidates with a Gemini contact sheet (`ad40bf0`)
  - `[x]` add opt-in `plan.visual_transition_review` (default off) and use the `video_analyze` Gemini binding
  - `[x]` review at most 12 adjacent transitions from compressed-first frame pairs and update only `transition`
  - `[x]` preserve the validated plan on extraction/provider/JSON failure and include review settings in lineage
- `[x]` C10 verify multi-window timestamp offsets with a real 15 fps video
  - six logical windows on a real stream-copied 15 fps clip retained source-relative timing; frame at slice 2.000s matched source 282.000s exactly
- `[x]` D12 compare the JianYing draft with a real sample draft
  - real-project export produced parseable content/meta files, source fps propagation, matching durations, populated platform fields, and clamped segment ranges

### R-048 Higher-detail video analysis and bounded plan input

**Status:** Complete through P2b (2026-11-13). Media resolution, structured significance fields, the prompt density contract, coverage observation, sparse-window completion retry, bounded plan-input projection, deterministic two-stage candidate shortlisting, and non-mutating compress width/fps recommendations are implemented.

**Goal:** Raise per-clip analysis density from summary level (~48 s per timeline row) to 8-12 s per
row, keep the extra detail in `texts/*.json` for voiceover/cut/preview, and stop that detail from
inflating the plan prompt. Old analysis JSON must remain usable without migration.

**Measured problem (France project, 142 clips / 466.5 min):** 579 timeline rows, 4.1 rows per clip,
48.3 s per row; the 26.7-min clip 009 produced only 12 rows (133 s per row). `video_analyze`
`prompt_tokens` is almost exactly `64 × video seconds + prompt`, so Gemini runs at
`media_resolution` LOW. The default 15-min window dilutes attention; `ANALYZE_PROMPT` asks for
5-10 s rows but also caps at 60 rows with 2-3 sentences each and sets no minimum coverage. Nothing
checks coverage afterward, and the plan task sends the full timeline plus inline transcripts.

**Config-only mitigation verified (2026-10-28):** with only `analyze.window_max_min: 5`,
`202610_Italy/038_GX011297` (26.6 min) produced 63 rows across 6 windows, 25.4 s per row, and no
gap >1 s (before: 12 rows / 133 s per row). This proves window size is one lever, but rows are still
~47 chars and only ~10.5 rows per 5-min window, so the prompt contract and visual resolution are
still required. Only that one file has been re-run; the rest of the project is still old output and
needs `analyze --force`.

Design: `docs/superpowers/specs/2026-10-24-analysis-detail-and-plan-input-budget-design.md`

- [x] P0a `feat(analyze)`: add project `analyze.media_resolution` (`low | medium | high`, default
  `medium`) and pass it through `VideoAIProvider.analyze_video` to the Gemini video call only, never
  to text tasks; add `media_resolution`, `window_max_min`, `window_overlap_sec` to the analysis
  lineage fingerprint so cached coarse results are invalidated (`304acd3`)
- [x] P0a: config schema/descriptions/UI select, example files, `low` fallback guidance, and en/zh
  locale keys (`304acd3`)
- [x] P0a: emit a one-time analyze warning when `compress.fps < 2` and `media_resolution != low`
- [x] P0b `feat(analyze)`: replace the fixed 60-row cap with a duration-based density contract
  (<=5 min 25-40 rows, 5-10 min 50-80, 10-15 min 75-120; 8-12 s per row, one sentence <=40 chars)
- [x] P0b: add optional `score` (1-5), `camera`, `quality`, `tags` per timeline row and top-level
  `key_moments` (3-8 per clip); clamp/validate them, dedupe and merge across windows, and keep old
  JSON compatible
- [x] P0b: log `[analyze] <name>: rows=N coverage=xx% max_gap=Ns` after each clip (observation only,
  no retry in P0)
- [x] P0c `feat(plan)`: add `clio/plan_input.py::build_plan_clips()` (top-K timeline + key_moments,
  no inline transcripts) with `plan.max_timeline_rows_per_clip` (8) and
  `plan.max_key_moments_per_clip` (6)
- [x] P0c: validate `use_timeline` and transcript selection against full clips while serializing
  only the projection; include the new plan bounds in the plan lineage
- [x] P1 (optional) two-stage candidate shortlist: `plan.max_candidate_clips` (60),
  `PLAN_SHORTLIST_PROMPT`, deterministic local fallback (`47703f8`)
- [x] P2a (optional) sparse-window completion retry: one largest-gap retry below 80% coverage or for gaps >=90s, merged back into the window and cached (`b7a8de6`)
- [x] P2b (optional) `compress` width/fps recommendation based on analysis requirements (`6880480`)

**Acceptance:** at `medium` the same clip uses 3.5-4.5x the visual prompt tokens of `low`; new rows
average <=20 s and <=40 chars; `key_moments` 3-8 per clip; France plan input <60k tokens after P0c and
<30k after P1; old `texts/*.json` still plans without migration; changing resolution/window
invalidates the analysis cache; pytest, Vitest, Ruff, and the full mypy gate stay green.

**Risks:** `medium` costs ~4x visual tokens (a 15-min window goes from ~58k to ~230k), so recommend
`medium` + `window_max_min: 5`; 640p still caps real detail, so raise `compress.max_width` to
960-1280 only if sign/food detail matters; project prompt overrides do not inherit the new contract;
every existing project needs one forced re-analysis.

### R-047-FU Optional Baidu ASR provider

**Status:** Deferred; not scheduled.

**Goal:** Add Baidu as a second cloud provider only if users need a vendor beyond Aliyun.

- [ ] Implement the Baidu HTTP provider and token cache behind the existing registry
- [ ] Add provider readiness, retry, cancellation, parsing, and contract tests
- [ ] Add setup documentation and verify end-to-end with a real credential

**Decision:** Deferred in the original R-047 delivery. The registry already supports additive
providers, so this does not block Aliyun cloud transcription or packaged-build use.

### R-024 GoPro GPMF telemetry highlight signal

**Status:** Open, not scheduled. Related to Q4.

- [ ] Parse GoPro GPMF telemetry timestamps (speed, elevation, location)
- [ ] Feed highlight windows into the analysis prompt
- [ ] Keep telemetry optional and backward-compatible for clips without GPMF data

## Deferred by choice

| Item | Reason |
| --- | --- |
| JianYing real draft version compatibility | Version-specific, not generic |
| Per-segment AI regenerate on plan rows | User declined; structural edit and full re-plan cover the need |
| Serve-time silent ffmpeg download | Explicit user/setup action only; never auto-download on serve |
| Auto-migrate multi-seg artifacts to single identity | Legacy read-only path covers old projects |

## Archive policy

Completed feature sections, old review iterations, and dated delivery notes are preserved in the
roadmap archive above. New completed changes should be recorded in `CHANGELOG.md`; `ROADMAP.md` is
limited to active, in-progress, blocked, and explicitly deferred work.

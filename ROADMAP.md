# Roadmap

Feature tracking. Each feature is broken down into minimal executable sub-tasks (per
`AGENTS.md` one feature, one commit). Mark `[ ]` for planned, `[~]` for in-progress, and
`[!]` for blocked work. Completed delivery history is archived rather than duplicated here.

- Historical roadmap details: [`docs/archive/2026-11-12-roadmap-historical-details.md`](docs/archive/2026-11-12-roadmap-historical-details.md)
- Older archive: [`docs/archive/2026-07-01-roadmap-archive.md`](docs/archive/2026-07-01-roadmap-archive.md)
- Release notes and completed changes: [`CHANGELOG.md`](CHANGELOG.md)

## Active backlog (2026-11-13)

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

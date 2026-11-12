# Roadmap

Feature tracking. Each feature is broken down into minimal executable sub-tasks (per
`AGENTS.md` one feature, one commit). Mark `[ ]` for planned, `[~]` for in-progress, and
`[!]` for blocked work. Completed delivery history is archived rather than duplicated here.

- Historical roadmap details: [`docs/archive/2026-11-12-roadmap-historical-details.md`](docs/archive/2026-11-12-roadmap-historical-details.md)
- Older archive: [`docs/archive/2026-07-01-roadmap-archive.md`](docs/archive/2026-07-01-roadmap-archive.md)
- Release notes and completed changes: [`CHANGELOG.md`](CHANGELOG.md)

## Active backlog (2026-11-12)

### P0/P1/P2 review remediation

Source: [`docs/analysis/2026-11-05-P0-P2-Issues.md`](docs/analysis/2026-11-05-P0-P2-Issues.md)

Snapshot: 83 items; 71 completed locally; 1 partial; 2 awaiting real-environment verification; 9 pending.
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
- `[ ]` Q6 shot-level structured analysis attributes and scene boundaries
- `[ ]` Q7 same-location entity and duplicate-scene consistency
- `[ ]` Q11 outline -> shot selection -> review planning loop
- `[ ]` Q12 generate voiceover from the planned segments after planning
- `[~]` F1 create a small scored evaluation set and scoring script
  - `[x]` define a versioned 12-case manifest and reference-label schema
  - `[x]` add deterministic scoring for coverage, density, fact recall, key moments, and structure
  - `[x]` emit JSON and Markdown reports with an optional minimum-score gate
  - `[ ]` run the harness against real project footage and record the first baseline

P2 items:

- `[ ]` O8 split `ui/server.py` handler construction, auth, body parsing, and routes
- `[ ]` O14 add frontend type checking and decompose the largest JS modules
- `[ ]` O16 raise Vitest coverage around bug fixes and save flows
- `[ ]` Q13 snap cut points to shot boundaries and sentence endings
- `[x]` F4 calculate AI output quality signals and surface warnings in the UI
  - `[x]` collect analyzed source timelines and detect empty/large-gap/out-of-bounds analysis coverage
  - `[x]` flag plan ranges outside analyzed content, duration drift, and missing voiceover guidance
  - `[x]` surface the new issues through the existing readiness panel and add regression tests
- `[ ]` F9 review transition candidates with a Gemini contact sheet

Awaiting real-environment verification:

- `[~]` C10 verify multi-window timestamp offsets with a real 15 fps video
- `[~]` D12 compare the JianYing draft with a real sample draft

### R-048 Higher-detail video analysis and bounded plan input

**Status:** Complete through P0c (2026-11-12). Media resolution, structured significance fields, the prompt density contract, coverage observation, and bounded plan-input projection are implemented. Optional P1/P2 improvements remain separate backlog candidates.

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
- [x] P0a: emit a one-time analyze warning when `compress.fps < 2`
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
- [ ] P1 (optional) two-stage candidate shortlist: `plan.max_candidate_clips` (60),
  `PLAN_SHORTLIST_PROMPT`, deterministic local fallback
- [ ] P2 (optional) sparse-window completion retry and `compress` width/fps recommendation

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

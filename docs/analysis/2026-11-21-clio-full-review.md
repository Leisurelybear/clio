# CLIO Full Review (2026-11-21)

**Type:** Read-only repository review. No source file, artifact, or project data was modified.
**Baseline:** working tree on top of commit `2a5c2c6` with 43 modified and 29 untracked files
(uncommitted); findings describe the working tree, not `HEAD` alone.
**Scope:** CLI/pipeline, AI/ASR, media identity and artifacts, planning/export, task runtime,
Web backend and frontend, tests, release tooling, documentation hygiene.
**Related documents:**
[comprehensive matrix (2026-11-15)](2026-11-15-clio-review-matrix.md),
[capture-day scope audit (2026-11-19)](2026-11-19-capture-day-scope-audit.md),
[open-items tracker (2026-11-13)](2026-11-13-clio-open-items-status.md).

## Verdict

The project is a mature, heavily tested pipeline with a clear module layout and strong
correctness intent. The working tree, however, **fails three of the five required gates today**
(`ruff format --check`, `mypy`, and one Vitest case), so it cannot be committed to CI as-is.
Beyond gate failures, this review adds **two P1 data-correctness defects** in the shared
media-identity contract (cut and export disagree with plan/preview on which source a stale
analysis index points at) and several smaller maintainability and hygiene gaps.

No P0 was established; this review does not claim that no P0 exists.

## Verification snapshot

Run against the working tree; Python from `.venv` (3.11.9), Node 24.18.

| Gate | Command | Result |
| --- | --- | --- |
| Ruff check | `ruff check clio main.py` | pass |
| Ruff format | `ruff format --check clio main.py` | **fail** — 4 files would reformat |
| mypy | `.venv/Scripts/python.exe -m mypy --check-untyped-defs --show-error-codes` | **fail** — 4 errors |
| Vitest (focused) | `npm test -- --run clio/ui/static/src/__tests__/editor-config.test.js` | **fail** — 1 of 90 |
| Frontend typecheck | `npm run typecheck` | not re-run this pass |
| Backend pytest | `pytest clio/tests/` | **not completed** — collection reached 3574 items; rerun required |
| Diff whitespace | `git diff --check` | pass (CRLF warnings only) |

Notes:

- The `python` on `PATH` is 3.10.6 and fails collection on `from datetime import UTC`.
  Always use `.venv/Scripts/python.exe` (3.11.9) for pytest/mypy.
- The full backend pytest run was interrupted by the tool channel, not by a test failure.
  It must be re-run to confirm the suite is green before committing.

## CI blockers (fix before any merge)

| ID | Priority | Area | Problem | Location | Effort |
| --- | --- | --- | --- | --- | --- |
| V01 | P1 | Formatting gate | Four files are not `ruff format` clean. | `clio/config/models.py`; `clio/config/validators.py`; `clio/tests/test_plan_readiness.py`; `clio/ui/routes/config_routes.py` | S |
| V02 | P1 | Typing gate | `Path-or-None` used as `Path` in tests. | `clio/tests/test_tasks_cut.py:72`; `clio/tests/test_run_preview.py:2536`, `:2538`, `:2545` | S |
| V03 | P1 | Frontend gate | Price-card assertion still expects the retired `USD / 1M` label; the card now renders a "兼容回退价 … / 1M" hint. | `clio/ui/static/src/__tests__/editor-config.test.js:379`; `clio/ui/static/src/editor-providers.js` | S |
| V04 | P1 | Suite assurance | Backend suite result unknown for this tree; it must be re-run to completion. | `clio/tests/` | S |

## New findings

| ID | Priority | Area | Problem | Location | Effort |
| --- | --- | --- | --- | --- | --- |
| [N01](#n01) | P1 | Media identity / cut | A stale plan index silently cuts the wrong current clip when a different source now owns that index; the identity alias is only consulted when the index has no current match. | `clio/tasks/cut.py:462` (alias path `:474`); `clio/tasks/cut.py:402` | M |
| [N02](#n02) | P1 | Media identity / export | Export builds `index -> source_stem` straight from analysis JSON and lets a duplicate index silently overwrite, so it can point a clip at the wrong original even though cut/preview deduplicate by identity. | `clio/export/jianying.py:103`; `clio/export/jianying.py:203` | M |
| [N03](#n03) | P2 | Preview performance | One preview request performs several independent full-directory scans (all-day scope resolution plus two plan-clip collections that each re-derive media aliases). | `clio/ui/services/run_preview.py:1568` (`:1578`), `:1345`, `:1421`; `clio/tasks/plan.py:446` | M |
| [N04](#n04) | P2 | Identity normalization | The same segment-index normalizer is duplicated, and media-identity keys are reimplemented in four places with subtly different shapes. | `clio/index.py:94`; `clio/plan_readiness.py:700`, `:713`; `clio/run_scope.py:151`; `clio/tasks/cut.py:402` | S |
| [N05](#n05) | P3 | Plan diagnostics | Dropping a superseded same-media analysis row reports the "media missing" reason, which is wrong and misleads the UI. | `clio/tasks/plan.py:543` | S |
| [N06](#n06) | P3 | Media URL security | The API token is appended to `/api/video?...&token=`, leaking it into access logs, browser history, and `Referer`, although the API already authenticates via the `Authorization` header. | `clio/ui/static/src/sidebar.js:44`; `clio/ui/static/src/video-url.js:16` | S |
| [N07](#n07) | P3 | Typecheck coverage | New frontend modules are outside `tsconfig.json`'s include list, and one has no `// @ts-check`, so they are never type-checked. | `tsconfig.json` (include); `clio/ui/static/src/plan-video.js`; `clio/ui/static/src/editor-model-prices.js` | S |

### N01 - Stale plan index can cut the wrong clip

**Problem.** `_resolve_cut_media` prefers any current compressed file that carries the plan's
index and only falls back to a `MediaIdentity` alias when no current file uses it. When a project
is re-indexed, a plan that references the old index `016` may now collide with an unrelated
current `016_Other.mp4`; the legacy alias (`016 -> GX011275`, currently `197_GX011275.mp4`) is
never consulted, so the plan silently cuts a different source.

**Evidence.** `clio/tasks/cut.py:462-495` resolves candidates by index before aliases; the
existing regression `clio/tests/test_tasks_cut.py:695`
(`test_exact_current_index_wins_over_legacy_alias`) **codifies** this behavior with no conflict
diagnostic.

**Impact.** Wrong footage in a delivered cut, with no warning. This breaks the AGENTS contract
that plan, cut, preview, and export agree on the same media identity.

**Recommended fix.** When a plan index resolves to a current compressed file whose identity does
not match the plan's analysis identity, treat it as a conflict: skip and report the clip with an
explicit reason instead of cutting the unrelated source. Invert the current test into a
conflict-expectation regression.

### N02 - Export rebuilds identity from raw analysis indexes

**Problem.** `_build_index_to_source` reads every `texts/*.json` and writes `mapping[key] = stem`
for each expanded index key, so two analyses that share an index overwrite each other in
sorted-filename order. Export never consults `media_identity`/analysis-index deduplication, so
the material it wires can differ from the clip that cut produced.

**Evidence.** `clio/export/jianying.py:103-150` (silent `mapping[key] = stem`), consumed by
`_build_materials` at `clio/export/jianying.py:203`. No test asserts identity agreement between
cut and export.

**Impact.** Export can reference the wrong original for repeated/deduplicated indexes, violating
the same identity contract as N01.

**Recommended fix.** Route export through the same `MediaIdentity`/alias resolution used by
`tasks/cut.py` (or a shared resolver), deduplicate by identity, and add a cut-vs-export identity
agreement test.

### N03 - Preview re-scans the project several times per request

**Problem.** A preview request calls `_analysis_jsons`, which runs
`resolve_run_scope(all_days=True)` over every JSON, and then calls `_collect_plan_clips` for the
consistency and plan stages; each of those re-derives media aliases via
`collect_media_index_aliases`. Plan and export readiness endpoints repeat the same collectors.

**Evidence.** `clio/ui/services/run_preview.py:1568` and `:1578`, `:1345`, `:1421`;
`clio/tasks/plan.py:446`; readiness collectors at `clio/ui/routes/plan.py:303`, `:352` and
`clio/ui/routes/export.py:169`.

**Impact.** Against the reference project (362 analysis JSONs / 181 compressed files) each preview
triggers several full scans, some touching GPMF/container/ffprobe date probes on network shares.

**Recommended fix.** Compute scope and aliases once per request and thread them into the clip and
readiness calls (cut/plan already accept a `media_aliases` argument), or add a short-TTL cache keyed
by a directory fingerprint.

### N04 - Identity helpers are duplicated

**Problem.** `_normalize_segment_index` is line-for-line identical in `clio/index.py:94` and
`clio/plan_readiness.py:700`. Media-identity keys are re-derived with different shapes in
`plan_readiness._media_identity_key` (`clio/plan_readiness.py:713`), `run_scope._key_parts`
(`clio/run_scope.py:151`), and `cut._legacy_media_owner` (`clio/tasks/cut.py:402`).

**Impact.** Divergent identity logic is exactly what produced N01/N02; a shared contract prevents
future drift.

**Recommended fix.** Collapse these into `clio/identity.py` and re-point callers.

### N05 - Plan dedupe reports the wrong reason

**Problem.** When an older analysis row is dropped because a current row for the same compressed
media already exists, the skip reason reuses `task.plan.media_missing_reason`
(`clio/tasks/plan.py:543`) even though the media is present.

**Recommended fix.** Add a distinct "superseded by current analysis" locale key (en + zh-CN) and
use it for the dedupe branch.

### N06 - Token carried in media URLs

**Problem.** `sidebar.js` and `video-url.js` append `api_token` as a query parameter to
`/api/video?...&token=`. Query tokens leak through access logs, browser history, and `Referer`,
while the API otherwise uses the `Authorization` header.

**Recommended fix.** Authenticate media requests with the header (or a short-lived opaque signed
URL) and remove the `token` query parameter.

### N07 - Uncovered frontend modules

**Problem.** `tsconfig.json` includes only ten modules. `plan-video.js` and
`editor-model-prices.js` are outside it; `plan-video.js` has no `// @ts-check`, so both escape
`npm run typecheck`.

**Recommended fix.** Add them to `include` (or switch to a glob) and add `// @ts-check`.

## Carried findings re-verified

The 2026-11-15 matrix remains authoritative for full problem statements. Re-checked against the
current tree; R01/R02/R11 are fixed and are not listed.

| ID | Priority | Current status | Re-verification note | Location |
| --- | --- | --- | --- | --- |
| R03 | P1 | Open | Reads JSON, runs the AI call, then `save_with_version(p, raw)` with **no** `expected_version`; `_mark_busy(False)` runs in `finally` before the write. | `clio/ui/routes/refine.py:66`, `:74`, `:84`, `:87` |
| R04 | P1 | Open | Refine response assigns `state.texts/voiceover = r.data` unconditionally; a late response for video A overwrites video B's buffer. | `clio/ui/static/src/editor-refine.js:53-56` |
| R05 | P1 | Open | `selectPlan` has no request-generation guard (unlike `selectVideo`); a slow earlier day can replace the current plan/preview. | `clio/ui/static/src/sidebar.js:104` |
| R06 | P1 | Open | `_saveGeneration` increments only when a save starts; success calls `clearDirty()` even if the user edited during the request. | `clio/ui/static/src/editor-plan-persistence.js:87`, `:115`, `:164` |
| R07 | P2 | Open | Web readers still hard-code `texts`/`scripts`/`compressed` subdirectories. | `clio/ui/services/file_service.py:626` |
| R08 | P1 | Open | `resolve_texts`/`resolve_in` validate only the basename; `is_file()` follows symlinks with no containment check. | `clio/ui/services/file_service.py:615`, `:626` |
| R09 | P2 | Open | Auto-copy copies only `draft_content.json`; `draft_meta_info.json` and `subtitles.srt` are omitted. | `clio/ui/routes/export.py:45` |
| R10 | P1 | Open | Auto-copy writes to shared `vlog_export_<day>` and overwrites with `write_bytes_atomic`; no confirmation or backup. | `clio/ui/routes/export.py:60-69` |
| R12 | P2 | Open | `FileTokenUsageStore` locks per instance; each refine builds a new store for the same file, so concurrent writes lose records. | `clio/ai/token_usage.py:128-131` |
| R13 | P2 | Not re-checked | JSON plan/refine saves still do not refresh Markdown/sidecar companions (per matrix). | `clio/ui/services/file_service.py` |
| R14 | P2 | Open | `run_check` requires a non-placeholder key for **every** bound provider, so keyless Ollama providers fail `main.py check`. | `clio/main.py:140` |
| R15 | P2 | Open | `.vindex` is named by `source_stem`; two same-basename sources overwrite one index. | `clio/vmeta.py:160` |
| R16 | P2 | Open | Freshness compares `int(st_mtime)` + size, so same-second equal-length edits reuse stale artifacts. | `clio/vmeta.py:62`, `:111`, `:154`, `:224` |
| R17 | P2 | Open | Media URLs carry the project name but omit `project_dir`; overlaps N06's URL surface. | `clio/ui/static/src/video-url.js` |

Optimization items: **O07 confirmed** - `ConfigCache.get` returns cache hits without refreshing
order, so eviction is insertion-order, not LRU (`clio/ui/services/config_cache.py:28-78`).
**O09 confirmed** - `gotchas.md` 9.7 still claimed Gemini uploads are never deleted and
`maintenance-instructions.md` claimed loader-time default injection; both were corrected while
writing this report. O01-O06, O08 and O10 were not re-measured.

## Repository hygiene

| ID | Problem | Detail |
| --- | --- | --- |
| H01 | `ROADMAP.md` links to untracked documents | `2026-11-15-clio-final-review.md`, `-module-review.md`, `-review-matrix.md/.csv`, both `*-evidence/` directories, and the `2026-11-14-run-ux-mockup/` assets are still `??`; links are dead after a clone. |
| H02 | Stray files at repo root | `.d10.patch`, `.tmp-cut*.patch`, 11 `.tmp_*.py`, `.tmp_plan_browser_*.png`, `.tmp_plan_video_regression.patch`, `x.txt`, `pelican-bicycle.svg`. `.gitignore` only covers `*.tmp`, so `.tmp_*` slips through. |
| H03 | Uncommitted work spans unrelated features | 43 modified files cover capture-day aliases, model pricing, Provider UI, and styles; they must be split one-feature-per-commit per `AGENTS.md`. |
| H04 | Untracked review docs not committed | The review documents this report cites (H01) plus this report must be added to version control for the links to resolve. |

## Suggested sequencing

1. **Make CI green (V01-V04):** format 4 files, fix the 4 mypy errors, update the stale Vitest
   price assertion, re-run backend pytest to completion. Commit docs + remove `.tmp_*` and widen
   `.gitignore` (H01/H02/H04).
2. **Split the dirty tree (H03)** into capture-day alias, model-price, and Provider-UI commits.
3. **P1 data correctness:** N01, N02 (shared identity resolution for cut + export), then R06,
   R03, R04, R10 - each with a behavioral regression.
4. **P2:** N03 (single scan per preview), N04 (shared identity helpers), R12 (file lock),
   R16 (`st_mtime_ns`), R07/R08 (containment).
5. **P3:** N05, N06 (header auth for media), N07 (tsconfig glob + `checkJs`), O07 (real LRU),
   O04 (pinned release deps).

## Method and limits

- Findings combine a static read of the cited lines, targeted greps, and the gate runs above.
- No live provider call, real-footage cut, JianYing import, or process-kill test was run.
- The backend suite did not finish, so pass/fail is reported as unknown rather than green.
- R13 and the O-rows other than O07 were not re-measured; treat their status as carried from the
  2026-11-15 matrix.


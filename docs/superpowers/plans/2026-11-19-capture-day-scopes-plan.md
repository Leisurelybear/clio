# Capture-Day Scope Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make capture-day scope identity stable across analysis, preview, execution, artifact
reuse, completion, and next-stage selection for both existing and new projects; preserve legacy
`dayN` artifacts without treating `dayN` as the primary identity.

**Architecture:** Add a server-owned scope resolver and manifest, deduplicate analysis artifacts by
media identity, initialize an immutable greenfield mode before the first analysis write, persist
additive scope metadata, bind preview/start to one input fingerprint, and bind completion to the
task-returned scope. Keep legacy
`dayN` filenames in phase 1, but do not use the legacy label-only path for greenfield projects.

**Tech Stack:** Python 3.11+, dataclasses, JSON, ffprobe, pytest, stdlib Web UI, ES modules, Vitest

**Design:** [`2026-11-19-capture-day-scopes-design.md`](../specs/2026-11-19-capture-day-scopes-design.md)

**Self-review:** [`2026-11-19-capture-day-scopes-review.md`](../reviews/2026-11-19-capture-day-scopes-review.md)

**Delivery rule:** Each phase is independently testable and gets its own small commit. Do not push
without explicit user approval.

**Status (2026-11-19):** Phase 1 reference-project reproduction is committed. The read-only audit
lives in [`../../analysis/2026-11-19-capture-day-scope-audit.md`](../../analysis/2026-11-19-capture-day-scope-audit.md);
the deduplication, greenfield-scope, and interrupted-analysis fixtures listed under Phase 1 Steps
1-2 are intentionally completed inside Phases 2-3, where the APIs they exercise exist. Phase 1
checkboxes below are ticked only for work that has actually landed.

---

## Phase 1: Reproduce the regression and record the reference audit

**Purpose:** Prove the current duplicate-count, missing-label, and endless-same-stage behavior
before changing production behavior, and prove that a fresh project would not inherit the same
failure. Keep the reference-project audit read-only.

**Files:**
- Add `clio/tests/test_run_scope_identity.py`
- Extend `clio/tests/test_run_preview.py`
- Add `docs/analysis/2026-11-19-capture-day-scope-audit.md`
- Read only: `D:\vlog_projects\202610_Italy`

- [ ] **Step 1: Add a synthetic project fixture**

Create a legacy fixture with:

- two original media identities on two capture dates;
- two analysis JSON files for each identity, with different indices/compressed stems;
- one artifact with a non-empty `day_label` and one missing label per duplicate pair;
- one legacy-only identity with an unambiguous `day3` label and no recoverable date;
- one undated and unlabeled identity.

Create a separate greenfield fixture with:

- no scope manifest and no legacy plans or consistency reports;
- one current media identity with a recoverable capture date;
- one current media identity with no recoverable date;
- a first-run path through analysis, preview, start, and completion;
- an interrupted-analysis variant whose manifest mode is initialized and one artifact is written
  before the batch stops.

Use the existing `MediaIdentity` shape. Do not invent a second filename-matching helper.

- [ ] **Step 2: Add regression tests with `pytest.mark.xfail(strict=True)` for current failures**

Add a test for each behavior below. Mark only cases that fail against the current production code
with `xfail(strict=True)`; keep already-correct invariants as ordinary tests. Cover:

- preview total is based on deduplicated identities, not raw JSON count;
- missing labels do not make all unlabeled artifacts belong to `day1`;
- an unambiguous pre-existing legacy label in legacy-adoption mode is not collapsed into
  `unknown`;
- a successful same-day consistency plus plan run does not leave the same stage endlessly ready;
- a greenfield project with one dated clip uses a date scope, not a rank-derived `day1` identity;
- a greenfield project with no recoverable date exposes `unknown` and blocks planning;
- the first preview/start/completion sequence for a greenfield project advances without legacy
  artifacts;
- an interrupted greenfield analysis remains greenfield and never adopts a generated label as
  legacy evidence;
- mixed canonical and legacy evidence without a manifest stays in legacy adoption and preserves
  existing aliases.

Name the expected behavior in the test names. Remove each `xfail` marker when its owning phase
lands: resolver/deduplication and greenfield scope resolution in Phase 2; metadata, manifest, and
interrupted-analysis tests in Phase 3; and the preview/start/completion loop regression in Phase 8.

- [x] **Step 3: Record the reference-project audit**

Run a read-only Python or PowerShell audit against `D:\vlog_projects\202610_Italy` and record:

- 362 JSON files;
- 181 unique original identities;
- duplicate pair count;
- label distribution;
- whether ffprobe can recover capture dates for the unique originals;
- the current preview/start/completion discrepancy pattern;
- what the `待执行 2–2 / 跳过 362–362` counters count, and why 362 is not a media total;
- the expected greenfield first-run behavior, documented separately from the legacy audit.

Do not rewrite, move, delete, or back up reference-project files in this phase.

- [x] **Step 4: Run focused tests**

```powershell
python -m pytest clio/tests/test_run_scope_identity.py -q
python -m pytest clio/tests/test_run_preview.py -q
```

Expected: the xfailed regressions stay green as expected failures; existing tests remain green.

- [x] **Step 5: Commit**

```powershell
git add clio/tests/test_run_scope_identity.py clio/tests/test_run_preview.py docs/analysis/2026-11-19-capture-day-scope-audit.md
git commit -m "chore(run): add capture-day scope regression audit"
```

---

## Phase 2: Add the canonical scope resolver and deduplication

**Purpose:** Make one production module responsible for identity grouping, deterministic artifact
selection, date/label resolution, scope IDs, and preview input fingerprints.

**Files:**
- Add `clio/run_scope.py`
- Modify `clio/tasks/analyze.py` only if a helper is moved or shared
- Modify `clio/tests/test_run_scope_identity.py`
- Extend `clio/tests/test_identity.py` if new identity-key behavior needs coverage

- [ ] **Step 1: Define the resolver dataclasses and IDs**

Add dataclasses for `ScopeOption`, `ResolvedScope`, and `ResolvedRunScope`. The resolver must
distinguish greenfield and legacy-adoption inputs, but both modes use the same scope ID forms.
Legacy `dayN` adoption is allowed only in legacy-adoption mode; greenfield resolution uses
persisted date or current media evidence and otherwise returns `unknown`.

Mode selection is deterministic and persisted once: a valid manifest `mode` wins; no prior
artifacts means greenfield. With no manifest, canonical analysis metadata can recover greenfield
state only when no pre-existing label, plan, consistency, or voiceover lineage is legacy. Any such
legacy evidence means legacy adoption, even if some canonical artifacts exist; mixed evidence is a
repair state. This phase only classifies mode; Phase 3 persists it before the first analysis
artifact write.

```text
YYYY-MM-DD
legacy-dayN
unknown
```

Expose a single production entry point:

```python
def resolve_run_scope(config, *, scope_id=None, legacy_day_label=None,
                      all_days=False, files=None, steps=None) -> ResolvedRunScope:
    ...
```

- [ ] **Step 2: Implement media-identity grouping**

Reuse `clio.identity.MediaIdentity` and its existing helpers. Group by source-level identity first.
If a legacy record has no original path, use the existing source-stem and compressed fallbacks in
the documented order. Never use the analysis numeric index or filename as identity.

Split segments of one original remain distinct when segment index or offset differs.

- [ ] **Step 3: Implement deterministic artifact selection**

Choose one artifact per identity group using the design priority:

1. current lineage/reuse check;
2. persisted scope metadata;
3. non-empty legacy `day_label`;
4. `media_identity`;
5. newest schema version;
6. newest modification time;
7. descending filename.

Return ignored duplicate artifacts as diagnostics. Do not delete them.

- [ ] **Step 4: Implement date and label resolution**

Resolve in this order:

1. persisted analysis scope metadata;
2. persisted capture fields;
3. telemetry/GPMF capture time;
4. ffprobe container `creation_time`;
5. filename date;
6. one unambiguous pre-existing legacy `dayN`, only in legacy-adoption mode;
7. `unknown`.

Persist the basis as `machine_local` when a timestamp is interpreted. In greenfield mode, do not
adopt a generated `day_label` as legacy evidence and do not convert an unresolved item to `day1`.

- [ ] **Step 5: Implement the input fingerprint**

Compute a deterministic fingerprint from selected steps, scope mode, canonical IDs, deduplicated
identity keys, manifest revision, and only the pre-run inputs that are not produced by an earlier
selected step. Keep completion evidence out of this function.

- [ ] **Step 6: Enable the regression tests**

Remove the resolver/deduplication and greenfield scope-resolution `xfail` markers from
`clio/tests/test_run_scope_identity.py`. Assert the synthetic legacy project returns deduplicated
counts, two date scopes, one legacy scope, and one unknown bucket. Add greenfield cases asserting
that a dated clip gets a date scope, an undated clip gets `unknown`, and no rank-derived `day1`
identity is created, and mixed canonical/legacy evidence keeps legacy adoption and its aliases.
Leave metadata, manifest, interrupted-analysis, and completion-loop regressions xfailed until their
owning phases.

- [ ] **Step 7: Run focused tests**

```powershell
python -m pytest clio/tests/test_run_scope_identity.py clio/tests/test_identity.py -q
python -m pytest clio/tests/test_run_preview.py -q
```

- [ ] **Step 8: Commit**

```powershell
git add clio/run_scope.py clio/tasks/analyze.py clio/tests/test_run_scope_identity.py clio/tests/test_identity.py
git commit -m "feat(run): add canonical capture-day scope resolver"
```

---

## Phase 3: Persist analysis scope metadata and freeze legacy aliases

**Purpose:** Stop recomputing scope identity from labels on every preview and make aliases stable.

**Files:**
- Add `clio/scope_manifest.py`
- Modify `clio/tasks/analyze.py`
- Modify `clio/tests/test_tasks_analyze.py`
- Add `clio/tests/test_scope_manifest.py`
- Modify `clio/tests/test_run_scope_identity.py`

- [ ] **Step 1: Add analysis metadata writes**

Write these additive fields to newly finalized analysis artifacts:

```json
{
  "capture_date": "2026-10-27",
  "capture_date_source": "container",
  "capture_date_basis": "machine_local",
  "scope_id": "2026-10-27",
  "legacy_day_label": "day1"
}
```

For an unambiguous legacy-only item, write `scope_id = legacy-dayN`, `capture_date = null`, and
`capture_date_source = legacy_label`. This inference applies only in legacy-adoption mode. In
greenfield mode, a missing date always writes `scope_id = unknown`; do not adopt a generated
`day_label` as legacy evidence.

Before writing the first analysis artifact, initialize the manifest mode described in Step 3. If
that write fails, abort before creating analysis JSON; do not leave an unclassified partial batch.
This metadata must be written on the first analysis of a new project, before any plan exists. Do
not use the rank-based `_build_day_labels()` result as a greenfield scope identity; it may only
supply a compatibility alias after the manifest resolves one. A missing date produces
`scope_id = unknown`, not `day1`.

- [ ] **Step 2: Add safe cache repair**

When an accepted analysis cache has unambiguous media identity and date evidence, add missing scope
metadata without changing user-edited plan content. If evidence is ambiguous, leave the cache
unchanged and report a diagnostic.

- [ ] **Step 3: Implement the manifest**

Persist `output/scope_manifest.json` with version, immutable `mode`, `next_legacy_day`, frozen
aliases, confidence, evidence, and hashed identity evidence. Initialize it with
`mode = greenfield` before the first analysis artifact write, then add each validated scope. A
legacy manifest, when explicitly created, records `mode = legacy_adoption`. Only adopt unambiguous
aliases automatically.

Before replacing a valid manifest, create a backup. Preserve malformed manifests and report them;
do not overwrite a malformed file with a guessed reconstruction.

- [ ] **Step 4: Preserve existing aliases**

Test that adding an earlier capture date does not renumber an existing `dayN`, and that an alias
collision is blocked rather than silently repaired. Add greenfield assertions that the mode is
persisted before the first artifact, the first analysis creates a date scope and manifest entry
without a legacy migration, and a generated `day_label` without a date remains `unknown`. Add an
interrupted-batch assertion proving the mode stays greenfield. Remove the metadata, manifest, and interrupted-analysis `xfail`
markers once these assertions pass.

- [ ] **Step 5: Run focused tests**

```powershell
python -m pytest clio/tests/test_scope_manifest.py clio/tests/test_tasks_analyze.py clio/tests/test_run_scope_identity.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add clio/scope_manifest.py clio/tasks/analyze.py clio/tests/test_scope_manifest.py clio/tests/test_tasks_analyze.py clio/tests/test_run_scope_identity.py
git commit -m "feat(run): persist and freeze capture-day scope metadata"
```

---

## Phase 4: Make plan, consistency, and plan-voiceover use canonical scopes

**Purpose:** Remove independent label interpretation and ensure every downstream artifact resolves
through the same scope and alias.

**Files:**
- Modify `clio/tasks/plan.py`
- Modify `clio/tasks/consistency.py`
- Modify `clio/tasks/scripts.py`
- Modify `clio/tasks/_helpers.py` only for shared media-selection behavior
- Extend `clio/tests/test_tasks_plan.py`
- Extend `clio/tests/test_tasks_consistency.py`
- Extend `clio/tests/test_tasks_scripts.py`

- [ ] **Step 1: Replace `_analysis_day_label()` default behavior**

Remove the implicit `"day1"` fallback. A missing label can resolve only from current media-date
evidence or an unambiguous manifest alias. Otherwise return a diagnostic and do not include the
artifact in a planning scope.

- [ ] **Step 2: Resolve plan clips from a scope**

Make plan input collection accept the resolver result. It must use the deduplicated, current
analysis artifact per identity and include the same artifact paths in lineage and prompt inputs.

- [ ] **Step 3: Resolve consistency paths and inputs from a scope**

Use the resolved alias for `plans/<alias>_entity_consistency.json/.md`. Readable legacy artifacts
are accepted only when their lineage and scope evidence match.

- [ ] **Step 4: Resolve plan-voiceover paths and inputs from a scope**

Use the resolved alias for `scripts/<alias>_plan_voiceover.json/.md`. Keep the existing overwrite
confirmation behavior.

- [ ] **Step 5: Add behavioral tests**

Cover:

- duplicate artifacts produce one plan input;
- missing labels never enter `day1`;
- a legacy scope reads and writes `dayN` artifacts but reports `scope_id = legacy-dayN`;
- an unknown scope is rejected for planning, consistency, and plan-voiceover;
- a changed manifest alias invalidates or redirects artifact lookup safely;
- a greenfield project can plan from its first date scope without any legacy artifacts.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest clio/tests/test_tasks_plan.py clio/tests/test_tasks_consistency.py clio/tests/test_tasks_scripts.py clio/tests/test_run_scope_identity.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add clio/tasks/plan.py clio/tasks/consistency.py clio/tasks/scripts.py clio/tasks/_helpers.py clio/tests/test_tasks_plan.py clio/tests/test_tasks_consistency.py clio/tests/test_tasks_scripts.py clio/tests/test_run_scope_identity.py
git commit -m "refactor(run): resolve scoped artifacts by canonical identity"
```

---

## Phase 5: Bind preview and start to one scope contract

**Purpose:** Ensure the user previews exactly the scope the server will run and rejects a stale
preview instead of silently running different inputs.

**Files:**
- Modify `clio/ui/services/run_preview.py`
- Modify `clio/ui/routes/run.py`
- Modify `clio/ui/services/run_estimate.py` if scope counts feed estimates
- Extend `clio/tests/test_run_preview.py`
- Add `clio/tests/test_run_scope_route.py`

- [ ] **Step 1: Add the preview scope block**

Return server-owned options and the selected scope:

```json
{
  "scope": {
    "mode": "single",
    "selected": ["2026-10-27"],
    "options": [],
    "unknown": null,
    "input_fingerprint": "...",
    "auto_selected": false,
    "warnings": []
  }
}
```

Each option includes display date/legacy label, analyzed count, duplicate count, source, and
planning eligibility.

- [ ] **Step 2: Accept optional `scope_id` in preview**

Resolve a saved canonical scope first. Fall back to an unambiguous saved `day_label`, then the
first known scope, and mark the result `auto_selected`.

- [ ] **Step 3: Validate start against the preview fingerprint**

`POST /api/run/start` accepts `scope_id`, `all_days`, or a legacy `day_label`. If the current
`input_fingerprint` differs from the submitted preview fingerprint, return structured `409` with
reason `stale_scope`, current options, and a recovery message.

- [ ] **Step 4: Keep all-days semantics explicit**

For `all_known`, freeze the mode and unknown exclusion. Re-resolve scopes after an analysis step in
the same run. Return the actual processed scopes in the task result.

- [ ] **Step 5: Add route tests**

Cover invalid IDs, path-like IDs, stale fingerprints, unambiguous legacy aliases in legacy mode,
ambiguous aliases, unknown planning rejection, all-days excluding unknown, and a greenfield project
selecting its first date scope without any legacy label.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest clio/tests/test_run_preview.py clio/tests/test_run_scope_route.py clio/tests/test_run_scope_identity.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add clio/ui/services/run_preview.py clio/ui/routes/run.py clio/ui/services/run_estimate.py clio/tests/test_run_preview.py clio/tests/test_run_scope_route.py clio/tests/test_run_scope_identity.py
git commit -m "feat(run): bind preview and start to canonical scope"
```

---

## Phase 6: Execute the pipeline from the resolved scope

**Purpose:** Remove independent day-label rediscovery from execution and make the task result
carry the actual scope IDs and completion evidence.

**Files:**
- Modify `clio/pipeline.py`
- Modify `clio/main.py` for CLI compatibility if needed
- Modify `clio/tasks/plan.py`
- Extend `clio/tests/test_pipeline.py`
- Extend `clio/tests/test_run_preview.py`

- [ ] **Step 1: Pass `ResolvedRunScope` into the pipeline**

Keep the existing CLI `day_label` argument as a compatibility wrapper that resolves through the
manifest. New Web and internal callers pass canonical `scope_id` and `input_fingerprint`.

- [ ] **Step 2: Remove per-step label rediscovery**

Replace `discover_day_labels()` calls in the execution path with the resolver. For `all_known`,
re-resolve after an analysis step by design; for a single scope, keep the canonical ID fixed.

- [ ] **Step 3: Record actual scope results**

Return the processed scope IDs, legacy aliases, artifact paths read/written, and completion
evidence in the task result. Do not rely on the browser to reconstruct this data.

- [ ] **Step 4: Add pipeline tests**

Cover single scope, all-known scope, unknown exclusion, legacy-only scope, duplicate input
deduplication, same-run analyze plus plan cascade, and a greenfield first run that starts from a
date scope and never invokes legacy label ranking.

- [ ] **Step 5: Run focused tests**

```powershell
python -m pytest clio/tests/test_pipeline.py clio/tests/test_run_preview.py clio/tests/test_run_scope_identity.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add clio/pipeline.py clio/main.py clio/tasks/plan.py clio/tests/test_pipeline.py clio/tests/test_run_preview.py clio/tests/test_run_scope_identity.py
git commit -m "refactor(run): execute pipeline from resolved scope"
```

---

## Phase 7: Replace the free-text day input with a server-backed selector

**Purpose:** Make the UI explain capture dates, legacy aliases, duplicate omissions, and unknown
media without requiring users to understand `dayN` ordering.

**Files:**
- Modify `clio/ui/static/src/runner.js`
- Modify `clio/ui/static/src/runner-preview.js`
- Modify `clio/ui/static/src/runner-stage-model.js` if scope data feeds stage status
- Modify `clio/ui/static/locales/zh-CN.json`
- Modify `clio/ui/static/locales/en.json`
- Add `clio/ui/static/src/__tests__/runner-scope.test.js`
- Extend `clio/ui/static/src/__tests__/runner-preview.test.js`
- Extend `clio/ui/static/src/__tests__/runner-locale.test.js`

- [ ] **Step 1: Render the scope selector**

Replace `#run-day` free text with a select populated from `preview.scope.options`. Display:

- capture date or "date unknown";
- legacy alias;
- clip count;
- date source;
- duplicate count;
- planning eligibility for unknown.

- [ ] **Step 2: Persist canonical scope state**

Save `scope_id`, mode, and fingerprint in the run session. Restore by canonical ID; use legacy
`dayN` only as a one-time compatibility input.

- [ ] **Step 3: Send preview and start consistently**

Start sends the selected `scope_id`, `all_days`, selected steps, options, and the preview
`input_fingerprint`. If the server returns `stale_scope`, refresh preview and show the recovery
message.

- [ ] **Step 4: Update localized copy**

Add zh-CN and en keys for scope labels, date-unknown legacy scopes, duplicate counts, unknown
blocking, stale preview, and completion diagnostics. Keep key sets identical.

- [ ] **Step 5: Add frontend tests**

Cover selector population, legacy/unknown rendering, all-days exclusion of unknown, session
restore, stale preview recovery, and locale key parity.

- [ ] **Step 6: Run focused tests and typecheck**

```powershell
npm test -- --run clio/ui/static/src/__tests__/runner-scope.test.js clio/ui/static/src/__tests__/runner-preview.test.js clio/ui/static/src/__tests__/runner-locale.test.js
npm run typecheck
```

- [ ] **Step 7: Commit**

```powershell
git add clio/ui/static/src/runner.js clio/ui/static/src/runner-preview.js clio/ui/static/src/runner-stage-model.js clio/ui/static/locales/zh-CN.json clio/ui/static/locales/en.json clio/ui/static/src/__tests__/runner-scope.test.js clio/ui/static/src/__tests__/runner-preview.test.js clio/ui/static/src/__tests__/runner-locale.test.js
git commit -m "feat(ui): select capture-day scopes from server options"
```

---

## Phase 8: Complete and advance from the submitted scope

**Purpose:** Eliminate the endless same-stage rerun by refreshing completion with the canonical
scope returned by the task and explaining validation failures.

**Files:**
- Modify `clio/ui/routes/run.py`
- Modify `clio/ui/services/run_preview.py`
- Modify `clio/task_center/` task result serialization if required
- Modify `clio/ui/static/src/runner.js`
- Modify `clio/ui/static/src/runner-stage-model.js`
- Modify `clio/ui/static/locales/zh-CN.json`
- Modify `clio/ui/static/locales/en.json`
- Extend `clio/tests/test_run_scope_route.py`
- Extend `clio/ui/static/src/__tests__/runner-recovery.test.js`
- Extend `clio/ui/static/src/__tests__/runner-stages.test.js`

- [ ] **Step 1: Return actual completion scope data**

Task results return `scope_ids`, `scope_mode`, `legacy_aliases`, processed/written artifacts,
and completion evidence.

- [ ] **Step 2: Compute completion validation separately**

Recompute `completion_fingerprint` from the actual required artifacts. Do not compare it to
`input_fingerprint`.

- [ ] **Step 3: Add completion diagnostics**

If the just-completed stage remains ready, return a reason code such as `scope_changed`,
`artifact_missing`, `artifact_scope_mismatch`, `lineage_mismatch`, `duplicate_inputs`, or
`task_success_but_validation_failed`. Compute `next_stage` from the same resolution.

- [ ] **Step 4: Refresh the UI with task-returned scope**

Do not use `#run-day`, `state.currentDay`, or an array-order guess for completion. Show a
diagnostic card with a recovery action when the stage remains ready.

- [ ] **Step 5: Add regression tests**

Reproduce the reported sequence:

1. preview same-day consistency plus plan;
2. run to success;
3. refresh completion using submitted canonical scope;
4. assert consistency and plan are reusable;
5. assert `next_stage` advances;
6. inject scope drift and assert the diagnostic appears instead of another ready button;
7. repeat the first-run sequence in a greenfield project with no manifest, legacy plan, or
   consistency artifact and assert the same advancement.

Enable the completion-loop `xfail` created in Phase 1 as part of this phase.

- [ ] **Step 6: Run focused tests**

```powershell
python -m pytest clio/tests/test_run_scope_route.py clio/tests/test_run_preview.py -q
npm test -- --run clio/ui/static/src/__tests__/runner-recovery.test.js clio/ui/static/src/__tests__/runner-stages.test.js
python -m pytest clio/tests/test_run_scope_identity.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add clio/ui/routes/run.py clio/ui/services/run_preview.py clio/task_center clio/ui/static/src/runner.js clio/ui/static/src/runner-stage-model.js clio/ui/static/locales/zh-CN.json clio/ui/static/locales/en.json clio/tests/test_run_scope_route.py clio/ui/static/src/__tests__/runner-recovery.test.js clio/ui/static/src/__tests__/runner-stages.test.js
git commit -m "fix(run): advance completion from the submitted scope"
```

---

## Phase 9: Remove legacy defaults and verify rollout

**Purpose:** Finish the migration, remove silent `day1` fallbacks, document behavior, and verify the
reference project through the new resolver without mutating it.

**Files:**
- Modify remaining `day_label: str = "day1"` call sites in `clio/`
- Modify `clio/tasks/analyze.py` to remove rank-based greenfield scope assignment
- Modify `clio/ui/routes/run.py`
- Modify `clio/ui/routes/projects.py` and `clio/ui/services/project_service.py` if `currentDay`
  remains as a compatibility field
- Modify `clio/ui/static/src/runner.js`
- Modify `clio/locales/zh-CN.json`
- Modify `clio/locales/en.json`
- Modify `clio/ui/static/locales/zh-CN.json`
- Modify `clio/ui/static/locales/en.json`
- Modify `ROADMAP.md`
- Update the audit and review documents with final evidence

- [ ] **Step 1: Search for remaining silent defaults**

```powershell
rg -n "day_label.*day1|day1.*day_label|or [\"']day1[\"']|default.*day1" clio main.py
```

For each result, either replace it with resolver-based behavior or document why it is a safe CLI
compatibility fallback. No Web preview, planning, consistency, or completion path may default to
`day1`. Search separately for `_build_day_labels` and `currentDay`; neither may define the primary
scope of a new project.

- [ ] **Step 2: Run the reference-project read-only audit**

Verify the reference project:

- 181 deduplicated clips;
- nine executable scopes;
- duplicate counts and label distribution;
- unknown count;
- no automatic manifest or artifact rewrite unless explicitly requested;
- expected `next_stage` after a successful plan-alignment run.

Verify a fresh project separately:

- first analysis writes date or `unknown` metadata and creates the manifest;
- first plan uses a date scope, not a rank-derived `day1`;
- no legacy plan, consistency, or voiceover artifact is required;
- an undated clip blocks planning in `unknown` until repaired.

- [ ] **Step 3: Update ROADMAP and documents**

Mark the active capture-day identity item complete or record the remaining rollout limit. Link the
design, plan, review, audit, and final verification evidence.

- [ ] **Step 4: Run focused and full verification**

```powershell
python -m pytest clio/tests/ -q
npm run typecheck
npm test
ruff format clio main.py
ruff check clio main.py
mypy --check-untyped-defs --show-error-codes
python main.py check
git diff --check
```

- [ ] **Step 5: Commit**

```powershell
git add clio main.py ROADMAP.md docs/analysis/2026-11-19-capture-day-scope-audit.md docs/superpowers/specs/2026-11-19-capture-day-scopes-design.md docs/superpowers/reviews/2026-11-19-capture-day-scopes-review.md
git commit -m "refactor(run): remove implicit day1 scope defaults"
```

---

## Verification Checklist

- [ ] Normal same-day consistency plus plan run advances to the next stage.
- [ ] A new project's greenfield mode is persisted before the first analysis write and its first
  run uses date or `unknown` scopes without a legacy migration.
- [ ] An interrupted first analysis cannot reclassify the project as legacy adoption.
- [ ] A new project with one dated clip uses a date scope ID, not a rank-derived `day1` identity.
- [ ] A new project with no recoverable date blocks planning in `unknown` until repaired.
- [ ] Reference project counts 181 deduplicated clips, not 362 raw JSON files.
- [ ] `day1–day9` resolves to stable capture-date scopes or frozen `legacy-dayN` scopes.
- [ ] Unknown media is excluded from `all_days` and blocked from planning with an explanation.
- [ ] Preview and start use the same scope IDs and input fingerprint.
- [ ] Completion uses task-returned scope IDs, not DOM state.
- [ ] Stale scope returns structured `409`, not silent execution.
- [ ] Legacy artifacts remain readable and are never moved or deleted automatically.
- [ ] zh-CN and en locale key sets match.
- [ ] `git diff --check`, focused tests, full pytest, Vitest, typecheck, Ruff, mypy, and
  `python main.py check` pass.
# Capture-Day Scope Identity Self-Review

**Date:** 2026-11-19
**Review type:** Design and implementation-plan review
**Design:** [`2026-11-19-capture-day-scopes-design.md`](../specs/2026-11-19-capture-day-scopes-design.md)
**Plan:** [`2026-11-19-capture-day-scopes-plan.md`](../plans/2026-11-19-capture-day-scopes-plan.md)

## Executive assessment

The direction is sound: a capture date should be the stable scope identity, `dayN` should become a
frozen compatibility alias, and preview/start/completion must share one resolver. The design now
handles the most important legacy case in the reference project: nine existing `dayN` labels with
no date persisted in analysis JSON.

Implementation should not begin with a broad refactor. It should first reproduce the endless
same-stage behavior and run a read-only audit of the reference project. The resolver and metadata
migration can then land in small phases without deleting or moving user artifacts.

The design is approved for implementation with Phase 1 as a gate. The exact cause of the reported
completion loop is not yet proven; the plan must confirm it before removing the legacy behavior.

## Review findings and resolutions

### SR-01 - Date-or-unknown alone could not represent the reference project

**Original problem:** The first draft allowed only `YYYY-MM-DD` and `unknown` scope IDs. The
reference project has nine legacy labels but no persisted capture date. Collapsing all nine into
`unknown` would make daily planning and existing artifacts unusable.

**Resolution:** The design now supports `legacy-dayN` for an unambiguous legacy label. It is a
server-issued, frozen compatibility scope, displayed as "date unknown · original label dayN".
`unknown` remains reserved for missing or conflicting identity evidence.

**Status:** Resolved.

### SR-02 - Unknown media was accidentally planning-eligible

**Original problem:** The first draft said unknown was selectable for planning. That would allow
content generation from media whose scope identity is unresolved and continue the same drift.

**Resolution:** Unknown is selectable only for inventory and analysis repair. Consistency,
voiceover, plan, and plan-voiceover are blocked until a date or unambiguous legacy assignment
exists.

**Status:** Resolved.

### SR-03 - Preview and completion fingerprints had different lifecycles

**Original problem:** A single fingerprint could not both bind preview/start inputs and validate
artifacts after a successful run. Including outputs in the input fingerprint would make it stale
immediately after a successful write.

**Resolution:** The design now defines:

- `input_fingerprint`: preview/start consistency and pre-run inputs;
- `completion_fingerprint`: post-run validation over actual required artifacts.

They are never compared directly.

**Status:** Resolved.

### SR-04 - Missing capture date and missing legacy label were conflated

**Original problem:** Both conditions were described as "unknown", which would discard an
unambiguous legacy `dayN` scope.

**Resolution:** The resolver now distinguishes:

- known date -> `capture_date` scope;
- date unknown, one unambiguous label -> `legacy_dayN` scope;
- date unknown, conflicting labels or no label -> `unknown`.

No path silently maps to `day1`.

**Status:** Resolved.

### SR-05 - Manifest reconstruction could renumber or guess aliases

**Original problem:** A missing manifest could cause a new ranking-based mapping and rename
existing artifacts. Ambiguous evidence could also be adopted silently.

**Resolution:** Existing aliases are frozen. New dates receive `next_legacy_day`. Only unambiguous
aliases are adopted automatically, and ambiguous reconstruction is read-only diagnostic state.
Alias collisions are blocking rather than silently repaired.

**Status:** Resolved.

### SR-06 - Deduplication by compressed stem would fail on the real project

**Original problem:** The duplicate pair `001_GX011260` and `182_GX011260` has different
compressed/index identities but the same original source identity. Deduplication by filename,
index, or compressed stem would leave 362 clips.

**Resolution:** Grouping uses source-level `MediaIdentity` evidence first and only falls back to
compressed identity when no source identity exists. Numeric indices and analysis filenames are
never primary identity.

**Status:** Resolved.

### SR-07 - `all_days` plus analyze can legitimately discover new scopes

**Original problem:** Strictly binding `all_days` to the preview's enumerated ID list would reject
or miss dates created by the run's own analysis step.

**Resolution:** `all_known` freezes the mode and unknown exclusion, not a permanently enumerated
list. The pipeline re-resolves after an analysis step in the same run. A single selected scope keeps
its stable canonical ID.

**Status:** Resolved; must be covered by pipeline regression tests.

### SR-08 - Completion still depended on DOM/free-text state

**Original problem:** Completion refresh used current day input state or `availablePlans[0]`, so it
could validate a different scope than the task actually ran.

**Resolution:** Task results carry canonical scope IDs, aliases, actual artifact paths, and
completion evidence. The UI refreshes from that result. If the completed stage remains ready, the
backend returns a concrete diagnostic code.

**Status:** Resolved in design; implementation risk remains in Phase 8.

### SR-09 - Same-date sessions are a separate product problem

**Original problem:** A calendar-date scope cannot distinguish morning and evening sessions on the
same day.

**Resolution:** The first delivery explicitly groups all clips on one date into one capture-day
scope. Session splitting is a later feature. The UI avoids calling the scope a trip day or
itinerary day.

**Status:** Accepted limitation.

### SR-10 - The exact completion-loop root cause is not yet proven

**Original problem:** The evidence proves scope drift and duplicate counting, but the remaining
same-stage readiness could also involve lineage, task result serialization, or preview caching.

**Resolution:** Phase 1 adds a focused regression and read-only audit. Phase 8 verifies the full
sequence through the production preview/start/completion entry points. No one should claim the loop
is fixed based only on the resolver refactor.

**Status:** Open gate; Phase 1 must produce evidence.

### SR-11 - Commit taxonomy did not match repository rules

**Original problem:** The first plan used a `test(...)` commit type. The repository commit contract
allows `feat`, `fix`, `refactor`, `docs`, and `chore`.

**Resolution:** The plan now uses `chore(run)` for the regression/audit phase and one allowed type
per later phase.

**Status:** Resolved.

### SR-12 - Timezone and ffprobe performance remain operational risks

**Original problem:** Dates are interpreted in machine-local time, and reprobing network media can
be slow. A trip crossing timezones can put clips on the wrong date.

**Resolution:** Persist `capture_date_source` and `capture_date_basis = machine_local`, show the
basis in diagnostics, and keep a project timezone policy out of this change. The resolver should
batch or cache probes rather than spawning per artifact in preview.

**Status:** Accepted limitation with a follow-up boundary.

## Boundary review

The design preserves the current repository contracts:

- `MediaIdentity` remains the shared identity source;
- legacy `dayN` filenames remain valid in phase 1;
- plan and voiceover overwrite protections remain in place;
- no user artifact is deleted or moved automatically;
- scope IDs remain server-owned and path containment remains enforced;
- zh-CN and en locale key sets remain identical;
- analysis, plan, consistency, and plan-voiceover line contracts remain versioned and inspectable.

## Residual risks

| Risk | Mitigation |
|---|---|
| Legacy labels are ambiguous across duplicate artifacts | Use `unknown` with a conflict diagnostic; require explicit repair |
| ffprobe cannot recover a date | Use frozen `legacy-dayN`; do not invent a date |
| Manifest is missing or malformed | Reconstruct read-only, report warning, back up before replacement |
| New analysis writes metadata while old artifacts remain | Deduplicate by identity and prefer current lineage/metadata |
| `all_days` discovers a new date during the run | Keep `all_known` mode explicit and use actual processed scopes for completion |
| A user edits a plan after completion | Recompute completion validation; show lineage/scope mismatch |
| Same date has multiple sessions | Accept as a known limitation; add explicit grouping later |
| Timezone changes between runs | Persist basis and date; add a project timezone policy later |

## Review decision

Proceed with Phase 1 only. The first implementation checkpoint is successful when it:

1. reproduces the endless same-stage behavior through production preview/start/completion paths;
2. proves the reference project's duplicate identity and label distribution without modifying it;
3. identifies whether all nine dates can be recovered from ffprobe or require `legacy-dayN`;
4. leaves every existing artifact and unrelated worktree change untouched.

After that checkpoint, Phases 2-4 can establish the canonical resolver, manifest, and shared
artifact lookup. Phases 5-8 should not begin until the resolver tests demonstrate that preview,
execution, and completion return the same scope IDs.
# Capture-Day Scope Identity Design

**Status:** Proposed; reviewed 2026-11-19
**Date:** 2026-11-19
**Scope:** Run scope selection, analysis artifact identity, plan reuse, and guided-stage completion
**Implementation plan:** [`2026-11-19-capture-day-scopes-plan.md`](../plans/2026-11-19-capture-day-scopes-plan.md)
**Self-review:** [`2026-11-19-capture-day-scopes-review.md`](../reviews/2026-11-19-capture-day-scopes-review.md)

## 1. Summary

Clio currently exposes a relative `dayN` label as both the user-facing day name and the internal
identity used to find, plan, reuse, and complete daily work. That label is derived from the sorted
set of capture dates found in the current media set. It is not hard-coded, but it is mutable:

- adding an earlier capture date shifts every later `dayN`;
- media whose date cannot be determined falls back to `day1`;
- missing `day_label` values also fall back to `day1`;
- old and duplicate analysis JSON files can contribute extra clips and labels;
- preview, execution, and completion can therefore resolve different inputs while appearing to
  refer to the same day.

The recommended fix is to make a capture date the stable scope identity and treat `dayN` as a
compatibility alias. When a date cannot be recovered but one legacy `dayN` label is unambiguous,
the server issues a frozen `legacy-dayN` scope. Truly unclassified media goes to `unknown`; it is
never assigned to `day1` by default.

Run preview, execution, artifact lookup, completion, and next-stage selection must all use the
same canonical scope resolution. Preview/start consistency uses an `input_fingerprint`; successful
completion is validated separately from the artifacts the run actually produced.

## 2. Evidence from the Reference Project

The reported project is `D:\vlog_projects\202610_Italy`. The observed output directory contains:

- 362 analysis JSON files;
- 181 unique `source_file` values;
- exactly two analysis JSON files per source file;
- 180 analysis JSON files without `day_label`;
- 182 labeled analysis JSON files distributed over `day1` through `day9`;
- labels `day1=21`, `day2=17`, `day3=29`, `day4=11`, `day5=30`, `day6=17`,
  `day7=24`, `day8=26`, `day9=7`;
- `project.json` with `currentDay = "day1"`;
- existing `day1_plan.json`, `day1_entity_consistency.json`, and unrelated `test_plan.json` /
  `trip_plan.json` files.

No analysis JSON contains `capture_date`, `capture_time`, `recorded_at`, or `creation_time`, but
all 362 contain `media_identity`. Duplicate pairs use different compressed/index identities, for
example `001_GX011260` and `182_GX011260`, while sharing the same original source identity.
Deduplication therefore cannot use the compressed stem, numeric index, or analysis filename.

The original media still carries container timestamps. A read-only sample of `GX011260.MP4`
returned `2026-10-01T19:02:14Z`. This makes date-first scope IDs feasible, but the dates must be
persisted because discovery currently reprobes media and does not store the result.

This explains the visible `day1` through `day9`: nine distinct capture dates were detected by
`_build_day_labels()` and numbered in ascending order. It is not a hard-coded nine-day limit.
The implementation is still misleading because:

1. The relative numbers are presented as if they were stable trip days.
2. Missing labels are interpreted as `day1` by plan discovery.
3. Duplicate analysis artifacts are counted as separate clips in planning and preview.
4. A plan can be generated from one artifact set and judged reusable against another.
5. Completion can refresh with client DOM state instead of the scope actually submitted.

The direct completion-loop cause must be confirmed with a regression test. The required invariant
is clear: the scope selected for preview, submitted to the task, returned by the task, and used to
refresh completion must resolve to the same media identities and compatible analysis artifacts.

## 3. Goals and Non-Goals

### Goals

- Give every analyzed capture day a stable, server-owned identity.
- Display capture dates instead of relative `dayN` labels in the Run UI.
- Preserve `dayN` inputs and artifact names as a non-destructive compatibility layer.
- Deduplicate analysis artifacts by current media identity.
- Stop treating missing capture dates or missing `day_label` values as `day1`.
- Represent an unambiguous legacy label as a visible, frozen compatibility scope.
- Make `all_days` expand known capture and legacy scopes only, with unknown media called out.
- Make preview, execution, reuse, completion, and next-stage selection share one resolver.
- Keep the migration reversible and avoid moving or deleting user artifacts automatically.

### Non-Goals

- Inferring city or location names from dates.
- Building a full trip itinerary editor.
- Splitting multiple capture sessions that occur on the same calendar date.
- Changing media identity or timeline-offset rules.
- Deleting duplicate analysis artifacts without user confirmation.
- Replacing legacy `dayN` artifact filenames in the first delivery.
- Solving timezone-aware capture semantics with a new project timezone setting in this change.

## 4. Canonical Scope Model

### 4.1 Scope kinds

A scope is a server-issued object. Three kinds are supported:

```json
{
  "id": "2026-10-27",
  "kind": "capture_date",
  "capture_date": "2026-10-27",
  "capture_date_source": "container",
  "capture_date_basis": "machine_local",
  "legacy_day_label": "day1",
  "clip_count": 21,
  "analyzed_clip_count": 21,
  "duplicate_artifact_count": 1,
  "planning_allowed": true,
  "warnings": []
}
```

```json
{
  "id": "legacy-day3",
  "kind": "legacy_day",
  "capture_date": null,
  "capture_date_source": "legacy_label",
  "capture_date_basis": "unknown",
  "legacy_day_label": "day3",
  "clip_count": 29,
  "analyzed_clip_count": 29,
  "duplicate_artifact_count": 0,
  "planning_allowed": true,
  "warnings": ["Capture date could not be determined; using the unambiguous legacy day3 label."]
}
```

```json
{
  "id": "unknown",
  "kind": "unknown",
  "capture_date": null,
  "capture_date_source": "unknown",
  "capture_date_basis": "unknown",
  "legacy_day_label": null,
  "clip_count": 3,
  "analyzed_clip_count": 3,
  "duplicate_artifact_count": 0,
  "planning_allowed": false,
  "warnings": ["Capture date could not be determined"]
}
```

`capture_date_source` is one of `analysis_artifact`, `container`, `telemetry`, `filename`,
`legacy_label`, or `unknown`. `capture_date_basis` is `machine_local` when a timestamp is
interpreted, or `unknown` when no date exists.

### 4.2 Allowed IDs

The API accepts only a server-discovered scope ID matching one of:

```text
^\d{4}-\d{2}-\d{2}$
^legacy-day[1-9]\d{0,5}$
^unknown$
```

The regex is a defense-in-depth check, not permission. The ID must also exist in the current
resolver result. Scope IDs never become path fragments directly. Artifact paths use the resolved
legacy day alias through `plan_day_stem()` and existing containment checks.

### 4.3 Missing date versus missing legacy label

These are different conditions:

- Date known: issue a `capture_date` scope. A missing analysis `day_label` is irrelevant.
- Date unknown, one unambiguous `dayN` label in the deduplicated source group: issue a
  `legacy_day` scope and show "date unknown · original label dayN".
- Date unknown, conflicting `dayN` labels in one source group: issue `unknown` with a conflict
  warning; do not choose one label silently.
- Date unknown, no label: issue `unknown`; never map to `day1`.

## 5. Discovery and Deduplication

### 5.1 Current media is the authority

Scope discovery starts from the current project media selection and compressed media, not from
every JSON file under `texts/`. Historical files may remain on disk, but only one current analysis
artifact per media identity participates in a run.

When `videos.json` exists, its selected originals define the active set. A project without a
selection file may use the existing project-directory fallback. Artifacts that cannot be linked to
the active media set are excluded from run counts and reported as orphaned diagnostics. They are
not deleted or rewritten during discovery.

### 5.2 Identity grouping

Artifacts are grouped using the existing `MediaIdentity` helpers. The preferred identity key is:

1. normalized original path;
2. original stem plus segment index and rounded segment offset;
3. normalized compressed path or stem only when no original identity exists;
4. source-file stem as the final legacy fallback.

Do not use the analysis file's numeric index, analysis filename, or compressed stem as the primary
identity. Re-analysis can assign a different index and compressed filename to the same source clip.

Legacy records may expose different identity levels. The resolver may union records when compatible
source-level aliases match, but it must not merge records solely because their compressed indices
differ or because one compressed stem looks similar. Split segments of one original are distinct
clips when their segment index or offset differs.

### 5.3 Deterministic artifact choice

Within each identity group, choose exactly one artifact using this order:

1. artifact that passes the current analysis lineage/reuse check;
2. artifact with persisted canonical scope metadata;
3. artifact with a non-empty legacy `day_label`;
4. artifact with `media_identity`;
5. newest schema version;
6. newest modification time;
7. filename in descending order as a deterministic tie-breaker.

Other valid artifacts are reported as duplicates and excluded from clip counts, prompts, plan
lineage, preview totals, and completion checks. They are not deleted automatically. The UI shows
the ignored count and offers a separate cleanup action or command.

If a source clip has no usable analysis artifact, it remains part of `clip_count` but not
`analyzed_clip_count`. Planning for that scope is blocked until analysis repairs the gap.

### 5.4 Date and label resolution

Resolution order is:

1. persisted `scope_id` / `capture_date` in a current analysis artifact;
2. persisted `capture_time`, `recorded_at`, or `creation_time` fields;
3. GPMF/telemetry capture time already supported by the project;
4. ffprobe container `creation_time` on the original media;
5. `YYYYMMDD` or equivalent date text in the original filename;
6. exactly one unambiguous legacy `dayN` label for the source group;
7. `unknown`.

An artifact's `scope_id` is evidence, not authority. The resolver recomputes the current date and
identity, then validates the stored scope metadata. A mismatch is a diagnostic and excludes the
artifact from reuse rather than silently accepting stale identity.

## 6. Same-Date Semantics and Timezone

For this delivery, all clips on one calendar date form one capture-day scope. Multiple sessions on
the same date are not split; that requires a later explicit grouping feature.

Capture dates preserve the current `_parse_capture_time_text()` behavior: aware timestamps are
converted to the analysis machine's local wall-clock date, and naive timestamps are treated as
local. The resulting date and `capture_date_basis = machine_local` are persisted so later previews
do not depend on the current machine timezone.

This has a known limitation: a trip that crosses timezones, or analysis performed in a different
timezone from the camera, can place clips on the wrong calendar date. The design exposes the source
and basis in diagnostics. A project-level timezone policy is a follow-up change.

The UI says "capture date" rather than "trip day N". A date is not presented as an itinerary day
number, and no city name is inferred from it.

## 7. Persisted Legacy Aliases

The project keeps a scope manifest under `output/scope_manifest.json`:

```json
{
  "version": 1,
  "next_legacy_day": 10,
  "scopes": {
    "2026-10-27": {
      "kind": "capture_date",
      "legacy_day_label": "day1",
      "confidence": "high",
      "evidence": ["analysis_day_label", "original_media_identity"],
      "identity_keys": ["sha256:..."]
    },
    "legacy-day3": {
      "kind": "legacy_day",
      "legacy_day_label": "day3",
      "confidence": "medium",
      "evidence": ["unambiguous_legacy_label"],
      "identity_keys": ["sha256:..."]
    }
  },
  "warnings": []
}
```

The manifest is a compatibility cache, not the primary identity. Existing aliases are frozen:

- an existing date or legacy scope keeps its `dayN` alias;
- a new date receives `next_legacy_day`, not a rank-based number;
- adding an earlier date never renames existing aliases;
- if an alias is already owned by another scope, the conflict is blocking and requires explicit
  repair; the resolver does not silently renumber it.

Only unambiguous aliases are adopted automatically. Evidence may come from an existing non-missing
`day_label`, matching plan/consistency lineage, or an explicit future user assignment. Ambiguous
reconstruction is read-only diagnostic state: the resolver reports it and refuses to write a
guessed alias.

Writes use the existing atomic JSON writer. Before replacing a manifest, copy the previous valid
manifest to a backup. A malformed or unsupported manifest is preserved, reported, and ignored in
favor of read-only reconstruction until repaired. Older code can ignore the file.

## 8. Analysis and Downstream Metadata

Analysis JSON gains additive metadata:

```json
{
  "capture_date": "2026-10-27",
  "capture_date_source": "container",
  "capture_date_basis": "machine_local",
  "scope_id": "2026-10-27",
  "legacy_day_label": "day1"
}
```

For a legacy-only scope, `capture_date` is null and `scope_id` is `legacy-dayN`. `legacy_day_label`
remains the artifact filename alias.

Plan, consistency, and plan-voiceover artifacts also persist:

```json
{
  "scope_id": "2026-10-27",
  "legacy_day_label": "day1",
  "scope_inputs": {
    "media_identity_keys": ["sha256:..."],
    "analysis_artifacts": [{"name": "...", "sha256": "..."}]
  }
}
```

`scope_inputs` is evidence used for completion diagnostics. It is not a substitute for the
existing lineage fingerprint and must not include the artifact that the run itself writes.

New analysis writes all scope metadata. Older artifacts remain readable. Discovery never rewrites
them. A later explicit analyze run, or a safe cache-repair path with unambiguous evidence, may add
missing metadata without changing user-edited plan content.

## 9. Run Contract

### 9.1 Shared resolver

A single production resolver returns a `ResolvedRunScope` for preview, start, pipeline execution,
artifact lookup, completion, next-stage selection, and export:

```text
resolve_run_scope(config, scope_id=None, legacy_day_label=None,
                  all_days=False, files=None, steps=None) -> ResolvedRunScope
```

The result contains:

- `mode`: `single`, `all_known`, or `selection`;
- selected scopes in execution order;
- display-ready options, including the unknown bucket;
- legacy alias mapping;
- deduplicated media identity keys;
- `input_fingerprint`;
- warnings and diagnostics.

No production caller may independently rediscover day labels or reinterpret a missing label as
`day1`.

### 9.2 Preview response

`POST /api/run/preview` accepts an optional `scope_id`, `legacy_day_label`, `all_days`, `files`,
and selected steps. It returns a `scope` block:

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

Options are server-owned and display-ready. Each option includes the scope ID, date or legacy
label, clip count, analyzed count, legacy alias, date source, duplicate count, and planning
eligibility. The client must not construct scope IDs or parse dates from free text.

If no scope is supplied, the server resolves the saved canonical scope, then an unambiguous saved
legacy alias, then the first known scope. It marks the result `auto_selected` so the UI can explain
the choice.

### 9.3 Fingerprints

Preview/start consistency and completion validation use different contracts.

`input_fingerprint` is computed before execution. It covers:

- selected steps and execution options;
- scope mode, canonical scope IDs, and manifest revision/alias mapping;
- deduplicated media identity keys and stable source/compressed input metadata;
- analysis artifact hashes only when they are inputs to a selected step;
- relevant config, prompt, context, transcript, and file-selection inputs.

It excludes artifacts that an earlier selected step in the same run will produce. In particular, a
run that includes `analyze` does not bind plan completion to the pre-run analysis artifact hash.
For `all_known`, the mode is frozen, but runtime may discover new scopes created by its own
analysis step; preview reports that possibility explicitly.

`completion_fingerprint` is recomputed after execution from the actual required artifacts. It
contains the actual scope IDs, artifact scope metadata, existing lineage fingerprint, and relevant
artifact digests. It is never compared with `input_fingerprint`; it answers whether the completed
work is valid now.

If `input_fingerprint` changes between preview and start, the server returns a structured `409`
with reason `stale_scope`, the current options, and a request to refresh preview.

### 9.4 Start and execution

`POST /api/run/start` accepts `scope_id`, `all_days`, or the legacy `day_label`. A legacy
`day_label` is resolved through the frozen alias map and is accepted only when unambiguous.
Invalid, conflicting, or stale scopes return a structured `409`.

The task input and result carry:

- canonical scope IDs;
- legacy aliases used for compatibility;
- scope mode and `all_days` expansion;
- whether the unknown bucket was excluded;
- the preview `input_fingerprint`;
- artifact paths actually read and written;
- actual processed scopes and completion evidence.

For a single scope, the canonical ID remains stable even if analysis adds an earlier date. For
`all_known`, the pipeline re-resolves the active scopes after an analysis step in the same run, as
the current design intends, but the mode and unknown exclusion remain frozen. This is not scope
drift; it is the documented semantics of "all known dates".

### 9.5 Completion and next stage

After a run, the client refreshes preview using the canonical scope data returned by the task, not
a free-text input or current DOM state. The backend computes `next_stage` from that same resolution.

A stage is reusable only when every required scoped artifact resolves to the same scope and passes
lineage validation. If the just-completed stage remains `ready` after a successful run, the
response includes a completion diagnostic with a reason code, for example:

- `scope_changed`;
- `artifact_missing`;
- `artifact_scope_mismatch`;
- `lineage_mismatch`;
- `duplicate_inputs`;
- `empty_scope`;
- `task_success_but_validation_failed`.

The UI must show the diagnostic and a recovery action instead of silently allowing the same stage
to run forever.

## 10. Unknown, Legacy, and All-Days Behavior

- `all_days` expands capture-date and unambiguous legacy-day scopes.
- The `unknown` bucket is excluded from `all_days` and reported with a count.
- `unknown` is selectable only for inventory and analysis repair. Consistency, voiceover,
  plan, and plan-voiceover are blocked until the media gets a date or an explicit legacy assignment.
- A legacy-day scope is executable because its original label is unambiguous, but the UI must show
  that its date is unknown.
- If no known scopes exist, `all_days` is disabled and the UI offers date repair or re-analysis.
- A missing date or missing label never becomes `day1` in discovery, plan filtering, or completion.

## 11. User-Facing Design

The Run page replaces the free-text day field with a server-populated selector:

```text
Capture day
[ 10月27日 · 21 clips                     ]
  Original label: day1
  Date source: video metadata
  1 duplicate analysis ignored

[ ] All 9 recognized dates
```

Rules:

- The primary label is the capture date.
- `dayN` is secondary compatibility information.
- A legacy-only scope is shown as "date unknown · original label day3".
- The UI states how many capture-date scopes and legacy scopes were detected.
- Missing dates, ambiguous labels, and ignored duplicates are visible in diagnostics.
- No city names are generated from a date.
- The selected scope is persisted in the run session by canonical ID.
- Completion refresh uses the task-returned scope IDs and selected mode.
- Copy is added to both zh-CN and en catalogs with identical key sets.

## 12. Artifact Compatibility and Migration

### Phase 1: additive identity, legacy filenames

- Add scope metadata to analysis, plan, consistency, and plan-voiceover JSON.
- Keep existing `dayN_plan.json`, `dayN_entity_consistency.json`, and
  `dayN_plan_voiceover.json` paths.
- Resolve every path through a shared scope-to-path helper using the frozen legacy alias.
- Read old artifacts only when their lineage matches the resolved scope. A legacy filename alone
  is not proof of scope identity.
- Do not rewrite or move artifacts during discovery.
- Write the manifest atomically and back up an existing manifest before replacement.

### Phase 2: date-based filenames

A later change may write `2026-10-27_plan.json` and related artifacts, prefer those paths, and
fall back to legacy `dayN` files. That migration is deliberately separate because it affects plan,
consistency, plan voiceover, cut, export, UI links, and user-edited files.

## 13. Security and Safety

- Validate `scope_id` against the server-discovered set and a strict date/legacy/unknown pattern.
- Continue using `safe_basename()` and path containment checks for every artifact path.
- Reject scope IDs containing separators, `..`, control characters, or absolute paths.
- Never use a client-supplied date as an unchecked path.
- Preserve artifact overwrite confirmations and user-edited plan protections.
- Do not log full filesystem paths beyond existing debug behavior.
- Store identity evidence in the manifest as hashes, not raw network paths.

## 14. Testing Strategy

Backend tests:

- capture-date parsing for naive, aware, telemetry, filename-only, and missing dates;
- earlier-date insertion does not change an existing canonical ID or frozen alias;
- duplicate analysis artifacts resolve to one deterministic current artifact;
- preview counts scopes, not raw JSON files;
- the reference-shaped fixture reports 181 deduplicated clips from 362 JSON files;
- missing date plus one unambiguous legacy label creates `legacy-dayN`;
- missing date plus conflicting labels creates `unknown`;
- missing date and label never becomes `day1`;
- `unknown` is excluded from `all_days` and blocked from planning;
- legacy scopes are included in `all_days` and remain executable;
- invalid and stale scope requests are rejected with structured `409`;
- preview, execution, and completion use the same scope IDs;
- input and completion fingerprints are computed from different artifact sets;
- a successful plan-alignment run makes the stage reusable and advances `next_stage`;
- a deliberately drifted scope produces a completion diagnostic instead of another ready loop;
- legacy plans remain readable when lineage matches.

Frontend tests:

- the day control is a selector populated from server options;
- date, legacy alias, duplicate count, and date source render correctly;
- unknown is not included in all-days;
- session restore uses canonical scope ID;
- start sends the previewed scope and fingerprint;
- completion refreshes the submitted scope and advances to the next stage;
- a same-stage completion diagnostic is visible instead of an endless run button;
- zh-CN and en catalogs have identical new keys.

Regression fixture:

- a synthetic project with two dates, two analysis artifacts per source, one missing label, one
  legacy-only source, and one undated/unlabeled clip. Assert two capture-date scopes, one legacy
  scope, and one unknown bucket; assert deduplicated clip totals; assert planning is blocked only
  for the unknown bucket.

## 15. Rollout and Rollback

1. Ship the read-only resolver and reference-project audit with no user-visible path change.
2. Add analysis metadata and manifest alias adoption.
3. Switch plan, consistency, and voiceover resolution to canonical scopes.
4. Add the preview/start scope contract and selector.
5. Bind completion to task-returned scopes and add the loop diagnostic.
6. Remove legacy default-`day1` fallbacks after the compatibility path is verified.

Rollback is safe because metadata is additive and legacy artifacts remain in place. The manifest
can be ignored by older code. Phase 2 filename migration must not begin until the manifest and
legacy fallback have been verified on the reference project.

## 16. Alternatives Considered

### Keep sorted `dayN` and only improve copy

Rejected. It leaves scope drift, duplicate counting, and completion mismatch unchanged.

### Use a random UUID for each scope

Rejected. It is stable but not understandable and would require a separate user mapping for every
run.

### Use date plus a hash of all clips

Rejected as the primary ID. The scope would change whenever a clip is added or re-analyzed, which
would break artifact reuse. A media fingerprint remains useful as a preview/start consistency
check, not as the user-facing day identity.

### Put every undated legacy label into one `unknown` bucket

Rejected. It would destroy the existing nine-way separation and make plans impossible to locate.
An unambiguous legacy label is represented by a frozen `legacy-dayN` scope instead.

### Delete duplicate analysis files immediately

Rejected. It is destructive and may remove the only valid artifact if selection is wrong. Report,
exclude, and offer explicit cleanup instead.

## 17. Acceptance Criteria

- The reference project reports 181 deduplicated clips and nine executable scopes, not 362 raw
  analysis files.
- Date metadata is persisted; if ffprobe cannot recover a date, the unambiguous legacy labels are
  exposed as `legacy-dayN` scopes rather than being collapsed into `unknown`.
- No undated or unlabeled clip is implicitly assigned to `day1`.
- Adding an earlier date does not rename existing canonical scopes or their compatibility aliases.
- After a successful plan-alignment run for a selected scope, preview marks the stage reusable and
  `next_stage` advances to the next incomplete stage.
- If completion validation still finds the stage ready, the UI shows a concrete diagnostic instead
  of allowing an unexplained endless rerun.
- `all_days` excludes unknown media with a visible warning and includes unambiguous legacy scopes.
- Existing legacy plans and consistency reports remain readable when their lineage matches.
- Invalid scope IDs cannot escape the project artifact directories.
- zh-CN and en catalogs contain the same new keys.
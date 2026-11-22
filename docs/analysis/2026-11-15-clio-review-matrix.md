# CLIO Review Issue and Optimization Matrix (2026-11-15)

Baseline: `aef5b0a96692a93893f680158a30044dfc867e53`. This matrix records the historical review
baseline and its original reproduction evidence. Follow-up remediation resolved R01/R02 and
local U01-U10; credential work resolved R11. R03-R10 and R12-R17 remain open unless a later
review explicitly changes their status. Current verification is in the pipeline reliability
assessment.

Read with the [final assessment](2026-11-15-clio-final-review.md), [module review](2026-11-15-clio-module-review.md), and [verification record](2026-11-15-clio-review-evidence/verification.md). A [CSV version](2026-11-15-clio-review-matrix.csv) supports sorting and assignment.

## Interpretation

- P0: demonstrated broad catastrophic loss or critical exploitable compromise; none established in this audit.
- P1: data integrity, artifact access boundary, or active-run reliability; address before widening multi-project/concurrent use.
- P2: incorrect supported behavior or a material validation/reproducibility gap.
- P3: maintainability, observability, documentation, or incremental assurance improvement.
- R rows are reproduced defects. O rows are measured/static improvements or explicit validation gaps; they are not additional reproduced failures.
- S/M/L are relative implementation effort, not calendar estimates. Assign an owner and a focused implementation plan before scheduling.
- Python/Frontend probe IDs refer to the evidence directory. These probes assert the current defect and must be inverted into normal regression expectations when fixing it.
- This matrix uses local R01-R17/O01-O10 identifiers; historical R001/OPT identifiers belong to their own dated tracker. O01/O02 overlap its two open items and must not be counted twice.

## Confirmed defects

17 rows: P1=8, P2=9.

| ID | Priority | Area | Problem | Code location | Effort |
| --- | --- | --- | --- | --- | --- |
| [R01](#r01) | P1 | Guided execution / compression | Compression preview counts an output with a matching source stem as reusable even when runtime compression rejects its metadata or changed settings. Guided stage selection can omit the required compression work. | [`clio/ui/services/run_preview.py:773`](../../clio/ui/services/run_preview.py#L773); [`clio/ui/services/run_preview.py:1089`](../../clio/ui/services/run_preview.py#L1089); [`clio/tasks/compress.py:95`](../../clio/tasks/compress.py#L95) | M |
| [R02](#r02) | P2 | Compression cache | Compression fingerprints and stored settings omit codec and effective CRF; changing those options can reuse an output encoded with old parameters. | [`clio/tasks/compress.py:56`](../../clio/tasks/compress.py#L56); [`clio/tasks/compress.py:67`](../../clio/tasks/compress.py#L67); [`clio/tasks/compress.py:375`](../../clio/tasks/compress.py#L375); [`clio/compress.py:125`](../../clio/compress.py#L125); [`clio/compress.py:155`](../../clio/compress.py#L155) | S |
| [R03](#r03) | P1 | Refine backend / concurrency | Refine reads JSON before a long AI call and saves without an expected version, overwriting intervening editor changes. Busy check and claim are separate operations, and the flag is cleared before the final write. | [`clio/ui/routes/refine.py:22`](../../clio/ui/routes/refine.py#L22); [`clio/ui/routes/refine.py:35`](../../clio/ui/routes/refine.py#L35); [`clio/ui/routes/refine.py:90`](../../clio/ui/routes/refine.py#L90); [`clio/ui/services/file_service.py:116`](../../clio/ui/services/file_service.py#L116) | M |
| [R04](#r04) | P1 | Refine frontend | A delayed refinement response for video A unconditionally replaces the shared texts/voiceover buffer after the user switches to video B. The later render guard does not protect the data buffer. | [`clio/ui/static/src/editor-refine.js:27`](../../clio/ui/static/src/editor-refine.js#L27); [`clio/ui/static/src/editor-refine.js:54`](../../clio/ui/static/src/editor-refine.js#L54) | M |
| [R05](#r05) | P1 | Plan navigation | selectPlan lacks a request-generation guard. A slow earlier day request can replace the plan and preview mapping after a later day has already loaded. | [`clio/ui/static/src/sidebar.js:114`](../../clio/ui/static/src/sidebar.js#L114) | S |
| [R06](#r06) | P1 | Editor save lifecycle | Save generation changes only when another save starts. A successful pending save clears dirty even if the user made a newer edit or navigated to another editor while the request was running. | [`clio/ui/static/src/editor-plan-persistence.js:84`](../../clio/ui/static/src/editor-plan-persistence.js#L84); [`clio/ui/static/src/editor-plan-persistence.js:115`](../../clio/ui/static/src/editor-plan-persistence.js#L115); [`clio/ui/static/src/editor-plan-persistence.js:164`](../../clio/ui/static/src/editor-plan-persistence.js#L164) | M |
| [R07](#r07) | P2 | Configured artifact directories | Producers and artifact indexing support configurable subdirectories, but several Web readers still hard-code texts/scripts/compressed. Artifacts can exist or be listed yet be unreadable or unplayable. | [`clio/ui/services/file_service.py:615`](../../clio/ui/services/file_service.py#L615); [`clio/ui/services/file_service.py:626`](../../clio/ui/services/file_service.py#L626); [`clio/ui/handler_factory.py:449`](../../clio/ui/handler_factory.py#L449); [`clio/ui/routes/videos.py:214`](../../clio/ui/routes/videos.py#L214); [`clio/ui/routes/videos.py:741`](../../clio/ui/routes/videos.py#L741); [`clio/ui/routes/waveform.py:36`](../../clio/ui/routes/waveform.py#L36); [`clio/config/models.py:649`](../../clio/config/models.py#L649) | M |
| [R08](#r08) | P1 | Artifact path security | Texts/scripts resolution validates the basename but follows filesystem symlinks without the project's containment/symlink validation. A planted link can expose a file outside the artifact root to these route consumers. | [`clio/ui/services/file_service.py:615`](../../clio/ui/services/file_service.py#L615); [`clio/ui/services/file_service.py:626`](../../clio/ui/services/file_service.py#L626); [`clio/ui/handler_factory.py:449`](../../clio/ui/handler_factory.py#L449); [`clio/ui/routes/refine.py:54`](../../clio/ui/routes/refine.py#L54) | M |
| [R09](#r09) | P2 | JianYing auto-copy completeness | Auto-copy copies only draft_content.json even though export emits draft_meta_info.json and subtitles.srt. A fresh destination lacks the generated discovery metadata; an existing destination can keep stale metadata. | [`clio/ui/routes/export.py:45`](../../clio/ui/routes/export.py#L45); [`clio/export/jianying.py:452`](../../clio/export/jianying.py#L452); [`clio/export/jianying.py:569`](../../clio/export/jianying.py#L569) | M |
| [R10](#r10) | P1 | JianYing destination protection | Auto-copy uses the same vlog_export_<day> destination and replaces existing draft content without a destination overwrite gate or backup. Re-exporting can replace manual editing work; different projects with day1 share the same destination name. | [`clio/ui/routes/export.py:60`](../../clio/ui/routes/export.py#L60); [`clio/ui/routes/export.py:69`](../../clio/ui/routes/export.py#L69); [`clio/ui/routes/export.py:98`](../../clio/ui/routes/export.py#L98) | M |
| [R11](#r11) | P1 | Provider lifetime / config reload | Provider TTL retirement respects active_calls, but explicit cache clearing closes active providers immediately. Configuration/environment saves call this path and can interrupt in-flight AI requests. | [`clio/ai/factory.py:51`](../../clio/ai/factory.py#L51); [`clio/ai/factory.py:138`](../../clio/ai/factory.py#L138); [`clio/ui/routes/config_routes.py:159`](../../clio/ui/routes/config_routes.py#L159); [`clio/ui/routes/env_routes.py:211`](../../clio/ui/routes/env_routes.py#L211) | M |
| [R12](#r12) | P2 | Usage/cost accounting | FileTokenUsageStore protects read-modify-write with an instance lock, but independent store instances target the same file. Atomic file replacement cannot prevent their lost updates. | [`clio/ai/token_usage.py:98`](../../clio/ai/token_usage.py#L98); [`clio/ai/token_usage.py:114`](../../clio/ai/token_usage.py#L114); [`clio/ui/routes/refine.py:71`](../../clio/ui/routes/refine.py#L71) | M |
| [R13](#r13) | P2 | Canonical JSON / human-readable sidecars | Web plan saves update JSON without regenerating Markdown, and Web refine saves JSON without refreshing the texts/scripts sidecars. Files representing the same artifact can disagree. | [`clio/ui/routes/plan.py:196`](../../clio/ui/routes/plan.py#L196); [`clio/ui/routes/refine.py:90`](../../clio/ui/routes/refine.py#L90); [`clio/ui/routes/texts.py`](../../clio/ui/routes/texts.py) | M |
| [R14](#r14) | P2 | CLI configuration check | main.py check requires an API key for every bound provider, including keyless Ollama configurations that the provider implementation supports. | [`clio/main.py:137`](../../clio/main.py#L137); [`clio/ai/openai_compat.py`](../../clio/ai/openai_compat.py) | S |
| [R15](#r15) | P2 | Media index identity | Compression groups records by full source path but VideoIndex.write names each index using source_stem alone. Two distinct same-basename sources overwrite one .vindex even though their compressed files have distinct numeric slots. | [`clio/tasks/compress.py:198`](../../clio/tasks/compress.py#L198); [`clio/tasks/compress.py:234`](../../clio/tasks/compress.py#L234); [`clio/vmeta.py:160`](../../clio/vmeta.py#L160); [`clio/tasks/analyze.py:1342`](../../clio/tasks/analyze.py#L1342) | M |
| [R16](#r16) | P2 | Source freshness | VideoMeta and VideoIndex truncate source modification time to integer seconds and rely on size. Equal-length changes within one second remain reusable despite a changed source timestamp/content. | [`clio/vmeta.py:62`](../../clio/vmeta.py#L62); [`clio/vmeta.py:108`](../../clio/vmeta.py#L108); [`clio/vmeta.py:154`](../../clio/vmeta.py#L154); [`clio/vmeta.py:221`](../../clio/vmeta.py#L221) | M |
| [R17](#r17) | P2 | Project identity in media URLs | Direct video URLs carry the project name but omit the selected project_dir, although normal API calls support that unambiguous selector. Same-name registered projects therefore fail playback resolution with 409. | [`clio/ui/static/src/sidebar.js:49`](../../clio/ui/static/src/sidebar.js#L49); [`clio/ui/static/src/video-url.js:15`](../../clio/ui/static/src/video-url.js#L15); [`clio/ui/services/project_service.py:440`](../../clio/ui/services/project_service.py#L440); [`clio/ui/services/project_service.py:515`](../../clio/ui/services/project_service.py#L515) | S |

## Optimization and validation backlog

10 rows: P2=3, P3=7.

| ID | Priority | Area | Problem | Code location | Effort |
| --- | --- | --- | --- | --- | --- |
| [O01](#o01) | P3 | Frontend typing | Typecheck includes only 10 of 81 production JS entry files and uses checkJs=false/strict=false. A green typecheck provides limited assurance for the mutable editor/navigation surfaces. | [`tsconfig.json:4`](../../tsconfig.json#L4); [`tsconfig.json:16`](../../tsconfig.json#L16); [`clio/ui/static/src/state.js`](../../clio/ui/static/src/state.js) | M |
| [O02](#o02) | P3 | Stable artifact naming | Artifact stems incorporate generated titles; title changes increase matching complexity and can leave parallel historical files across analysis/scripts/labels/covers. | [`clio/tasks/_helpers.py:389`](../../clio/tasks/_helpers.py#L389); [`clio/tasks/analyze.py`](../../clio/tasks/analyze.py); [`clio/index.py`](../../clio/index.py) | L |
| [O03](#o03) | P2 | Frontend behavioral assurance | Frontend global thresholds are far below current coverage, while important editor/viewer paths remain weakly exercised. Happy-path tests miss response reordering and edit-during-save behavior. | [`vitest.config.js:17`](../../vitest.config.js#L17); [`clio/ui/static/src/editor-plan.js`](../../clio/ui/static/src/editor-plan.js); [`clio/ui/static/src/viewer.js`](../../clio/ui/static/src/viewer.js); [`clio/ui/static/src/__tests__/`](../../clio/ui/static/src/__tests__/) | M |
| [O04](#o04) | P2 | Release reproducibility | Release builds install unpinned build/GUI dependencies; macOS uses requirements.txt rather than a platform lock. The macOS installer is explicitly pinned to Python 3.11.9. Reproducing the exact shipped dependency set is difficult. | [`.github/workflows/release.yml:91`](../../.github/workflows/release.yml#L91); [`.github/workflows/release.yml:177`](../../.github/workflows/release.yml#L177); [`.github/workflows/release.yml:209`](../../.github/workflows/release.yml#L209); [`requirements-locked.txt`](../../requirements-locked.txt); [`packaging/clio.spec`](../../packaging/clio.spec) | L |
| [O05](#o05) | P2 | Product-level acceptance | Unit/integration success and a real-ffmpeg fake-provider smoke do not establish real AI output fidelity, browser media behavior or actual JianYing import compatibility. | [`clio/tests/test_e2e_ffmpeg_smoke.py`](../../clio/tests/test_e2e_ffmpeg_smoke.py); [`clio/evaluation.py`](../../clio/evaluation.py); [`clio/export/jianying.py`](../../clio/export/jianying.py); [`clio/desktop/`](../../clio/desktop/) | M |
| [O06](#o06) | P3 | Architecture seams | Large orchestration modules and private cross-layer helper imports make cache, persistence and identity contracts easy to reimplement inconsistently. | [`clio/analyze.py`](../../clio/analyze.py); [`clio/tasks/analyze.py`](../../clio/tasks/analyze.py); [`clio/ui/services/run_preview.py`](../../clio/ui/services/run_preview.py); [`clio/ui/routes/videos.py`](../../clio/ui/routes/videos.py); [`clio/utils.py`](../../clio/utils.py) | L |
| [O07](#o07) | P3 | Configuration cache policy | ConfigCache describes LRU eviction but cache hits do not refresh insertion order, so eviction follows insertion order for unchanged entries. | [`clio/ui/services/config_cache.py:28`](../../clio/ui/services/config_cache.py#L28); [`clio/ui/services/config_cache.py:66`](../../clio/ui/services/config_cache.py#L66); [`clio/ui/services/config_cache.py:78`](../../clio/ui/services/config_cache.py#L78) | S |
| [O08](#o08) | P3 | Pipeline observability | Pipeline processed counts are inferred from return shape: integer 0 counts as one processed item, None as zero, and a multi-day mapping as one. warning_count starts at zero without a common warning aggregation contract. | [`clio/pipeline.py:150`](../../clio/pipeline.py#L150); [`clio/pipeline.py:160`](../../clio/pipeline.py#L160); [`clio/pipeline.py:203`](../../clio/pipeline.py#L203); [`clio/tasks/transcribe.py`](../../clio/tasks/transcribe.py); [`clio/tasks/label.py`](../../clio/tasks/label.py) | M |
| [O09](#o09) | P3 | Maintenance documentation | Operational references retain instructions contradicted by current code: loader-time default injection and Gemini upload deletion. A future maintainer could reintroduce already-fixed behavior. | [`docs/superpowers/agents/maintenance-instructions.md:7`](../../docs/superpowers/agents/maintenance-instructions.md#L7); [`docs/superpowers/agents/gotchas.md:42`](../../docs/superpowers/agents/gotchas.md#L42); [`clio/ai/gemini.py:552`](../../clio/ai/gemini.py#L552) | S |
| [O10](#o10) | P3 | Capacity/performance evidence | No new scale benchmark was run for many clips, long videos, concurrent projects, retained task history or repeated waveform requests. Per-submission task threads and filesystem scans warrant measured capacity limits. | [`clio/task_center/manager.py:126`](../../clio/task_center/manager.py#L126); [`clio/task_center/manager.py:159`](../../clio/task_center/manager.py#L159); [`clio/ui/routes/videos.py:296`](../../clio/ui/routes/videos.py#L296); [`clio/ui/routes/waveform.py`](../../clio/ui/routes/waveform.py) | M |

## Detailed repair and acceptance contracts

### R01 — fixed in follow-up; original evidence retained

**P1 — Guided execution / compression**. Fixed in the pipeline follow-up. The historical
reproduction below records the pre-fix behavior.

**Problem:** Compression preview counts an output with a matching source stem as reusable even when runtime compression rejects its metadata or changed settings. Guided stage selection can omit the required compression work.

**Location:** [`clio/ui/services/run_preview.py:773`](../../clio/ui/services/run_preview.py#L773); [`clio/ui/services/run_preview.py:1089`](../../clio/ui/services/run_preview.py#L1089); [`clio/tasks/compress.py:95`](../../clio/tasks/compress.py#L95)

**Evidence and limits:** Python R01: change max_width after writing valid vmeta; preview will_skip=1 while _find_reusable returns no reusable path. Frontend stage omission follows from the preview contract; a live browser pipeline was not run.

**Recommended repair:** Share a read-only compression reuse predicate between preview and runtime, using source identity, metadata freshness, effective settings, probe validity and skip_existing/force policy.

**Acceptance criteria:** Preview and execution agree for changed settings/source, corrupt or missing output, duplicate basenames, skip_existing=false and force=true. Preview does not mutate artifacts.

**Coordination / effort:** Coordinate with R02/R15/R16; M.

### R02 — fixed in follow-up; original evidence retained

**P2 — Compression cache**. Fixed in the pipeline follow-up. The historical reproduction below
records the pre-fix behavior.

**Problem:** Compression fingerprints and stored settings omit codec and effective CRF; changing those options can reuse an output encoded with old parameters.

**Location:** [`clio/tasks/compress.py:56`](../../clio/tasks/compress.py#L56); [`clio/tasks/compress.py:67`](../../clio/tasks/compress.py#L67); [`clio/tasks/compress.py:375`](../../clio/tasks/compress.py#L375); [`clio/compress.py:125`](../../clio/compress.py#L125); [`clio/compress.py:155`](../../clio/compress.py#L155)

**Evidence and limits:** Python R02: in CRF mode, changing both codec and CRF leaves the fingerprint and metadata matcher unchanged.

**Recommended repair:** Version the effective compression settings and reuse one serializer for metadata writing and matching. Include codec and mode-relevant CRF; retain safe legacy reads.

**Acceptance criteria:** Codec changes invalidate reuse; CRF changes invalidate CRF-mode reuse; irrelevant CRF changes in target-size mode do not. Legacy metadata remains readable with an explicit regeneration policy.

**Coordination / effort:** R01 shared predicate; S.

### R03

**P1 — Refine backend / concurrency**. Open; reproduced.

**Problem:** Refine reads JSON before a long AI call and saves without an expected version, overwriting intervening editor changes. Busy check and claim are separate operations, and the flag is cleared before the final write.

**Location:** [`clio/ui/routes/refine.py:22`](../../clio/ui/routes/refine.py#L22); [`clio/ui/routes/refine.py:35`](../../clio/ui/routes/refine.py#L35); [`clio/ui/routes/refine.py:90`](../../clio/ui/routes/refine.py#L90); [`clio/ui/services/file_service.py:116`](../../clio/ui/services/file_service.py#L116)

**Evidence and limits:** Python R03: a provider callback performs a real versioned manual save; refine subsequently returns success and replaces it with the AI result. Existing backup behavior can retain recovery copies; this is not a claim that all history is destroyed.

**Recommended repair:** Capture the input content version and compare-and-swap on completion. Atomically claim refinement through commit; integrate project artifact concurrency and cancellation with managed tasks.

**Acceptance criteria:** A concurrent edit causes a clear 409 without replacing it; two refinements cannot both claim one file; cancellation/error releases ownership; the busy lifetime includes persistence. Normal refinement still succeeds.

**Coordination / effort:** Coordinate with R04/R13; M.

### R04

**P1 — Refine frontend**. Open; reproduced.

**Problem:** A delayed refinement response for video A unconditionally replaces the shared texts/voiceover buffer after the user switches to video B. The later render guard does not protect the data buffer.

**Location:** [`clio/ui/static/src/editor-refine.js:27`](../../clio/ui/static/src/editor-refine.js#L27); [`clio/ui/static/src/editor-refine.js:54`](../../clio/ui/static/src/editor-refine.js#L54)

**Evidence and limits:** Frontend R04: switch currentVideo to B before resolving refinement A; active state.texts becomes AI A. A subsequent B save could persist the wrong content; that later save was not performed by the probe.

**Recommended repair:** Bind response application to project, artifact, entity, editor revision and request generation. Store inactive results by artifact identity or require a fresh load, while preserving newer edits.

**Acceptance criteria:** Delayed A success/error cannot change B content, version, dirty flag or status. Switching projects or editing A while refinement runs also preserves current content. Returning to A loads its correct saved result.

**Coordination / effort:** Coordinate with R03/R06; M.

### R05

**P1 — Plan navigation**. Open; reproduced.

**Problem:** selectPlan lacks a request-generation guard. A slow earlier day request can replace the plan and preview mapping after a later day has already loaded.

**Location:** [`clio/ui/static/src/sidebar.js:114`](../../clio/ui/static/src/sidebar.js#L114)

**Evidence and limits:** Frontend R05: start day1 then day2, resolve day2 then day1; currentDay remains day2 while state.plan contains Day 1.

**Recommended repair:** Capture project/day/entity identity and a selection generation; apply both success and failure only for the current selection. Guard version and preview state together.

**Acceptance criteria:** Out-of-order loads, errors, project changes and plan-to-video navigation never replace the current plan or its media mapping/version; saving day2 cannot submit day1 data.

**Coordination / effort:** Coordinate with R06; S.

### R06

**P1 — Editor save lifecycle**. Open; reproduced.

**Problem:** Save generation changes only when another save starts. A successful pending save clears dirty even if the user made a newer edit or navigated to another editor while the request was running.

**Location:** [`clio/ui/static/src/editor-plan-persistence.js:84`](../../clio/ui/static/src/editor-plan-persistence.js#L84); [`clio/ui/static/src/editor-plan-persistence.js:115`](../../clio/ui/static/src/editor-plan-persistence.js#L115); [`clio/ui/static/src/editor-plan-persistence.js:164`](../../clio/ui/static/src/editor-plan-persistence.js#L164)

**Evidence and limits:** Frontend R06: submit a texts save, replace the buffer with a later dirty edit, then resolve success; the unsaved later content remains but dirty becomes false.

**Recommended repair:** Snapshot the submitted content and track edit revision plus full save-target identity. Clear dirty only if the active target/revision still equals the submitted revision; retain the returned version for the saved target.

**Acceptance criteria:** Edits during saves remain dirty for plan/texts/voiceover/config. Navigation and out-of-order saves do not clear another target's dirty state. Conflict handling preserves unsaved content.

**Coordination / effort:** Shared editor revision model useful for R04/R05; M.

### R07

**P2 — Configured artifact directories**. Open; reproduced.

**Problem:** Producers and artifact indexing support configurable subdirectories, but several Web readers still hard-code texts/scripts/compressed. Artifacts can exist or be listed yet be unreadable or unplayable.

**Location:** [`clio/ui/services/file_service.py:615`](../../clio/ui/services/file_service.py#L615); [`clio/ui/services/file_service.py:626`](../../clio/ui/services/file_service.py#L626); [`clio/ui/handler_factory.py:449`](../../clio/ui/handler_factory.py#L449); [`clio/ui/routes/videos.py:214`](../../clio/ui/routes/videos.py#L214); [`clio/ui/routes/videos.py:741`](../../clio/ui/routes/videos.py#L741); [`clio/ui/routes/waveform.py:36`](../../clio/ui/routes/waveform.py#L36); [`clio/config/models.py:649`](../../clio/config/models.py#L649)

**Evidence and limits:** Python R07: files under configured custom texts/scripts directories exist while Web resolvers return None. Hard-coded video/waveform roots were verified statically.

**Recommended repair:** Use configuration-aware artifact/media resolution in all route families, with existing legacy read-only fallbacks and containment checks.

**Acceptance criteria:** Default and nested custom subdirectories work across generation, listing, GET/PUT/refine, playback, metadata and waveform. Legacy artifacts remain readable and traversal/symlinks remain rejected.

**Coordination / effort:** Implement with R08; M.

### R08

**P1 — Artifact path security**. Open; reproduced.

**Problem:** Texts/scripts resolution validates the basename but follows filesystem symlinks without the project's containment/symlink validation. A planted link can expose a file outside the artifact root to these route consumers.

**Location:** [`clio/ui/services/file_service.py:615`](../../clio/ui/services/file_service.py#L615); [`clio/ui/services/file_service.py:626`](../../clio/ui/services/file_service.py#L626); [`clio/ui/handler_factory.py:449`](../../clio/ui/handler_factory.py#L449); [`clio/ui/routes/refine.py:54`](../../clio/ui/routes/refine.py#L54)

**Evidence and limits:** Python R08: scripts/linked.json points outside the output root; resolve_in accepts and reads it, while validate_within_root rejects it. This requires a planted link and route access; no unauthenticated Internet exploit or arbitrary-write exploit is claimed.

**Recommended repair:** Validate the candidate's lexical path, resolved containment and symlink components through the existing shared policy before any read or write consumer uses it. Return a consistent forbidden response.

**Acceptance criteria:** Final and intermediate symlinks escaping the artifact root are rejected by GET/PUT/refine routes; ordinary artifacts and permitted legacy locations still work. Windows-capable and Unix tests exercise the same policy.

**Coordination / effort:** R07 resolver consolidation; M.

### R09

**P2 — JianYing auto-copy completeness**. Open; reproduced.

**Problem:** Auto-copy copies only draft_content.json even though export emits draft_meta_info.json and subtitles.srt. A fresh destination lacks the generated discovery metadata; an existing destination can keep stale metadata.

**Location:** [`clio/ui/routes/export.py:45`](../../clio/ui/routes/export.py#L45); [`clio/export/jianying.py:452`](../../clio/export/jianying.py#L452); [`clio/export/jianying.py:569`](../../clio/export/jianying.py#L569)

**Evidence and limits:** Python R09: a three-file generated bundle becomes a one-file copied destination. Actual JianYing discovery/import was not tested, so universal app-level failure is not asserted.

**Recommended repair:** Define and copy the complete supported draft bundle, rewriting destination-dependent metadata and identifiers as needed; publish the bundle only after all files are ready.

**Acceptance criteria:** Fresh and existing destinations contain consistent content/metadata/subtitles with correct destination paths. Verify discoverability and import in the documented supported JianYing version and record that version.

**Coordination / effort:** Implement with R10; desktop import validation required; M.

### R10

**P1 — JianYing destination protection**. Open; reproduced.

**Problem:** Auto-copy uses the same vlog_export_<day> destination and replaces existing draft content without a destination overwrite gate or backup. Re-exporting can replace manual editing work; different projects with day1 share the same destination name.

**Location:** [`clio/ui/routes/export.py:60`](../../clio/ui/routes/export.py#L60); [`clio/ui/routes/export.py:69`](../../clio/ui/routes/export.py#L69); [`clio/ui/routes/export.py:98`](../../clio/ui/routes/export.py#L98)

**Evidence and limits:** Python R10: edit destination draft_content.json, export again, and observe replacement with no backup in the destination. Cross-project collision follows statically from the day-only name.

**Recommended repair:** Create a new project-scoped draft identity/directory by default. Explicit replacement must require confirmation and retain a recoverable complete backup; stage publication to handle failures.

**Acceptance criteria:** Repeat exports and two projects sharing day1 preserve prior manually edited drafts. Explicit confirmed replacement is recoverable. Failure/cancellation does not leave a half-published destination.

**Coordination / effort:** R09 complete bundle; M.

### R11 — fixed by credential retirement work; original evidence retained

**P1 — Provider lifetime / config reload**. Fixed by credential retirement work. The historical
reproduction below records the pre-fix behavior.

**Problem:** Provider TTL retirement respects active_calls, but explicit cache clearing closes active providers immediately. Configuration/environment saves call this path and can interrupt in-flight AI requests.

**Location:** [`clio/ai/factory.py:51`](../../clio/ai/factory.py#L51); [`clio/ai/factory.py:138`](../../clio/ai/factory.py#L138); [`clio/ui/routes/config_routes.py:159`](../../clio/ui/routes/config_routes.py#L159); [`clio/ui/routes/env_routes.py:211`](../../clio/ui/routes/env_routes.py#L211)

**Evidence and limits:** Python R11: within provider_use, cache clearing invokes provider.close before the active lease exits. A live network failure was not induced.

**Recommended repair:** Invalidate lookup entries immediately but retire active providers until their last lease exits. Separate graceful invalidation from explicit process shutdown and cover the provider acquisition-to-lease handoff.

**Acceptance criteria:** Saving config during an active provider call does not close that call's client; new calls use new settings; old clients close exactly once after all users exit, including exceptional exits.

**Coordination / effort:** No other finding required; M.

### R12

**P2 — Usage/cost accounting**. Open; reproduced.

**Problem:** FileTokenUsageStore protects read-modify-write with an instance lock, but independent store instances target the same file. Atomic file replacement cannot prevent their lost updates.

**Location:** [`clio/ai/token_usage.py:98`](../../clio/ai/token_usage.py#L98); [`clio/ai/token_usage.py:114`](../../clio/ai/token_usage.py#L114); [`clio/ui/routes/refine.py:71`](../../clio/ui/routes/refine.py#L71)

**Evidence and limits:** Python R12: synchronize two independent writers after reading; both calls succeed but persisted history contains only one call.

**Recommended repair:** Serialize the entire transaction by canonical storage path across instances; use a file lock or transactional store if concurrent processes are supported. Preserve bounded history and cumulative totals.

**Acceptance criteria:** N writers across distinct instances preserve all counts, tokens and costs; history retention limits do not truncate cumulative totals. Document and test the supported cross-process policy.

**Coordination / effort:** Can be fixed independently; M.

### R13

**P2 — Canonical JSON / human-readable sidecars**. Open; reproduced.

**Problem:** Web plan saves update JSON without regenerating Markdown, and Web refine saves JSON without refreshing the texts/scripts sidecars. Files representing the same artifact can disagree.

**Location:** [`clio/ui/routes/plan.py:196`](../../clio/ui/routes/plan.py#L196); [`clio/ui/routes/refine.py:90`](../../clio/ui/routes/refine.py#L90); [`clio/ui/routes/texts.py`](../../clio/ui/routes/texts.py)

**Evidence and limits:** Python R13: plan PUT changes day_title in JSON while the old Markdown remains unchanged. R03 also observes no TXT generated by successful texts refinement.

**Recommended repair:** Centralize canonical artifact persistence and derived rendering, or generate sidecars on explicit export from canonical JSON. Ensure version-safe updates and report/recover failed derivative generation.

**Acceptance criteria:** After plan/texts/scripts saves and refinement, any advertised MD/TXT contains the current canonical content. Conflict/error paths preserve user content and cannot publish derivatives for another revision.

**Coordination / effort:** R03 persistence concurrency; M.

### R14

**P2 — CLI configuration check**. Open; reproduced.

**Problem:** main.py check requires an API key for every bound provider, including keyless Ollama configurations that the provider implementation supports.

**Location:** [`clio/main.py:137`](../../clio/main.py#L137); [`clio/ai/openai_compat.py`](../../clio/ai/openai_compat.py)

**Evidence and limits:** Python R14: with all other checks patched healthy, one local Ollama task with no key produces exit code 1. The user's current cloud/local configuration check separately passes.

**Recommended repair:** Share capability/type-aware credential validation with provider construction and doctor readiness rather than imposing a universal key requirement.

**Acceptance criteria:** Valid keyless Ollama bindings pass check; missing credentials for providers requiring them still fail with task/provider-specific guidance; mixed provider configs behave consistently.

**Coordination / effort:** Can be fixed independently; S.

### R15

**P2 — Media index identity**. Open; reproduced.

**Problem:** Compression groups records by full source path but VideoIndex.write names each index using source_stem alone. Two distinct same-basename sources overwrite one .vindex even though their compressed files have distinct numeric slots.

**Location:** [`clio/tasks/compress.py:198`](../../clio/tasks/compress.py#L198); [`clio/tasks/compress.py:234`](../../clio/tasks/compress.py#L234); [`clio/vmeta.py:160`](../../clio/vmeta.py#L160); [`clio/tasks/analyze.py:1342`](../../clio/tasks/analyze.py#L1342)

**Evidence and limits:** Python R15: _write_vindex receives camera-a/shared.mp4 and camera-b/shared.mp4; shared.vindex retains only camera-b and 002_shared.mp4. .vmeta still exists as an independent identity source, so this is not a claim that every downstream operation misidentifies media.

**Recommended repair:** Use a stable disambiguated source identity for new index storage and resolve it through shared MediaIdentity helpers; keep legacy stem-index reads with explicit ambiguity handling.

**Acceptance criteria:** Two or more same-basename sources retain separate complete indexes across compress, reindex, verify and single-file analysis; ambiguous legacy lookup never selects another source silently.

**Coordination / effort:** Coordinate with R01 and legacy compatibility; M.

### R16

**P2 — Source freshness**. Open; reproduced.

**Problem:** VideoMeta and VideoIndex truncate source modification time to integer seconds and rely on size. Equal-length changes within one second remain reusable despite a changed source timestamp/content.

**Location:** [`clio/vmeta.py:62`](../../clio/vmeta.py#L62); [`clio/vmeta.py:108`](../../clio/vmeta.py#L108); [`clio/vmeta.py:154`](../../clio/vmeta.py#L154); [`clio/vmeta.py:221`](../../clio/vmeta.py#L221)

**Evidence and limits:** Python R16: rewrite a six-byte source and move mtime from .1 to .9 seconds in the same second; VideoMeta.is_stale(source,target) remains false.

**Recommended repair:** Version source freshness metadata with mtime_ns and size, and define a stronger content-signature option for workflows preserving timestamps. Retain safe reads for legacy second-resolution fields.

**Acceptance criteria:** Same-size sub-second edits invalidate metadata and reuse. Legacy metadata migration/regeneration is deterministic. Document that preserved timestamps require a content signature and that sampling is not a full integrity hash.

**Coordination / effort:** R01 compression reuse; M.

### R17

**P2 — Project identity in media URLs**. Open; reproduced.

**Problem:** Direct video URLs carry the project name but omit the selected project_dir, although normal API calls support that unambiguous selector. Same-name registered projects therefore fail playback resolution with 409.

**Location:** [`clio/ui/static/src/sidebar.js:49`](../../clio/ui/static/src/sidebar.js#L49); [`clio/ui/static/src/video-url.js:15`](../../clio/ui/static/src/video-url.js#L15); [`clio/ui/services/project_service.py:440`](../../clio/ui/services/project_service.py#L440); [`clio/ui/services/project_service.py:515`](../../clio/ui/services/project_service.py#L515)

**Evidence and limits:** Frontend R17 confirms the selected directory is absent from the player URL. Static backend review confirms duplicate names are deliberately rejected with 409, rather than silently choosing a project.

**Recommended repair:** Extend and use the shared media URL builder to propagate canonical project directory identity consistently across player, preloader and cut preview, preserving auth and media parameters.

**Acceptance criteria:** Two registered projects with the same name each play their selected original/compressed/cut media. Player and preloader URLs agree on directory, token, absolute source, day and revision parameters.

**Coordination / effort:** Custom compressed directories also require R07; S.

### O01

**P3 — Frontend typing**. Open; measured; overlaps historical OPT-022.

**Problem:** Typecheck includes only 10 of 81 production JS entry files and uses checkJs=false/strict=false. A green typecheck provides limited assurance for the mutable editor/navigation surfaces.

**Location:** [`tsconfig.json:4`](../../tsconfig.json#L4); [`tsconfig.json:16`](../../tsconfig.json#L16); [`clio/ui/static/src/state.js`](../../clio/ui/static/src/state.js)

**Evidence and limits:** Tracked-file inventory counts 81 production JS files; tsconfig explicitly lists 10. Imports may expand the compiler graph, so 10 is the explicit entry count, not an exact graph coverage percentage.

**Recommended repair:** Expand @ts-check/JSDoc incrementally around editor state, API responses and async identity/revision contracts; add modules to the checked set without broad any suppression.

**Acceptance criteria:** Core state/save/navigation/refine modules join typecheck and intentional wrong entity/version assignments fail a compiler fixture or documented local check.

**Coordination / effort:** Supports R04-R06; do not duplicate historical OPT-022; M.

### O02

**P3 — Stable artifact naming**. Open; static; overlaps historical OPT-023.

**Problem:** Artifact stems incorporate generated titles; title changes increase matching complexity and can leave parallel historical files across analysis/scripts/labels/covers.

**Location:** [`clio/tasks/_helpers.py:389`](../../clio/tasks/_helpers.py#L389); [`clio/tasks/analyze.py`](../../clio/tasks/analyze.py); [`clio/index.py`](../../clio/index.py)

**Evidence and limits:** _build_stem uses index plus sanitized title. This is a migration/maintenance risk, not a newly reproduced data-loss incident.

**Recommended repair:** Define stable artifact IDs derived from MediaIdentity and keep titles as display metadata; plan backwards-compatible lookup and explicit orphan reporting before changing names.

**Acceptance criteria:** Retitling a clip does not create a second active artifact identity; old title-based files remain readable; migration is reversible and never deletes user-edited files silently.

**Coordination / effort:** Align with R15; do not duplicate historical OPT-023; L.

### O03

**P2 — Frontend behavioral assurance**. Open; measured.

**Problem:** Frontend global thresholds are far below current coverage, while important editor/viewer paths remain weakly exercised. Happy-path tests miss response reordering and edit-during-save behavior.

**Location:** [`vitest.config.js:17`](../../vitest.config.js#L17); [`clio/ui/static/src/editor-plan.js`](../../clio/ui/static/src/editor-plan.js); [`clio/ui/static/src/viewer.js`](../../clio/ui/static/src/viewer.js); [`clio/ui/static/src/__tests__/`](../../clio/ui/static/src/__tests__/)

**Evidence and limits:** 951 passing tests; statement/branch coverage 61.04%/55.19%; editor-plan 19.79%/14.50%, viewer 38.28%/30.87%. Gates are 25% statements and 22% branches.

**Recommended repair:** Add meaningful delayed-response, cross-project and dirty-revision tests; set achievable per-module ratchets for core workflows and a small real-browser scenario suite.

**Acceptance criteria:** R04-R06/R17 fail before fixes and pass after fixes in normal CI. Core workflow coverage ratchets prevent regressions without arbitrary 100% targets; at least one browser flow exercises real routing and editing.

**Coordination / effort:** Pair with each functional fix; M.

### O04

**P2 — Release reproducibility**. Open; static.

**Problem:** Release builds install unpinned build/GUI dependencies; macOS uses requirements.txt rather than a platform lock. The macOS installer is explicitly pinned to Python 3.11.9. Reproducing the exact shipped dependency set is difficult.

**Location:** [`.github/workflows/release.yml:91`](../../.github/workflows/release.yml#L91); [`.github/workflows/release.yml:177`](../../.github/workflows/release.yml#L177); [`.github/workflows/release.yml:209`](../../.github/workflows/release.yml#L209); [`requirements-locked.txt`](../../requirements-locked.txt); [`packaging/clio.spec`](../../packaging/clio.spec)

**Evidence and limits:** Workflow text installs pyinstaller/pywebview/platform dependencies without a complete platform build lock. Existing attestations and vulnerability CI are strengths; no new vulnerability or failing release is asserted.

**Recommended repair:** Maintain per-platform runtime/build constraints and generate manifests/SBOM from each resolved bundle. Plan a tested runtime migration instead of indefinitely freezing the installer patch level.

**Acceptance criteria:** Windows/macOS builds record all resolved runtime/build versions, verify lock consistency and publish matching artifact-derived manifests. Runtime migration passes native startup/media/dialog checks on both platforms.

**Coordination / effort:** Release environment required; no release triggered by this review; L.

### O05

**P2 — Product-level acceptance**. Open; validation gap.

**Problem:** Unit/integration success and a real-ffmpeg fake-provider smoke do not establish real AI output fidelity, browser media behavior or actual JianYing import compatibility.

**Location:** [`clio/tests/test_e2e_ffmpeg_smoke.py`](../../clio/tests/test_e2e_ffmpeg_smoke.py); [`clio/evaluation.py`](../../clio/evaluation.py); [`clio/export/jianying.py`](../../clio/export/jianying.py); [`clio/desktop/`](../../clio/desktop/)

**Evidence and limits:** This review did not invoke billable providers, open desktop GUI, build packages or import a draft in JianYing. Existing local evaluation fixtures are not evidence for all those boundaries.

**Recommended repair:** Keep a small consented media corpus and a versioned acceptance checklist covering AI temporal accuracy, ASR language/noise, browser preview, subtitles, original offsets and manual import.

**Acceptance criteria:** Record fixture, provider/model, app/browser/OS versions and observed results; require human checks of identity, chronology, hallucinations and editability. Billable tests remain explicitly opt-in.

**Coordination / effort:** R09/R10 before destination overwrite scenarios; M.

### O06

**P3 — Architecture seams**. Open; measured/static.

**Problem:** Large orchestration modules and private cross-layer helper imports make cache, persistence and identity contracts easy to reimplement inconsistently.

**Location:** [`clio/analyze.py`](../../clio/analyze.py); [`clio/tasks/analyze.py`](../../clio/tasks/analyze.py); [`clio/ui/services/run_preview.py`](../../clio/ui/services/run_preview.py); [`clio/ui/routes/videos.py`](../../clio/ui/routes/videos.py); [`clio/utils.py`](../../clio/utils.py)

**Evidence and limits:** Line counts including comments/blanks: 2109, 1664, 1155, 1096 and 1011 respectively. Size alone is not a defect; R01/R07/R13 demonstrate concrete duplicated-policy costs.

**Recommended repair:** Extract narrow artifact-resolution/persistence and cache-policy services as fixes require them. Keep CLI/Web adapters thin and avoid a broad rewrite or a single universal manager.

**Acceptance criteria:** Shared services own the relevant contract; preview/runtime and producer/consumer tests use production entry points. Public behavior and legacy reads stay compatible with full relevant suites green.

**Coordination / effort:** Follow functional fixes; avoid independent speculative refactor; L.

### O07

**P3 — Configuration cache policy**. Open; static.

**Problem:** ConfigCache describes LRU eviction but cache hits do not refresh insertion order, so eviction follows insertion order for unchanged entries.

**Location:** [`clio/ui/services/config_cache.py:28`](../../clio/ui/services/config_cache.py#L28); [`clio/ui/services/config_cache.py:66`](../../clio/ui/services/config_cache.py#L66); [`clio/ui/services/config_cache.py:78`](../../clio/ui/services/config_cache.py#L78)

**Evidence and limits:** Static inspection: hit returns the cached copy directly; capacity eviction removes next(iter(_cache)). No measured user-visible latency regression is claimed.

**Recommended repair:** Either implement recency ordering under the existing lock or document FIFO deliberately; keep per-key synchronization valid during eviction/invalidation.

**Acceptance criteria:** With capacity 2 and access A,B,A,C, the documented policy evicts the expected key; concurrent eviction and reload preserve one coherent result per key.

**Coordination / effort:** Can be fixed independently; S.

### O08

**P3 — Pipeline observability**. Open; static; narrower than historical OPT-024.

**Problem:** Pipeline processed counts are inferred from return shape: integer 0 counts as one processed item, None as zero, and a multi-day mapping as one. warning_count starts at zero without a common warning aggregation contract.

**Location:** [`clio/pipeline.py:150`](../../clio/pipeline.py#L150); [`clio/pipeline.py:160`](../../clio/pipeline.py#L160); [`clio/pipeline.py:203`](../../clio/pipeline.py#L203); [`clio/tasks/transcribe.py`](../../clio/tasks/transcribe.py); [`clio/tasks/label.py`](../../clio/tasks/label.py)

**Evidence and limits:** Static result adapter inspection. Existing failure-code propagation and historical OPT-024 satisfaction are not disputed; this recommendation concerns the accuracy and units of summary metrics.

**Recommended repair:** Introduce an internal normalized step outcome for counts/skips/warnings while preserving documented public task returns; label units for clip/day/project results.

**Acceptance criteria:** Disabled transcription reports zero actual work, labels report actual file counts, multi-day planning reports day counts, and warning totals reflect emitted structured warnings without changing failure semantics.

**Coordination / effort:** No UI reinterpretation of historical processing matrices; M.

### O09

**P3 — Maintenance documentation**. Open; static.

**Problem:** Operational references retain instructions contradicted by current code: loader-time default injection and Gemini upload deletion. A future maintainer could reintroduce already-fixed behavior.

**Location:** [`docs/superpowers/agents/maintenance-instructions.md:7`](../../docs/superpowers/agents/maintenance-instructions.md#L7); [`docs/superpowers/agents/gotchas.md:42`](../../docs/superpowers/agents/gotchas.md#L42); [`clio/ai/gemini.py:552`](../../clio/ai/gemini.py#L552)

**Evidence and limits:** Maintenance instructions say missing dataclass defaults are injected; current loader/tracker say reads preserve existing config. Gotchas says uploads are not deleted; current provider has cleanup paths.

**Recommended repair:** Reconcile active instructions and mark historical gotchas as historical with verification links. Keep current invariants in one authoritative convention reference.

**Acceptance criteria:** Active docs agree on read-only loading and upload cleanup; each historical warning states whether it still applies. Documentation link checks and git diff --check pass.

**Coordination / effort:** Can be fixed independently; S.

### O10

**P3 — Capacity/performance evidence**. Open; validation gap.

**Problem:** No new scale benchmark was run for many clips, long videos, concurrent projects, retained task history or repeated waveform requests. Per-submission task threads and filesystem scans warrant measured capacity limits.

**Location:** [`clio/task_center/manager.py:126`](../../clio/task_center/manager.py#L126); [`clio/task_center/manager.py:159`](../../clio/task_center/manager.py#L159); [`clio/ui/routes/videos.py:296`](../../clio/ui/routes/videos.py#L296); [`clio/ui/routes/waveform.py`](../../clio/ui/routes/waveform.py)

**Evidence and limits:** Static thread-per-submission implementation plus inventory/cache reads; no resource-exhaustion incident, memory leak or throughput number was established in this review.

**Recommended repair:** Measure representative project sizes and queue bursts, then add bounded admission/workers or scan/index improvements only where the results justify them.

**Acceptance criteria:** Publish workload definitions, cold/warm latency, peak threads/RSS and cancellation response. The chosen limit rejects or queues excess work predictably without blocking UI health/SSE traffic.

**Coordination / effort:** Benchmark before tuning; M.

## Suggested repair sequence

1. Preserve editor and destination content: R03-R06, R10; enforce the artifact boundary in R08.
2. Stabilize cache and active-run behavior: R01/R02/R11, then R15/R16 with legacy compatibility tests.
3. Complete supported workflows: R07/R17, R09, R12-R14.
4. Pair O03 regressions with each fix, then address release/product acceptance O04/O05. Apply O01/O02/O06 incrementally; keep O07-O10 as measured maintenance work.

Each independent fix should use its own small Conventional Commit, update this review's status/evidence, run focused regressions and the relevant full suite, and preserve unrelated working-tree changes. No remote push is authorized by this review request.

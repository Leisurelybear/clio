# Clio — Web UI Frontend

> Series: `10` of `00-overview.md`. Covers `clio/ui/static/` — the no-build-step ES-module SPA
> served by `clio/ui/server.py` (see `09-http-server.md`).

## 1. Responsibilities

- Render the full editor: video sidebar + viewer + waveform, analysis/text/voiceover editors,
  plan editor with timeline + subtitle overlays, run panel, task center, notification inbox,
  and the config editor (global/project tabs, provider registry).
- Keep the browser/desktop two integrations aligned: the desktop shell passes a token and
  folder-picker bridge; the plain browser shows an auth modal and server-side picker.
- Everything stateful lives in vanilla JS — no framework, no bundler. Pure logic modules are
  unit-tested with Vitest (509 tests across 50 files in CI on Node 22).

## 2. Module Map (key files in `static/src/`)

| Module | Responsibility |
|---|---|
| `api.js` | `api(method, url, body)` fetch wrapper — auto `project`/`project_dir` query params, Bearer token, 401 auth modal, JSON error body, SVG icon<defs> library |
| `state.js` | Global mutable `state` (current project, videos, plans, deps, config tab, task filters) |
| `main.js` | Bootstrap (`DOMContentLoaded`), global `window.*` bindings for inline handlers, runtime-warning banner, session restore |
| `layout.js` / `theme.js` / `toast.js` / `utils.js` | Shell, dark/light theme, toast queue, DOM/format helpers |
| `sidebar*.js` | Sidebar: `sidebar-data.js` (project/video/list/plans loads, relink, selection), `sidebar-video-filter.js` (stage chips + search), `sidebar-video-manage.js`, `sidebar-batch-relink.js`, `sidebar-relink.js`, `sidebar-rerun.js` |
| `viewer.js` / `waveform.js` / `video-fade.js` | `setupPlayer`, `loadWaveformForCurrentVideo`, plan-waveform composition, crossfade between sources |
| `editor-*.js` | `editor.js` (tab controller + save), `editor-texts.js`, `editor-voiceover.js`, `editor-refine.js`, `editor-save.js` (dirty-tab guard, save target resolution) |
| `editor-config.js` | **Config editor** (R-017 R-036 R-042): global/project tabs, model/registry provider cards, task bindings, prompt management, logs/tokens panes |
| `editor-plan.js` | Plan render + save + `executeCut` |
| `plan-edit.js` | **Pure functions**: `reorderSequence`/`removeSegment`/`insertSegment`, timeline↔file time mapping (`planSecFromPlayer`/`fileSecFromPlan`), range parsing |
| `plan-timeline.js` / `plan-waveform.js` / `plan-subtitle.js` / `plan-range-picker.js` / `plan-export.js` | Timeline geometry, per-source peak stitching, subtitle scheduling/rendering, range-picker, `exportJianyingDraft` |
| `runner.js` | Run panel: dependency gating (`mediaStepsNeedFfmpeg`), step toggle, preview/force, status handling |
| `task-center.js` | Task list, filters, SSE subscription, `waitForTask` promise bridge |
| `notification-center.js` | **Global SSE inbox** (R-042): unread badge, filters, read actions, deep links into Task Center |
| `subtitle-settings.js` | Subtitle style config (mode auto/manual, position, font) |
| `file-browser.js` / `desktop-pick.js` | Server-side path picker modal / native desktop folder picker |
| `offline-media.js` / `session-restore.js` / `video-selection.js` / `video-url.js` / `video-menu.js` | Offline matching + batch relink, restore last session, selection merge, video URLs, per-video menu badges (stage checks, cut actions) |
| `logs-buffer.js` / `logs-filter.js` / `latest.js` | Log tail buffer, level inference/filtering, race-guard `latest()` for fetches |

## 3. Data Fetching Contract

- **Every call** goes through `api.js`, which auto-appends `project` and `project_dir` —
  the backend keys per-project state off exactly those query params.
- Token: `sessionStorage['api_token']` → `Authorization: Bearer`; on 401 the auth modal shows
  and all in-flight editors stop with `HTTP 401: 需要 API Token`.
- **SSE refresh model**: `notification-center.js` owns the single global stream
  (`/api/notifications/stream`); on `refresh:true` the UI re-fetches notifications **and**
  task snapshots. `startTaskStream`/`subscribeTaskEvents` in `task-center.js` build on this.
- **Latest-wins guards**: `latest.js` exposes a fetch-keyed last-write-wins helper so a slow
  earlier request can never clobber a fresh render (list/status races).

## 4. Plan Editor Flow

```text
renderPlan(plan)                 [editor-plan.js]
  ├─ timeline: buildTimeline(sequence) → segment widths, global↔local mapping  [plan-timeline.js]
  ├─ waveform: recomposePlanWaveformFromCache → composePlanPeaks(timeline, byVideoIndex)  [plan-waveform.js]
  ├─ playhead: player time ↔ plan seconds ↔ file seconds (offset for legacy _seg)  [plan-edit.js]
  ├─ range picker + subtitle: plan-range-picker.js / plan-subtitle.js (scheduleBatchTiming)
  └─ save(): PlayerState → useTimeline; validate_for_save + readiness (see 07) → PUT /api/plans/{name}
```

- Drag-to-reorder and delete are **pure computations** (`reorderSequence`, `removeSegment`)
  that also fix `expanded` state (`nextExpandedAfterMove` etc.) so the DOM stays minimal.
- Selection state: `clampGlobal` clamps a time to the playable timeline; `nextPlayableSegIndex`
  skips segments without media for continuous playback.

## 5. Config Editor (R-017 provider registry)

- Three panes: **Global** (providers + models + `.env` keys), **Project** (task bindings,
  pipeline/whisper/naming per project), and **Prompts** (read/edit builtin prompt templates).
- Provider cards allow add/edit/delete, model tag inputs, capability tags (`gemini` ⇒ video
  tasks only); default providers are undeletable. Local edits are remembered per session in a
  `_collapsedCards` Map before `PUT /api/config/global|project`.
- Env keys are edited through `PUT /api/env` (never leaked into `config.yaml`).

## 6. Run Panel

`runner.js` builds the step list from the registered tasks; each step is enabled by capability
(gemini for video), dependency state (`missingMediaDepsForSteps` warns about missing ffmpeg
before any encode), and skip flags (`--force` equivalent). Starting a run submits a
`PIPELINE`/`RERUN` task; status events drive step progress via `task-center.js`.

## 7. Cross-Module Dependencies

- → `clio/ui/server.py` + routes (all data over the REST/SSE API, `api.js` URL conventions)
- → `clio/task_center` (SSE seq/revision, task snapshot, notification inbox)
- → `clio/ui/static/` assets served by `server.py` `handle_static`
- Reverse: routes depend on the same `state` keys the frontend sends (`project`, `project_dir`)
- Plan save correctness depends on shared `parseUseTimeline` semantics with backend
  `parse_time_range` (see `07-analysis-plan-domain.md`)

## 8. Risks / Maintenance Notes

1. `editor-config.js` (2048 lines) and `viewer.js` (877) are the two largest modules; the
   config editor mixes rendering, per-session collapse state, AND provider CRUD API calls —
   split candidates if complexity keeps growing.
2. Global `window.*` bindings exist because HTML inline `onclick` handlers call them
   (`switchToOriginalThenCompress`, `initProjectConfig`…); renaming a module export requires
   updating the inline handlers in `index.html`.
3. `api.js` appends project params to **absolute** URLs too — any third-party URL fetch must
   bypass `api()` or it inherits `project=` params.
4. State is one mutable global object; cross-module reads are implicit. A future upgrade to a
   subscription store (or TS) must preserve `state.js`’ exact key names or all importers break.
5. Visual correctness (waveform bins, subtitle timing, crossfade) is time-sensitive; the pure
   helpers are Vitest-covered, but canvas painting (`drawWaveform`, `captureFrame`) is exercise
   only in-browser — keep them free of side effects to preserve testability boundaries.
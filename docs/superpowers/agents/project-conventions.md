# Project Conventions

Detailed implementation contracts moved out of the top-level `AGENTS.md` during O17.
Use the index in `AGENTS.md` to jump directly to the relevant section.

## 4. Key Conventions

### 4.0 Docs naming

- All files under `docs/` follow `docs/CONVENTIONS.md` (date + kebab-case; specs `-design`, plans `-plan`).

### 4.1 Commit

- English message, Conventional Commits: `type(scope): subject`
- Each commit as small as possible: one independent feature/fix per commit
- Types: `feat` / `fix` / `refactor` / `docs` / `chore`
- History rewriting: use `git rebase -i --root`; on Windows use byte-level Python filter (see gotchas.md)

### 4.2 Workflow

- Plan first, then implement: record in `ROADMAP.md`, confirm approach, then code
- Document new modules: README.md for users, AGENTS.md for AI (purpose, entry, conventions)

### 4.3 Code Style

- No comments unless explaining why
- Chinese for user-facing copy (CLI prompts, error messages)
- Default `skip_existing=True` shared by all steps (controlled by `analyze` toggle)
- AI-returned JSON uses `extract_json()`: first `json.loads`, then regex `{}`

### 4.4 Configuration

- Repo commits `config.example.yaml` / `.env.example`; real files gitignored
- No local paths, proxy IPs, API keys in examples
- After config changes, update both example and READMEs

### 4.5 Prompts

- All in `clio/prompts.py` as constants
- Trip context injected via `_wrap_with_context()` before all prompts
- Output format: JSON (for `extract_json()` parsing)
- Fixed-output tasks use `StructuredOutput` from `clio/ai/structured.py` so providers can enforce the schema natively; refinement tasks use JSON object mode and keep `extract_json()` as a fallback.

### 4.6 Refine Special Modes

`refine_text` falls back to `video_analyze` by default. To use a cheaper pure-text model:

```yaml
ai:
  tasks:
    refine_text:
      provider: deepseek
      model: deepseek-chat
```

For known errors (`--fix`), use a single JSON file and the targeted fix prompt. The first `_changelog` entry must always say `Modified XXX per user feedback`.

### 4.7 Model Registry (R-017)

Provider management is a frontend-only experience - no new backend APIs needed:

- `ProviderConfig.models: list[str]` stores model names per provider
- API keys live in `.env` via `PUT /api/env`, never in `config.yaml`
- `editor-config.js` renders:
  - Config sections as collapsible cards with section labels (Global/Project tabs)
  - Provider list (Global tab): add/edit/delete, tag input for model names
  - Default task binding (Global tab): `ai.default_tasks`, inherited by every project
  - Task binding (Project tab): per-task overrides of global defaults; dropdowns filtered by capability (`gemini` -> video tasks)
  - Collapse state remembered per session (`_collapsedCards` Map)
- Global default bindings write to `config.yaml` via `PUT /api/config/global`; project overrides write to `project.yaml` via `PUT /api/config/project`
- Runtime merges `ai.default_tasks` with project `ai.tasks` (project wins per task); `refine_text` follows the effective `video_analyze` binding unless explicitly overridden
- `/api/deps/keys` reports `unbound_tasks` separately from `missing` provider keys so new projects do not falsely ask for an API key
- Default providers (`gemini`, `openai`, `deepseek`) cannot be deleted
- Video tasks (`video_analyze`) only show gemini-type providers in dropdown

### 4.8 Notification Center (R-042)

- Notifications live in the Task Center SQLite store but do not replace task state.
- Task terminal statuses and every warning/error task event are registered transactionally.
- Frontend status, toast, and runtime-warning messages register through `POST /api/notifications`.
- Unread notifications are never removed by retention cleanup; only read history is bounded.
- `notification-center.js` owns the global SSE connection, unread badge, inbox filters,
  read actions, and Task Center deep links.
- `ui.desktop_notifications` is a global opt-in, default off. Native delivery happens only when
  the page is hidden; the inbox write remains authoritative and native failure is silent.
- `desktop-notify.js` owns eligibility and dedupe. `clio/desktop/notifications.py` uses
  PowerShell Toast, `osascript`, or `notify-send`; never build a shell string from notification text.

### 4.9 Historical Logs (R-027e)

- `clio/log_history.py` is the only supported reader for hourly disk logs in the UI.
- Accepted names are direct children matching `YYYY-MM-DD-HH.log`; never accept arbitrary paths.
- Reads are tail-only: 2 MB default, 5 MB hard cap, partial first line discarded.
- Historical mode pauses live polling and disables clear; returning to `当前会话` resumes from the
  retained session offset. Keep the live and historical buffers separate.
- Endpoint changes must cover traversal, symlink escape, unreadable/missing files, and truncation.

### 4.10 FFmpeg Installer (R-028b/c)

- `clio/ffmpeg_install.py` is the only supported automatic installer. Setup scripts and the Web UI
  call it; do not add a second download path.
- Assets are fixed to the BtbN `n9.0` release names for Windows x64/arm64 and Linux x86_64/aarch64.
  macOS intentionally returns manual guidance because no reliable checked static build is used.
- The checksum is read from the same release. Reject non-HTTPS redirects, oversized/empty
  downloads, corrupt archives, path traversal, links, devices, duplicates, and failed `-version`.
- Install to `%LOCALAPPDATA%/clio/ffmpeg` on Windows and `~/.local/share/clio/ffmpeg` elsewhere;
  `CLIO_FFMPEG_DIR` overrides the root. Never rewrite `paths.ffmpeg` / `paths.ffprobe`.
- `POST /api/deps/ffmpeg/install` creates one cancellable `ffmpeg_install` Task Center job;
  `GET /api/deps/ffmpeg/install/status` reports capability, platform, task id, and progress.
- `ffmpeg-install.js` owns the dialog and manual fallback. Keep extraction, checksum, and archive
  safety tests with any installer change; UI progress/cancel tests live in Vitest.

### 4.11 Plan Preview Media (R-031b)

- Preview resolution is cut first, source second. Never add day concatenation without a versioned
  artifact contract and invalidation rules.
- `clio/plan_preview.py` maps sequence positions to `{index}_{title}_seg_{i:03d}.mp4` under the
  standard `paths.output_dir/cuts/<day>` only. Custom `cut-outdir` is unsupported.
- `/api/video?source=cut` serves only a direct child of that directory. Reject traversal, nested
  paths, symlinked roots/components, and files outside the project output.
- Accept clips without `_cut_info`; when metadata exists, require matching segment index, timeline,
  start/end (within 0.05s), and valid JSON.
- `plan-preview-media.js` owns cut-vs-source resolution and timing. `_previewMedia` in state records
  the active mode; cut seeks are clip-local, source seeks retain plan start plus legacy offsets.
- Preload identity includes source/project/file/seek time. A failed cut is blocked once and retried
  at the same composite time from the source video.

### 4.12 Internationalization (R-025)

- `ui.language` (`zh-CN` | `en`, default `zh-CN`) is the single source of truth for both backend and
  frontend copy. Precedence in `clio/i18n.py::_resolve_locale`: explicit `locale=` argument >
  `CLIO_LANG` env var > active locale from `ui.language` > `zh-CN`.
- Switching in Settings applies immediately: `setLocale()` dispatches `clio:locale-changed` and
  modules that build DOM from JS strings re-render on that event. `applyTranslations()` re-runs for
  static `data-i18n` markup.
- Two catalogs, both must keep identical key sets per locale:
  - `clio/locales/{zh-CN,en}.json` - backend/CLI copy (`t()` from `clio/i18n.py`).
  - `clio/ui/static/locales/{zh-CN,en}.json` - Web UI copy (`t()` from `ui/static/src/i18n.js`).
  Lookup falls back `locale -> en -> zh-CN -> key`, so partial catalogs degrade to English, never to
  a raw key.
- `t()` interpolates `{name}` placeholders only. Never add ICU/pluralization; keep the same
  placeholder names in every locale.
- `zh-CN` values must stay byte-identical to the literal they replaced; tests assert that copy.
- Never translate: `clio/prompts.py` (AI prompts), `clio/config/descriptions.py` (zh-CN source of
  truth mirrored to `settings.desc.*`), docstrings, comments, internal log identifiers, and content
  already persisted to disk.
- `clio/tasks/migrate.py::is_already_new_structure()` is the only supported way to detect the
  "already migrated" error; it checks every supported locale. Never match a localized string.
- Milestone 10 guards prevent regressions: `clio/tests/test_i18n_inventory.py` (AST scan; comments
  and docstrings never count) and `clio/ui/static/src/__tests__/i18n-inventory.test.js` (strips JS
  comments first). Both compare against an explicit allow-list; add an entry with a reason rather
  than deleting the guard.
- `clio/pipeline.py` and `clio/ui/services/run_preview.py` share the `run.step.<name>.label` keys.
  Do not reintroduce a private step-label dict.
- The header language button (`#btn-lang` in `index.html`, `ui/static/src/language-switcher.js`)
  sits next to the theme toggle and is the quick entry point for `ui.language`. It cycles the
  shipped locales, applies the switch immediately, and persists through `PUT /api/config/global`.
  It never sends unsaved settings edits: while the config form is dirty it reads the on-disk
  global config instead, and a failed write only warns (the live switch stays).
- `ensureGlobalUiDefaults()` (same module) is the only supported way to materialize the `ui` block.
  `renderConfig()` skips sections that are absent from the data, and configs written before
  `ui.language` existed have no `ui:` section, so the settings card disappeared. Call it before
  rendering the global tab; never render that tab from a raw config object.

### 4.13 HTTP Access Logs

- `clio/http_access_log.py` is the only supported access-log policy and formatter. It owns noise
  classification, query redaction, body capture, and the `[http]` line shape.
- `server.access_log_mode` (`off | errors | api | all`, default `api`) lives in global
  `config.yaml`. Static files, polling reads (`/api/notifications`, `/api/project`,
  `/api/plan/readiness`, `/api/tasks`), binary media, and stream connects are suppressed unless
  slow, errored, or explicitly included by `all`.
- `log_request()` prints one line per kept request with request id, method, sanitized path, status,
  latency, and response size. Handler-set response bytes are authoritative; do not restore the
  base handler's size argument.
- Body capture is opt-in via `server.access_log_body_bytes` (0-4096). `/api/env`, `/api/config*`,
  `/api/providers*`, and `/api/logs*` are always denied. Bodies are JSON-redacted, escaped to one
  line, and byte-truncated with a marker.
- Unhandled handler exceptions print `[http-error]` plus traceback and return 500 only when no
  response has started. Client disconnects stay silent.
- `server.log_retention_days` (default 14, 0 = forever) prunes direct-child
  `YYYY-MM-DD-HH.log` files; never delete unrelated or symlinked entries.

### 4.14 Network Locations in the File Picker

- `clio/ui/services/network_service.py` is the only supported SMB server/share enumerator. It uses
  `WNetOpenEnumW`/`WNetEnumResourceW` plus Explorer registry hints (`HKCU\Network`,
  `MountPoints2`, `Map Network Drive MRU`) because Windows network discovery is frequently disabled.
- `GET /api/fs/network` lists servers; `?server=\\server` lists that server's disk shares. Only
  `\\server` is accepted as the `server` query value; administrative shares ending in `$` are filtered.
- Never pass a server-only UNC to `Path`/`os.scandir` (pathlib normalizes it into a local-looking path).
  Browse a share (`\\server\share`) or use the shares endpoint first.
- The picker keeps a manual `\\server` entry because automatic discovery may return an empty list.
  `_browse_parent()` returns `""` for Windows drive and UNC roots so the UI returns to the drive list.
- `file-browser.js` owns the `fs` / `servers` / `shares` views, select-all/deselect-all, and the
  multi-select double-click confirm. Keep backend and Vitest coverage when changing this contract.

### 4.15 Sidebar Video Bulk Selection

- Selection mode (`#btn-select-videos`) owns `state.selectedFiles`; the per-row checkbox and the
  rubber-band drag both write that same array, and any `renderVideoList()` rebuilds the checkboxes.
- `video-selection.js` owns pure list mutations. `removeSelectedVideos()` removes many stored paths
  in one pass: absolute-path equality first, a unique basename as fallback, with `missing` and
  `ambiguous` reported separately.
- `sidebar-data.js::removeSelectedVideosFromProject()` is the only bulk-remove entry point. It asks
  for one confirmation, sends one `PUT /api/videos/selected`, reloads the list, and prunes
  `state.selectedFiles` to videos that still exist. The header action is disabled at zero selected.
- `sidebar-video-filter.js::filterVideos()` is the single source of the visible row set. The
  selection header's select-all/deselect-all must operate on that filtered set only (never on
  `state.videos` directly), via `video-selection.js::toggleSelectionFor()`; selections made under a
  different filter are preserved, and offline rows are never selectable.
- `video-drag-select.js` owns the rubber-band rect math and pointer binding. It is active only in
  selection mode, starts outside interactive controls, keeps the previous selection with
  Ctrl/Cmd, cancels on Escape, and auto-scrolls near the list edges. `main.js` wires
  `enabled`/`selected`/`commit` so the module stays independent of sidebar state.
- Keep `video-drag-select.test.js` and `video-selection.test.js` green when changing selection copy
  or matching rules.

### 4.16 Sidebar Video Metadata Card (R-049)

- `GET /api/video/info` is the only metadata endpoint for the sidebar. It resolves compressed rows
  under `output/compressed` via `validate_within_root` and original rows via the `videos.json`
  allowlist; offline rows return `exists: false` instead of 404 so relink stays reachable.
- `clio/utils.py::probe_media_details()` owns ffprobe parsing (resolution, fps, bitrate, codec,
  container) and caches by `(ffprobe, resolved path, size, mtime)`. Callers must degrade to
  stat/.vmeta fields when ffprobe is missing or fails (`probe_error`), never raise to the UI.
- `.vmeta` supplies the compression comparison (`source_size`/`target_size`, ratio, and
  `compress_settings`). In original view use `source_duration_sec`; in compressed view use
  `target_duration_sec`. Never invent a ratio when either size is zero.
- `video-info.js` owns the 400ms hover tooltip and the pinned `视频信息` modal; `sidebar-data.js`
  binds hover per row and clears the client cache on every `loadVideos()`. Keep `buildInfoSections()`
  pure so formatters and field grouping stay unit-testable.
- The row menu always exposes `info` first (enabled for offline rows too); it must stay outside the
  ffmpeg dependency gate.

### 4.17 Artifact Overwrite Protection

- `clio/artifact_guard.py` owns the `_user_edited` / `_user_edited_at` markers and the
  confirmation gate for regenerated plan and voiceover artifacts.
- `PUT /api/plan` and `PUT /api/voiceover` mark saved artifacts as user-edited. New AI output
  clears the markers but preserves `_lineage`.
- Regeneration with a matching `_lineage` skips normally. A changed lineage, `--force`, or
  unreadable/non-object JSON on a user-edited artifact raises
  `ArtifactOverwriteConfirmationRequired`; confirmed overwrites back up the JSON/Markdown first.
- `POST /api/run/preview` reports `artifact_overwrite.items`; `POST /api/run/start` returns 409
  `artifact_overwrite_confirmation_required` until `confirm_artifact_overwrite=true` is sent.
- `clio/ui/static/src/runner.js` owns the second confirmation dialog and must refresh the preview
  for both `plan` and `voiceover` steps.

### 4.18 Safe HTML Rendering

- `clio/ui/static/src/utils.js::html` is the default tagged template for dynamic markup.
  Every interpolation is escaped; nested `html` fragments and arrays are composed safely.
- Use `unsafeHtml()` only for trusted, code-owned markup such as local icons. Never pass
  user, API, filename, log, or AI-generated content through it.
- Keep `escapeHtml()` for values interpolated into legacy non-tagged template strings.
  Prefer `html` for new or touched `innerHTML` / `insertAdjacentHTML` code.
- Security tests must cover values inside both text and quoted attributes.

### 4.19 Bounded Plan Input (R-048 P0c)

- `clio/plan_input.py::build_plan_clips()` is the only supported projection from detailed analysis
  clips into the daily-plan prompt. Do not selectively copy timeline fields in `tasks/plan.py`.
- The projection keeps representative timeline rows, `highlights`, `key_moments`, media identity,
  duration, capture time, GPS, and summary fields. It always removes top-level and per-row
  transcript fields; transcripts enter planning only through the existing transcript context.
- `plan.max_timeline_rows_per_clip` and `plan.max_key_moments_per_clip` default to 8 and 6.
  `0` disables the corresponding cap. Timeline output stays time-sorted, ends with an endpoint when
  possible, and prefers key-moment overlap, score, then usable quality.
- Prompt serialization and plan lineage use the projection. Range validation, transcript selection,
  source provenance, and stored state use the full clips passed as `full_clips`.

### 4.20 Generated Plan Cut Snapping (Q13)

- `clio/cut_snap.py::snap_plan_ranges()` is the only supported cut-point snapping path. It runs on
  AI-generated plan dictionaries after local validation and before `Plan.from_dict()` and artifact save.
- Analysis timeline `start`/`end` values are local shot boundaries. Transcript segment `end` values
  become sentence boundaries after subtracting `segment_offset_sec` for legacy split clips.
- Starts prefer the nearest shot boundary; ends prefer the nearest sentence end. Either endpoint may
  use the other boundary type, but movement is capped at 1.5 seconds.
- Never apply snapping when it would make a range shorter than 0.5 seconds. Leave invalid or missing
  timelines unchanged. Do not apply this pass to user-edited plans loaded from disk.
- When a plan changes, recalculate `total_estimated_sec` so preview, cut, export, and the saved plan
  share one range contract. Keep `clio/tests/test_cut_snap.py` and planner integration coverage green.

### 4.21 Same-Day Entity Consistency (Q7)

- `clio/tasks/consistency.py` is the only supported generator and reader for
  `plans/<day>_entity_consistency.json/.md`. Reuse the `refine_text` task binding; do not add a
  separate provider task.
- The structured output canonicalizes entity names and aliases, carries clip references, and
  records duplicate scenes with bounded timestamp ranges. Validate clip indexes against the same
  day's analyzed material, merge aliases case-insensitively, and reject malformed or out-of-range
  findings before writing.
- Lineage is deterministic over the plan-clip projection, prompt/context fingerprint, schema
  version, and report contents. Planning ignores stale or malformed reports and must include the
  current report in the plan lineage so a changed report regenerates the cached plan.
- Pipeline order is `analyze -> consistency -> voiceover -> plan -> plan_voiceover -> label`.
  `run_preview.py` and `runner.js` own the dedicated day input and step presentation.
- Consistency reports are derived artifacts, not user-edited plan/voiceover artifacts. They do not
  require the artifact overwrite confirmation flow.

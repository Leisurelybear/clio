# Changelog

## 2026-08-09

Review remediation batch for `E:\Downloads\clio-review-20260808.csv` (P0 3 + P1 15
items, all verified with tests; full regression 1432 pytest + 435 Vitest).

### Security & data safety
- fix(config): plain-safe YAML upgrade and atomic replace — break `!python/object`
  tags from user-written `config.yaml`, write via unique temp file + `os.replace`
- fix(cut): constrain plan dir and clip names with `safe_basename`; new
  `clio/utils.py:safe_basename` traversal-free name helper
- fix(identity): reject a vindex that does not actually list the clip, so stale
  sidecars can never re-associate a clip with the wrong video

### Cache correctness (fingerprint + invalidation)
- fix(compress): fingerprint source and settings for cache reuse (P1-02/P1-04) —
  reuse only when the recorded source path/settings still match; a source or
  compression-setting change now re-compresses instead of silently reusing
- fix(compress): prune stale same-source candidates after commit — `setdefault`
  used to keep whichever file the OS iterated first per key, so a stale output
  could win the slot and force a re-encode every run (unbounded dir growth); now
  all same-key candidates are searched and stale siblings (file + `.vmeta`) are
  removed once a fresh output is committed
- fix(analyze): invalidate the skip cache when the analysis lineage (prompt/model)
  changes; fix(plan)/scripts: invalidate plan and voiceover caches the same way
- fix(analyze): coerce AI-returned fields with strict validators (index/title),
  and fix(identity): legacy/vindex mismatch no longer mislabels videos

### Verify / reindex / export hardening
- fix(verify): report missing and undeclared segments instead of silently
  passing; fix(readiness): treat an empty discovered index as missing media
- fix(reindex): trust only fresh split metadata, drop average-offset guessing
- fix(export): refuse to write an empty draft when no video materials resolve

### CLI & concurrency
- fix(cli): propagate `transcribe` and `migrate` exit codes so failures fail the
  caller; fix(whisper): reload module binding after in-process pip install
- fix(waveform): bounded semaphore instead of busy-wait job counter (P0-02);
  fix(run): clear the cancel event before worker spawn so a fresh run starts
  clean (P0-03) — cancel/failover behavior documented with tests
- fix(subtitle): serialize settings saves latest-write-wins, so a burst of quick
  edits cannot write stale project config last

### Tests / Docs
- New regression tests: compress stale-sibling pruning + fresh-over-stale
  lookup, vindex mismatch, verify missing segments, reindex fresh-metadata-only,
  ready empty-index, export empty draft, run cancel reset, subtitle settings
  ordering. Backend 1432 passed / 1 skipped; Vitest 435 passed (39 files).
- Updated `E:\Downloads\clio-review-20260808.csv` (状态 column: 18 items 已完成)

## 2026-08-08

### Added
- feat(ui): **sidebar optimization** — the top task area is fixed/filter-scroll (project
  actions moved into a `项目 ▾` dropdown in the panel header, the 5 entity nav icons become
  one sticky icon row), and the sidebar can collapse via a `‹` header button (`Ctrl+B`).
  The editor panel got a matching `»` collapse button (`Ctrl+\`); both collapsed handles
  widen and show a top-aligned expand arrow so fold/unfold live at the same height.
- Video list **search + status chips**: search narrows by index / filename / title; chips
  (`缺分析 / 缺口播 / 缺转录 / 离线`, plus `未压缩` in the original view) filter to videos
  missing that stage; the heading shows `(可见数/总数)`.
- Per-video rows now show **4 stage dots** (压缩/分析/口播/转录) instead of text badges.
- New pure module `clio/ui/static/src/sidebar-video-filter.js` — stage-status / search
  predicates (unit-testable) consumed by `sidebar-data.js`.
- Bottom **stage count bar** replaces the old directory-scan `流水线` `step-list`: 4 cells of
  `完成数/总数` computed from each video's own artifact fields; clicking a cell lists the
  videos that already completed that stage (the `全完成` cells are no longer a dead end).

### Fixed
- fix(ui): count-bar cells now filter to **completed** items, so the visible `done/total`
  number matches the rows you get (previously a click filtered the *missing* set and an
  all-done cell hit a misleading "没有匹配的视频" empty state)
- fix(ui): the stage filter is reset when switching `压缩 / 原视频` (a stale `未压缩` filter
  from the original view no longer leaves the compressed view empty)
- fix(ui): the project menu is renamed to `项目 ▾` so the trigger no longer repeats a menu
  item, and the menu self-closes after picking an action

### Refactor
- `renderSteps()` removed (its `state.steps` reader and 4 call sites dropped); list
  freshness now goes through `renderVideoList()`

### Tests
- New `sidebar-video-filter.test.js` (Vitest, 11 cases) for stage/status/chip/count/search
  helpers; full suite 433 cases green, backend 1409 passed

### Docs
- Design: `docs/superpowers/specs/2026-08-08-sidebar-optimization-design.md`
- Plan: `docs/superpowers/plans/2026-08-08-sidebar-optimization-plan.md`
- `clio/ui/README.md` gained a 侧边栏 section describing search/chips/count bar/collapse

## 2026-08-06

### Added
- feat(ui): plan-mode **floating subtitles** — during composite plan preview, the current
  segment's spoken `voiceover` text is overlaid on the player and rotates at even intervals
  across the segment's `use_timeline` duration
- New `clio/ui/static/src/plan-subtitle.js`: pure helpers `splitSubtitleLines`
  (Chinese/ASCII punctuation + long-line breaking), `scheduleSubtitleTiming` (even
  distribution, last-line clamp), `subtitleIndexAtTime` (half-open range lookup), plus a
  per-index cached loader `loadVoiceoverText` and the DOM renderer
  `renderPlanSubtitle`/`hidePlanSubtitle`
- `viewer.js` drives subtitles from the existing plan preview paths
  (`seekToGlobal`, `player.ontimeupdate`, `stopPreview`, `renderPreviewBar`)

### Tests
- `plan-subtitle.test.js` (Vitest, 26 cases): line splitting, timing, line-at-time lookup,
  cached loader, DOM renderer show/hide/line-change behavior

### Docs
- Design: `docs/superpowers/specs/2026-08-05-plan-subtitles-design.md`
- Plan: `docs/superpowers/plans/2026-08-05-plan-subtitles.md`

## 2026-08-05

### Fixed
- fix(progress): `ProgressTracker.update()` only resets `current` when the phase actually
  changes, so same-phase message-only updates (e.g. transcribe's per-percent callbacks)
  no longer wipe the per-clip counter — the 转录 progress bar/percentage now advances
- fix(ui): the 60s stale-progress warning no longer claims "正在后台下载模型 / Whisper 模型管理"
  for non-transcribe phases; only `transcribe` shows the Whisper model hint, other phases
  show a generic "可能仍在运行或网络连接异常" message
- fix(desktop): enable `text_select=True` on the pywebview window — pywebview's default is
  False, which made all desktop-app text unselectable/un-copyable
- fix(ci): use `getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)` so the core mypy gate
  passes on POSIX (Ubuntu CI) where the constant is absent

### Tests
- `test_progress`: same-phase message update preserves `current`; phase re-entry still resets
- `test_desktop_app`: window is created with `text_select=True`
- `test_runner` (Vitest): `staleWarningHtml` shows Whisper hint only for `transcribe`

### Docs
- CHANGELOG: record the two run-progress fixes, desktop text selection, and the CI mypy gate fix

## 2026-08-03

### Fixed
- fix(desktop): remove the early `config_path.exists()` exit so **all** CLI subcommands
  (desktop / serve / doctor / …) bootstrap a missing `config.yaml` via the loader's
  auto-generation — CLI entry now matches `python -m clio.desktop` behavior
- fix(desktop): treat a broken or incomplete `clio.lock` as stale — `read_lock()` requires
  a non-bool int `port`, else returns `None`; `app.py` reads `lock.get("port")` defensively
  so a second launch after a crashed first instance no longer `KeyError`/`InvalidURL`
- fix(ui): `/api/deps/keys` falls back to globally declared providers when no project/tasks
  exist, so the B-1 missing-key banner still guides first-run setup
- ci(release): hard-check `README-desktop.md` is staged in macOS zip (parity with Windows)
- fix(config): normalize a missing `config.yaml` behind a non-directory path to
  `FileNotFoundError` on POSIX (macOS/Linux), matching Windows behavior in `load_global_config`

### Tests
- `test_desktop_single_instance`: missing-port / non-int-port locks → `None`
- `test_main`: `desktop` subcommand dispatches even when `config.yaml` is absent
- `test_routes_deps`: no-tasks → falls back to global providers

### Docs
- ROADMAP R-040: record the post-review regression fixes above

## 2026-08-01

### Added
- feat(config): **auto-generate** missing `config.yaml` from bundlable `config.example.yaml`
  template (loader `_ensure_global_config`; repo-root + `_internal/clio/config/` lookup)
- feat(desktop): **pywebview host** — start localhost UI server on a free port, open a native
  window with `js_api` pickers; `desktop` subcommand entry (`python main.py desktop`)
- feat(desktop): **single-instance lock + focus probe + web coexistence prompt** (R-039) —
  second launch focuses the existing window; prompts when web UI is up on 8765
- feat(desktop): **hourly file logging** in the desktop shell (`logs/`)
- feat(ui): runtime **banners** guiding API-key and ffmpeg setup (R-040 B-1/B-2)
- feat(desktop): clear dialog when WebView2 Runtime missing (R-040 B-3)
- feat(packaging): ship `README-desktop.md` next to the exe (R-040 F-3)
- feat(whisper): guard `pip install` in frozen desktop builds (R-040 F-1)
- feat(config): allow out-of-list task models with a warning instead of fatal (R-040 F-2)
- feat(packaging): **multi-platform desktop builds** — Windows x64 + macOS universal2
  (PyInstaller onedir) with a GitHub Release publishing workflow
- feat(desktop): native dialogs (browse folders / relink / multi-file-add videos)
- feat(plan): write `source_inputs` input-pool on generated plans; list them in `plan.md`

### Fixed
- fix(desktop): pass auto-created `config.yaml` path to the server on first launch (5e863c4)
- fix(desktop): `finally` owns the server shutdown instead of the window `closed` event
- fix(desktop): refine window error dialog text + ffmpeg winget ID + key banner wording
- feat(ui): native pick helpers route browse buttons to the OS folder dialog
- carry-over 07-31 desktop items: cancel run + stop server on window close

### Changed
- pack: bundle `config.example.yaml` into the onedir (`clio/config/`)

### Docs
- specs/plans/designs for auto-generated config, desktop single-instance, hybrid desktop
- ROADMAP R-039 / R-040 closed; multi-platform packaging recorded

## 2026-07-31

### Added
- `desktop/app.py` pywebview host with `js_api` pickers; start/stop localhost server on free port
- desktop `cli` subcommand (`clio desktop`); persist last picked directory
- mockable native dialog wrappers; route browse buttons to native folder dialog
- native dialog on WebView2 missing (B-3) / cancel + stop server on window close
- native multi-file add videos; relink + batch relink dialogs; remove custom dir browser modal
- native browse on config path fields

### Docs
- R-032 desktop implementation plan + hybrid design (HTTP + native dialogs only)

## 2026-07-29

### Docs
- R-032 hybrid desktop design locked (js_api-only pickers, desktop token clarified)

## 2026-07-27

### Added
- plan segment **range picker** modal; wire the 选区 button to it; harden interplay
- pure range-picker timebase helpers + tests

### Changed
- remove the dead physical-split writer and its config knobs (R-038)

## 2026-07-26

### Fixed
- fix(analyze): harden long-video window handling

## 2026-07-25

### Fixed
- fix(ui): stable player viewport on source switch + linear panel resizing
- fix(ui): preserve progress + nav state / ignore stale loads when switching video source
- fix(ui): convert player time → transcript time
- fix(analyze): merge `summary.csv` on selection re-analyze
- fix(gemini): apply timeout + token limits
- fix(config): reject invalid runtime settings
- fix(media): normalize configurable index widths
- fix(cut): reject non-string `day_label` with 400
- fix(label): unlink partial labeled mp4 on cancel
- fix(plan): let scrub only seek; resync select-videos label

### Tests
- UI: cover preview-bar scrub-only, select-videos label, run button
- plan: cover `source_inputs` extras round-trip + skip legacy

### Docs
- full project review results; R-033 hardening marked done

## 2026-07-24

### Added
- `source_inputs` design + implementation plan

### Fixed
- fix(analyze): re-raise cancel during window slice copy
- fix(r033): strip masked `api_key` / plan `files=` skip / compress partial cleanup

## 2026-07-23

### Added
- R-033 **hardening + pipeline robustness** design/plan: config write masking, input
  sandboxing, body/token guards, run isolation, partial cleanup

### Fixed
- fix(config): validate global config on load and `PUT`; strip `api_key` on write + mask on GET
- fix(ui): cap JSON body size, constant-time token compare
- fix(ui): sandbox cut `output_dir` + run `project_dir`; sanitize day labels
- fix(log): skip blank session_log rows in print-split writes
- fix(pipeline): honor files filter, state keys, counts, ETA reset
- fix(media): cleanup partials on cancel; strict waveform abspath
- fix(ui): keep plan auto-advance after native play/seek/scrub

### Tests
- cover `_matches_selected_*` identity-only JSON

### Docs
- R-033 hardening + robustness design/plan; marked done

## 2026-07-22

### Added
- store timestamped log entries; normalize + time format + `sinceMs` filter
- show per-entry timestamps and recent-time chips in the UI (R-027d)

### Docs
- R-027d session log timestamps design + implementation plan

## 2026-07-21

### Added
- feat(ui): session logs **keyword filter** + **level chips** (R-027) — 全部/信息/警告/错误; client buffer re-filter; match count
- feat(ui): per-line **level badges** with color (heuristic from tags / ✗ / 跳过 / Traceback / …)
- Pure helpers: `clio/ui/static/src/logs-filter.js` + Vitest `logs-filter.test.js`

### Fixed
- fix(ui): debug-level session log badge label was missing (showed 信息)

### Docs
- ROADMAP R-027a/b marked done; open **R-027d** (timestamps + time filter) and **R-027e** (load `logs/*.log` history)
- `clio/ui/README.md`: session logs filter / levels + scheduled follow-ups

## 2026-07-20

### Fixed
- fix(plan): normalize plan/media **index keys** (`"1"` ≡ `"001"`) so export/cut readiness no longer false-blocks when texts store int indices
- fix(plan): **collect offline originals** from `videos.json` via `media_identity` / stem maps → `video_offline` warnings
- fix(ai): **clear provider cache** after `.env` / config / provider writes so rotated API keys take effect immediately (not after TTL)
- fix(ai): include `timeout_sec` / `max_tokens` / `retry_attempts` / `requests_per_minute` in provider cache key
- fix(ui): keep **run SSE** alive when switching to Plan; process done/cancel without Run pane DOM
- fix(ui): **exact-match** player source `file=` query (no basename substring false positives)
- fix(ui): escape plan **day_label** in day select; `String(index)` match on segment click

### Changed
- chore: stop tracking `.coverage`; ignore `.coverage` / `coverage.xml` / `htmlcov/` / `.mypy_cache/`

### Docs
- Full project review: `docs/analysis/2026-07-20-full-project-review.md`
- ROADMAP: open R-032 (desktop packaging), R-033 (post-review hardening); record 2026-07-20 P0 fix wave

## 2026-07-19

### Added
- feat(ui): plan **global preview timeline** (R-031a) — bar scrub maps to Σ `use_timeline` seconds; composite clock `成片 mm:ss / 总`; pure `plan-timeline.js`
- feat(ui): plan **progress fill + cap playhead** and **composite peaks waveform** (R-031a2) — client stitch of per-source `/api/waveform` peaks onto the plan axis; pure `plan-waveform.js`
- Unit tests: `plan-timeline.test.js`, `plan-waveform.test.js`

### Changed
- Plan preview no longer forces “click segment block to hop only”; drag is the primary seek
- Plan waveform scrub uses `seekToGlobal` (same timebase as the progress bar)
- Leaving Plan entity restores single-source waveform load

### Fixed
- fix(ui): do not reload single-file waveform on every plan segment hop (kept composite peaks)
- fix(ui): soft-update fill/playhead during play/scrub (avoid full bar `innerHTML` thrash)
- fix(ui): recompose plan waveform when `use_timeline` / order changes without re-entering Plan
- fix(ui): resolve plan `videoIndex` with `String()` equality; continuous play skips missing indexes

### Docs
- design/plan: `docs/superpowers/specs|plans/2026-07-19-plan-global-preview-timeline*`
- design/plan: `docs/superpowers/specs|plans/2026-07-19-plan-preview-chrome-waveform*`
- ROADMAP R-031a / R-031a2 done; R-031b cut/concat media open
- `clio/ui/README.md`, `docs/cli-reference.md` serve blurb

## 2026-07-18

### Added
- feat(analyze): logical analyze windows for long clips (`window_max_min` / `window_overlap_sec`); temp slices under `output/.analyze_windows/<stem>/`, merge to one absolute-timeline texts JSON
- feat(identity): `is_legacy_split_*` / `legacy_segment_offset_sec` gate for read-only legacy `_seg*` projects
- feat(deps): `probe_ffmpeg_deps` + `GET /api/deps/ffmpeg` — report ffmpeg/ffprobe availability without starting jobs
- feat(ui): runtime-warnings banner when ffmpeg/ffprobe missing (`ffmpeg-missing`, warning level)
- feat(ui): soft-disable ⋮ menu compress/transcribe (and label if present) when deps missing
- feat(ui): runner blocks start when selected steps include compress/label/transcribe and deps missing
- feat(ui): waveform short-circuits client-side when deps known missing
- feat(ui): player audio waveform lazy peaks (separate feature; see design/plan under `docs/superpowers/`)

### Changed
- compress: physical `_segNN` split removed — always 1 original → 1 compressed; `split_max_min` / `splits_subdir` / `reencode_split` are deprecated no-ops
- analyze: `max_analyze_duration_min` default `0` (unlimited); hard-skip only applies to **legacy** segment files, not windowed whole files
- cut / export / plan / transcript_align: apply segment offsets only via `legacy_segment_offset_sec`

### Fixed
- fix(analyze): window slice re-encode fallback + shrink when >200MB; per-clip temp dirs; cancel_event on slice; fail-closed multi-window
- fix(compress): leftover `_seg*` files no longer block creating a whole-file compress; new `.vmeta` never rewrites `split_info`
- fix(waveform): missing ffmpeg returns `code=missing_binary` without `.generating` lock or error cool-down files
- fix(waveform): empty `paths.ffmpeg` must PATH-discover (not bare name `"ffmpeg"`)
- fix(waveform): treat dead-pid and same-process orphan `.generating` locks as stale
- fix(ui): waveform load on `selectVideo`, clear stale peaks on status, error cool-down handling
- fix(ui): after config save / reload, re-probe ffmpeg and refresh banner + video menus (`refreshFfmpegDepsUi`)

### Docs
- design: `docs/superpowers/specs/2026-07-18-remove-physical-split-design.md`
- plan: `docs/superpowers/plans/2026-07-18-remove-physical-split-plan.md`
- design: `docs/superpowers/specs/2026-07-18-ffmpeg-handling-design.md` (A shipped; B zip setup / C one-click install deferred)
- plan: `docs/superpowers/plans/2026-07-18-ffmpeg-handling-phase-a-plan.md`
- README / cli-reference / project.example / ROADMAP R-029: physical split → analyze windows

## 2026-07-16

### Fixed
- fix(config/ui): expose provider `max_tokens` / `timeout_sec` / `retry_attempts` / `requests_per_minute` / `poll_interval_sec` in Provider edit modal (were missing from UI)
- fix(config): default `max_tokens` is now `0` (unlimited; omit field on OpenAI-compatible calls); 0 no longer fails validation
- fix(config/ui): GET `/api/config/project` merges dataclass defaults so missing sections (plan/analyze/script/export/…) still appear in Settings
- fix(ui): Chinese labels for plan fields (`目标时长（秒）`, `每日最多片段数`, …)

### Changed
- docs: config.example.yaml documents max_tokens=0 unlimited semantics

## 2026-07-15

### Fixed
- fix(ui): original list marks offline `*.mp4` etc. as `missing:true` (relink CTA works)
- fix(ui): `PUT /api/videos/selected` keeps offline paths instead of silently dropping them
- fix(ui): compressed view still shows clips after relink (stem/basename match, not only .vmeta path)
- fix(migrate): preserve intentional empty `videos.json=[]` instead of re-scanning input_dir
- fix(cli): `transcribe` honors `-p/--project`; add `-p` to cut/export/verify/reindex
- fix(cli): export uses `--out-dir` (avoids conflict with `-o/--output` from `_add_io_args`)
- fix(doctor/check): report offline video count when selection has missing files
- fix(registry): normalize dict entries in `projects.json` so Path(dict) cannot crash
- fix(ui): fs video browser uses `VIDEO_EXTENSIONS` (includes mkv/avi) consistent with migrate
- fix(ui): guard `browse-mkdir` onclick assignment with null check to prevent crash in test/jsdom
- fix(chore): untrack `project.yaml` from git (already in `.gitignore` but accidentally tracked)
- fix(ui): remove stale `type: ignore[attr-defined]` in texts.py + handler_protocol.py (\_resolve_in and \_get_state already on Protocol)
- chore(ui): remove dead `R-XXX` placeholder code in main.js (no project-item is ever disabled)

### Added
- feat(ui): `PUT /api/videos/relink` — relink offline videos to new paths (backend + frontend `prompt()` UX)
- feat(ui): "重新关联路径..." context menu option for offline videos
- test(fs): 9 test cases for `handle_post_fs_mkdir` (missing params, path traversal, access denied, OSError)
- test(routes): 7 test cases for `handle_put_videos_relink` (missing params, bad path, not found, ambiguous match, success)
- test(frontend): 6 test cases for `relinkVideo` (api params, cancel, same-path, error, network, fallback)
- test: offline original listing, PUT selected preserve offline, compressed-after-relink, loader non-list JSON

### Changed
- docs(roadmap): mark A-007 route registry as complete (router.py already integrated)
- docs(roadmap): remove CR-003 from remaining open items (already done in Phase 2)
- docs(roadmap): refresh Remaining Open Items — mark B-089/B-092/B-095/B-096 done; record `feat/project-video-manager` complete
- docs(AGENTS.md): update test count 1111→1153, add `npm test` to Quick Reference
- compress: skip offline videos with a clear log instead of mid-run hard failure

### Removed
- chore(ui): delete `runner_feat.js` and `runner_main.js` (1027 lines of dead code; all features already in `runner.js`)

## 2026-07-14

### Added
- feat(ui): "新建文件夹" button in both video manager and browse directory modals
- feat(ui): drag-and-drop video selection in video manager (filename matching + `DataTransferItem` API)
- feat(ui): cancel buttons styled in red (`button[id$="-cancel"]`)
- docs: route registry design plan (A-007)

### Fixed
- fix(config): remove dead `input_dir`/`recursive` from `PathsConfig` dataclass
- fix(ui): register `handle_post_fs_mkdir` in `_POST_HANDLER_PARAMS` (fixes TypeError from unwanted `qs` argument)
- fix(ui): add path traversal validation in mkdir endpoint (reject `/`, `\\`, `..` in name; validate `new_dir.resolve()`)
- fix(ui): backdrop click no longer closes file-management modals (video manager, browse dir, new project, open project)
- fix(processing_state): replace `threading.Timer` with daemon `Thread` to avoid Python 3.10 `__init__` race condition
- fix(test): wrap ad-hoc `MonkeyPatch()` in `try/finally` in `test_routes_run.py` to prevent test isolation leak
- fix(test): add missing `_build_original_stem_map` patch in `test_cancel_during_extract_marks_cancelled`
- fix(test): pass `recursive=True` to `find_videos` in `test_recursive_finds_nested`

### Changed
- chore(gitignore): ignore `project.json`, `videos.json`, `*.migrate-bak`

## 2026-07-10

### Added (merged from feat/ui-redesign, PR #17/#18)
- R-010b: Confidence scoring (`_confidence` field in AI outputs)
- R-010c: Multi-model comparison CLI (`main.py compare-models`)
- R-010d: Backend prompt management API (`GET/PUT/DELETE /api/prompts`)
- R-010e: UI Prompt Management panel (Settings tab)
- R-016: Draggable UI layout (`layout.js`, localStorage persistence)
- R-019f: context_override propagation tests
- R-020: `main.py verify` sidecar integrity CLI (`clio/tasks/verify.py`)
- R-021: All-days planning (`plan --all-days`)
- R-022: Smart cover frame extraction (`clio/tasks/cover.py`)
- R-023: Transcript/visual timeline alignment (`clio/tasks/transcript_align.py`)
- R-025: Webhook trigger endpoint (`POST /api/webhook/trigger`)
- Provider connection test button + skipped diagnostics panel (CR-008)
- Video counterpart navigation (jump between compressed ↔ original)
- Toast notifications for key actions
- `whisper_cache.py` — Whisper model cache management
- `template/prompts/README.md` — prompt override documentation
- Config semantic validation for provider/task compatibility (CR-002)
- R-004e: Config section cards — collapsible layout with section labels + left accent bar (e7a93ef, af64f83)

### Fixed
- Runner.js and editor-plan.js merge corruption repaired
- Whisper model download completion flow
- Various test alignment with new `resolve_prompt_template` naming
- README restored from clean commit

## 2026-07-03

### Added
- R-017: Model Registry & Task Binding UI (14 commits, be636f2–2a712f7)
- ProviderConfig.models field for model name storage (be636f2)
- Tag input component for model name editing (c1c3ba9)
- Provider list UI with add/edit/delete in Settings Global tab (5d4c8fc)
- Task binding panel with dropdowns in Settings Project tab (87cd821)
- CSS styles for provider cards, task binding cards, modals, tag input (61d50b8)
- 39 unit tests for _renderProviderList and _renderTaskBinding (dd44169)

### Fixed
- Provider edit modal layout, default provider delete button hidden (9b9a7ab)
- Dirty state not triggered by default model population (86c88e7)
- Restore ai.debug_print_prompt/provider_ttl_min/ai.context fields lost during custom AI renderer refactor (fe63fe6)
- Various test assertions for provider card rendering and task binding (21307f5, 2a712f7)
- Video player onloadedmetadata for original source segments (a1e5b32)
- Edit modal hint about empty API key remaining unchanged (d550715)

## 2026-07-02

### Added
- Phase 6: Global vs Project Config Separation — config.yaml now holds only global settings, project.yaml holds per-project overrides
- New config format (V2): `config_version: V2` marker with auto-migration from V1
- `GlobalConfig`/`ProjectConfig` dataclasses with `CombinedX` read-only wrappers for backward compat
- `load_global_config()` / `load_project_config()` — per-layer loading
- V1→V2 migration: auto-split merged config.yaml into global-only + project.yaml on first load
- `/api/config/global` and `/api/config/project` — per-layer GET/PUT endpoints with field ownership validation
- Frontend config tab split (Global / Project / Merged views) in editor-config.js
- 36 new tests for V2 migration, combined wrappers, field validation, apply_run_paths
- `docs/project.example.yaml` — project-level config example

### Fixed
- `handle_put_config_raw` — layer validation now correctly handles split sections (e.g. `compress`) at field level
- V1→V2 migration now creates `project.yaml` for default project (not just projects.json-registered projects)
- V1→V2 migration no longer overwrites existing `project.yaml`
- `test_config.py` / `test_routes_config.py` — migrated 57 tests to new `AppConfig(global_cfg=..., project_cfg=...)` interface
- `test_tasks_cut/label/scripts/refine/transcribe/reindex` — migrated tests to use real dataclass instances instead of MagicMock
- `conftest.py` — `tmp_config` fixture now creates V2-format config.yaml + project.yaml

## 2026-07-01

### Added
- R-019: Run panel prompt injection — context_override textarea and task_prompts in POST /api/run (43a922b)

### Fixed
- B-097: Segment-specific text/script matching in video list — 3-strategy lookup replaces index-based [0] fallback (05edab2)
- B-073: `_parse_segment_info` now supports `_partNN`, `_ptNN`, `_chunkNN` in addition to `_segNN` (f2465cd)
- B-043: Pre-commit hook only re-stages files actually modified by ruff, not all staged files (25fe130)

### Changed
- B-088: ROADMAP archived — commit log, test coverage verification, code review audit moved to docs/archive/2026-07-01-roadmap-archive.md (88d7238)
- R-019f: context_override propagation — non-AI tasks swallow with `**kwargs`, AI tasks pass with explicit `context_override` param

## 2026-06-30

### Fixed
- Auto-highlight next segment when original video crosses segment boundary (viewer.js): playing a split original video now auto-jumps sidebar selection and loads the next segment's texts/voiceover without reloading the player (f0059db)

## 2026-06-26

### Added
- U-010a/b: Unit tests for 13 previously uncovered modules — overall coverage 82% → 89% (864 tests)
- New test files: `test_fs.py`, `test_static_files.py`, `test_token_routes.py`, `test_processing_state_routes.py`,
  `test_server.py`, `test_config_cache.py`, `test_refine_routes.py`, `test_shutdown.py`, `test_session_log.py`,
  `test_token_usage.py`, `test_reindex.py`, `test_export.py`, `test_export_routes.py`

### Changed
- README badge: tests 640+ → 860+

## 2026-06-25

### Added
- F-02 concurrent AI analysis: `ThreadPoolExecutor(max_workers)` for analyze step, per-video progress via `tracker.next()` (34dd5b5)
- U-002 provider cache TTL: `ai.provider_ttl_min` (default 60), expired providers auto-close on access (538064b)
- N-01 JianYing draft export: `vlog_tool/export/` package with FORMAT_REGISTRY dispatch, `draft_content.json` builder for JianYing 5.9 (f010db0, 559921c, 3349038)
- CLI `export` subcommand: `main.py export --format jianying --day day1` (008da7b)
- UI export button: "导出到剪映" in plan view, `POST /api/export` route (8e4be96, 3349038)

### Fixed
- B-02 plan state.mark key: fallback `source_stem` to JSON filename when `source_file` field is empty (bb86872)
- B-05 Whisper download: replace unsafe `PyThreadState_SetAsyncExc` with safe `requests.get(stream=True)` + chunked cancel check (6d452e6)
- B-05: restore detailed error messages via `_download_error_detail()` for HTTP/download failures
- F-02: restore `tracker.update(phase="analyze")` and per-video `tracker.next()` calls lost during refactoring

### Refactored
- A-01 server.py: move `_project_states={}` and `_config_cache=None` from class body to closure assignment (69c3321)

## 2026-06-24

### Fixed
- Transcribe: extract `_extract_orig_stem` helper, remove dead `_resolve_original_video`, fix state key for missing-original case, wire `progress_callback` to `_extract_audio` in rerun (7fc9b18)
- Transcribe: move `error_count` outside tracker guard so errors are always counted (5eabf52)
- Transcribe: from original video, CUDA fallback, centralized model download UX (428e275)

### Added
- Unit tests for files/overwrite params across all pipeline steps (fa5fee1)
- Fix review: label.py shadow, plan.py overwrite gate, type validation and tests (95b539f)

## 2026-06-22

### Added
- R-014 token usage tracking: `TokenUsage`, `AIResponse`, `FileTokenUsageStore`, Gemini/OpenAI provider usage extraction, UI Tokens panel, CLI `tokens` subcommand (01317f0–e875159)
- R-018 multi-video selection + step execution: sidebar checkboxes, `files`/`overwrite` params, pipeline filtering (3ace4f4–5b0a9c0)
- Config auto-upgrade: inject missing dataclass defaults on load (b4bd05b)
- Split reencode option for frame-accurate cuts (cd1da63)
- AI debug print prompt option (92a6d9e)
- Refine panel with context textarea and AI trigger button (089dc6a)

### Fixed
- Provider cache TOCTOU race + Gemini close leak (45e09e3)
- Transcribe thread-safe os.environ save/restore (9ef45e2)
- Config propagate max_tokens to ProviderConfig (dc3ad72)
- Various code review fixes (6efbcc3, 7017ff6, badb621, bdcc678)
- ffprobe integrity check for skip_existing (6c3c231)
- ETA fix: elapsed_total moved to finally block (94f4501)
- Restore missing analyze header in config.example.yaml (d799f21)

## 2026-06-20

### Added
- Whisper model UI download: POST /api/whisper/install with progress, download button in transcript tab (326fe46, e361f7d)
- Project remove + empty state UI (12c314e, 360b91a)
- Quick-launch serve scripts (fcbccf5)
- ENV_FIREWORKS_API_KEY support

### Fixed
- UI event binding order for empty state (aa720d8, c1584df)
- Lint + UT after empty-state changes (fe45f53)

## 2026-06-19

### Fixed
- Setup scripts: idempotency, input dir check, CUDA disk space (3a5eaed)
- Modal event binding before init early return (aa720d8)
- All event handlers before try block for empty state (c1584df)

## 2026-06-18

### Added
- R-012 preview progress bar: two-row layout, play/pause toggle, segment blocks with tooltip (298a729–5029ba1)
- UI layout overhaul: resizable panels, dark OLED theme (7f5c0d6)

### Fixed
- ProcessingState recorded after generating plan (a29a53c)
- max_tokens + temperature in OpenAI API calls (3660fea)
- compress MIN_VALID_SIZE 256→50KB (78a0b69)
- transcribe find_videos for recursive scanning (6d23de3)
- Trip context cache key includes file mtime (cdcc873)
- TRANSCRIPT_CONTEXT English→Chinese (3ce9ef3)
- Atomic writes for scripts/refine output (123c84f)
- Split clean up partial segments + atomic manifest (097a6ff)
- Stale existing files cleanup on source_file mismatch (eb93573)
- Structured validation for AI responses (129de90)
- Closure late-binding trap in progress callback (d410c4e)

## 2026-06-17

### Fixed
- 10 commits covering usability and code quality issues from comprehensive analysis (a29a53c–d410c4e):
  - Plan state recording, atomic writes, max_tokens validation
  - Closure trap fix, transcript Chinese, prompts validation

## 2026-06-16

### Fixed
- Second code review: 5 S0 + 5 S1 + 1 S2 items all fixed:
  - runner.js prog undefined (S0-1)
  - analyze.py transcript bound to source_stem (S0-2)
  - split.py missing manifest (S0-3)
  - Pipeline recovery atomic writes (S0-4)
  - /api/cut project query parameter (S1-1)
  - transcript UI _segNN stripping (S1-2)
  - _extract_audio ffmpeg parameter (S1-3)
  - Whisper batch gated by max_analyze_duration (S1-4)
  - AI provider cache composite key (S1-5)
  - Whisper model cache key device/compute_type (S2-3)

### Added
- Comprehensive code review: 6 Critical + 12 Important + 36 Minor, fixed 6+12+5

## Earlier

See git log for full history. Key milestones:
- Initial scaffold (commit 1)
- ffmpeg compress with comma escaping fix (commit 2)
- Whisper ASR full integration (commits ~98–112)
- Security fixes: _is_safe_basename, non-retryable 4xx (commits 113–118)
- UI fixes: video ref, state capture, btn guard (commits 119–122)
- Provider cache + test isolation (commits 130–131)
- CI fixes for ctranslate2 mock, Linux case, lint (commits 133–136)
- 27 new tests for whisper, processing_state, CLI (commit 137)

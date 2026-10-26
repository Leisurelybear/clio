# Directory Tree — Current Code Map

> Load on demand when locating ownership boundaries or test coverage. Keep `AGENTS.md` short; put detailed file maps here.
>
> Last refreshed: 2026-10-23 (after the R-025 i18n completion batch).

```
vlog-video-analysis/
├── main.py                    # Thin CLI entry; delegates to clio.main
├── config.example.yaml        # Global config example; no local paths or keys
├── docs/project.example.yaml  # Per-project config example
├── templates/
│   ├── prompts/               # Prompt override templates
│   ├── trip_context.md        # Trip background and AI rules, injected into prompts
│   └── vlog_template.md       # Voice-over style template
├── docs/
│   ├── CONVENTIONS.md         # docs/ naming rules (date + kebab-case)
│   ├── cli-reference.md       # Full CLI reference
│   ├── project.example.yaml
│   ├── architecture/          # 00-overview … 13-observability
│   ├── analysis/              # Review and audit reports
│   ├── archive/               # Archived completed roadmap/history sections
│   ├── refactor/              # Older refactor notes
│   ├── review/                # Detailed review documents
│   ├── screenshots/           # README images
│   └── superpowers/
│       ├── agents/            # AI bootloader references (this file, gotchas, …)
│       ├── plans/             # Implementation plans
│       ├── specs/             # Designs/specs
│       └── reviews/           # Superpowers review outputs
└── clio/
    ├── main.py                 # CLI parser and command dispatch
    ├── pipeline.py             # Shared CLI/UI pipeline orchestration
    ├── doctor.py               # Full environment diagnosis (`doctor`)
    ├── i18n.py                 # Catalog lookup, locale resolution, active locale
    ├── analyze.py              # AI-facing helpers: analyze, script, plan, refine
    ├── analyze_windows.py      # Long-clip logical windows (slice/merge; no physical split)
    ├── prompts.py              # Prompt constants and output contracts (never translated)
    ├── compress.py             # Single-file ffmpeg compression
    ├── cut.py                  # Segment cutting wrapper
    ├── ffmpeg_install.py       # Verified cross-platform ffmpeg downloader (R-028b/c)
    ├── transcribe.py           # Whisper ASR core
    ├── whisper_cli.py          # Whisper CLI install/check entry
    ├── whisper_cache.py        # faster-whisper model cache layout helpers
    ├── gpmf.py                 # GoPro GPMF telemetry helpers (R-024)
    ├── utils.py                # Subprocess wrappers, ffmpeg discovery, JSON extraction, atomic IO
    ├── resources.py            # Bundled config/template lookup for repo, wheel, PyInstaller
    ├── index.py                # Unified artifact index (compressed/texts/scripts/transcripts)
    ├── identity.py             # Canonical media identity helpers
    ├── instance_probe.py       # Web-port probe: clio / other / unreachable
    ├── plan_model.py           # Plan/PlanSegment domain + save validation (R-026)
    ├── plan_preview.py         # Cut-clip mapping for plan preview (R-031b)
    ├── plan_readiness.py       # Export/cut readiness (error/warning tiers)
    ├── privacy.py              # Log redaction of tokens and local paths
    ├── ratelimit.py            # Thread-safe provider rate limiter
    ├── progress.py             # `.progress.json` tracker for CLI/UI runs
    ├── processing_state.py     # Per-file step status
    ├── schema.py               # Artifact schema versioning helpers
    ├── log.py                  # Tee/stdout capture, hourly disk rotation, redaction
    ├── session_log.py          # Process-memory log ring buffer for live UI tail
    ├── log_history.py          # Read-only hourly-log listing and capped tail (R-027e)
    ├── prompt_overrides.py     # Prompt override lookup and caching
    ├── shutdown.py             # beforeStop hooks and subprocess cleanup
    ├── vmeta.py                # `.vmeta` and `.vindex` sidecar models
    ├── _constants.py           # Shared extension sets and constants
    ├── _str_enum.py            # StrEnum shim for Python < 3.11
    ├── locales/                # Backend/CLI catalogs: zh-CN.json, en.json
    ├── config/
    │   ├── models.py           # GlobalConfig, ProjectConfig, AppConfig
    │   ├── loader.py           # config.yaml/project.yaml loading and auto-upgrade
    │   ├── parsers.py          # provider/task parsing
    │   ├── validators.py       # ownership and validation helpers
    │   ├── schema.py           # UI schema (groups, widget hints, choices)
    │   ├── descriptions.py     # UI field descriptions (zh-CN source of truth)
    │   └── enums.py            # Config enums (whisper model/device/language)
    ├── ai/
    │   ├── base.py             # TaskName enum and provider protocol
    │   ├── factory.py          # Provider cache and lookup
    │   ├── gemini.py           # Gemini multimodal File API
    │   ├── openai_compat.py    # OpenAI-compatible text providers
    │   └── token_usage.py      # Token usage store and aggregation
    ├── asr/
    │   ├── base.py             # TranscriptSegment and provider protocol
    │   ├── factory.py          # Decorator-based provider registry
    │   ├── readiness.py        # Engine-aware readiness blockers
    │   ├── retry.py            # Bounded cloud HTTP retry helper
    │   ├── local.py            # faster-whisper adapter
    │   ├── aliyun.py           # DashScope async ASR provider
    │   └── upload.py           # OSS multipart upload
    ├── tasks/
    │   ├── compress.py         # Compress all/selected files, vmeta/vindex writing
    │   ├── analyze.py          # Analyze compressed clips
    │   ├── scripts.py          # Generate voiceover JSON
    │   ├── plan.py             # Generate day plans
    │   ├── refine.py           # Refine text/script outputs
    │   ├── transcribe.py       # Batch transcription
    │   ├── transcript_align.py # Attach transcript snippets to timeline entries
    │   ├── cut.py              # Batch cut export
    │   ├── label.py            # Burn index labels into compressed clips
    │   ├── cover.py            # Cover frame extraction
    │   ├── waveform.py         # Lazy audio waveform peaks cache
    │   ├── reindex.py          # Rebuild vmeta/vindex sidecars
    │   ├── verify.py           # Verify metadata integrity
    │   ├── migrate.py          # Legacy project migration
    │   ├── compare_models.py   # Multi-provider/model comparison
    │   ├── _helpers.py         # Shared task utilities and CSV/text output helpers
    │   └── _video_loader.py    # videos.json selection load/save
    ├── task_center/
    │   ├── manager.py          # Task lifecycle, cancellation, retry
    │   ├── executor.py         # Job execution wrapper
    │   ├── models.py           # Task/event dataclasses
    │   ├── state_machine.py    # Allowed status transitions
    │   ├── store.py            # SQLite history + notification inbox
    │   ├── reporter.py         # Progress/log event reporting
    │   └── schema.py           # Store schema and migrations
    ├── desktop/
    │   ├── app.py              # pywebview host, close policy, focus callback (R-039)
    │   ├── server_host.py      # Non-blocking localhost server lifecycle
    │   ├── single_instance.py  # clio.lock + focus probe + web-8765 detection (R-039)
    │   ├── notifications.py    # Native toast delivery (PowerShell/osascript/notify-send)
    │   ├── dialogs.py          # Native folder/file pickers
    │   ├── api.py              # JS bridge API
    │   └── state.py            # desktop-state.json (last_dir persistence)
    ├── export/
    │   └── jianying.py         # JianYing/CapCut draft export
    ├── ui/
    │   ├── server.py           # stdlib http.server dispatcher and auth gate
    │   ├── http_server.py      # Bounded ThreadingHTTPServer with request timeouts
    │   ├── router.py           # Declarative URL router ({param} + prefixes)
    │   ├── handler_protocol.py # Typed protocol for the dynamic handler methods
    │   ├── services/
    │   │   ├── config_cache.py    # Fingerprint-aware AppConfig cache + locale sync
    │   │   ├── file_service.py
    │   │   ├── network_service.py # Windows SMB server/share discovery for the picker
    │   │   ├── project_service.py
    │   │   ├── run_preview.py     # Dry-run step preview shared with the run pane
    │   │   └── ai_test_service.py # Provider connectivity test
    │   ├── routes/             # Focused route modules
    │   │   ├── ai.py              # /api/ai/test
    │   │   ├── config_routes.py
    │   │   ├── deps.py            # /api/deps/* (ffmpeg, keys, install)
    │   │   ├── env_routes.py
    │   │   ├── export.py
    │   │   ├── fs.py
    │   │   ├── plan.py
    │   │   ├── processing_state_routes.py
    │   │   ├── projects.py
    │   │   ├── notifications.py   # Persistent notification inbox API and SSE
    │   │   ├── prompts.py
    │   │   ├── refine.py
    │   │   ├── run.py             # Run start/rerun submission to Task Center
    │   │   ├── static_files.py
    │   │   ├── tasks.py           # Unified Task Center API and SSE
    │   │   ├── texts.py
    │   │   ├── token_routes.py
    │   │   ├── transcripts.py
    │   │   ├── videos.py
    │   │   ├── waveform.py
    │   │   ├── whisper_check.py
    │   │   ├── whisper_download.py
    │   │   ├── whisper_models.py
    │   │   └── whisper_routes.py  # Re-export barrel for the whisper* modules
    │   └── static/
    │       ├── index.html
    │       ├── style.css
    │       ├── locales/           # Web UI catalogs: zh-CN.json, en.json
    │       └── src/
    │           ├── main.js                    # Boot sequence and wiring
    │           ├── api.js / state.js / utils.js / layout.js / theme.js
    │           ├── i18n.js                    # Catalog lookup, locale events, data-i18n apply
    │           ├── language-switcher.js       # Header 中/EN button + `ui` block defaults
    │           ├── sidebar.js / sidebar-data.js / sidebar-video-filter.js
    │           ├── sidebar-rerun.js / sidebar-relink.js / sidebar-batch-relink.js
    │           ├── sidebar-video-manage.js / sidebar-browse.js
    │           ├── runner.js                  # Run pane, step progress, rerun
    │           ├── viewer.js                  # Player, preview bar, subtitle settings
    │           ├── video-menu.js / video-selection.js / video-fade.js / video-url.js
    │           ├── video-drag-select.js      # Sidebar rubber-band selection (rect math + pointer)
    │           ├── file-browser.js / select-btn.js / latest.js / empty-states.js
    │           ├── offline-media.js / preview-preload.js / session-restore.js
    │           ├── url-params.js / editor-save.js / desktop-pick.js
    │           ├── desktop-notify.js          # Native delivery eligibility and dedupe
    │           ├── editor.js / editor-texts.js / editor-voiceover.js / editor-refine.js
    │           ├── editor-config.js           # Config form, providers, task binding, logs
    │           ├── editor-plan.js / plan-edit.js / plan-export.js / plan-timeline.js
    │           ├── plan-range-picker.js / plan-preview-media.js / plan-subtitle.js
    │           ├── plan-waveform.js / subtitle-settings.js / waveform.js
    │           ├── logs-filter.js / logs-buffer.js / logs-history.js
    │           ├── notification-center.js / task-center.js / runtime-warnings.js
    │           ├── toast.js / transcription-status.js / ffmpeg-install.js
    │           └── __tests__/                 # Vitest module tests
    └── tests/                  # 117 pytest modules (2400+ cases)
```

## Test Map

Use focused tests first, then full regression:

- Core utilities: `clio/tests/test_utils.py`, `test_utils_expanded.py`
- Config split: `test_config_v2.py`, `test_routes_config.py`, `test_config_cache.py`, `test_config_schema.py`
- Pipeline/run: `test_pipeline.py`, `test_routes_run.py`, `test_progress.py`, `test_processing_state.py`,
  `test_run_preview.py`
- Task Center: `test_task_center_manager.py`, `test_task_center_models.py`, `test_task_center_store.py`,
  `test_task_concurrency.py`, `test_http_sse_lifecycle.py`
- Notifications: `test_notifications.py`, `test_desktop_notifications.py`; frontend
  `notification-center.test.js`, `desktop-notify.test.js`, `toast.test.js`
- Logs: `test_log.py`, `test_session_log.py`, `test_log_history.py`; frontend `logs-filter.test.js`,
  `logs-buffer.test.js`, `logs-history.test.js`, `logs-ui.test.js`, `logs-locale.test.js`
- Media identity: `test_identity.py`, `test_vmeta.py`, `test_routes_videos.py`, `test_file_service.py`,
  `test_index.py`
- Plan domain: `test_plan.py`, `test_plan_model.py`, `test_plan_readiness.py`, `test_plan_preview.py`;
  frontend `plan-*.test.js`, `viewer-cut-preview.test.js`
- ASR: `test_asr*.py`, `test_transcribe.py`, `test_transcribe_engine_switch.py`, `test_tasks_transcribe.py`,
  `test_transcript_align.py`, `test_whisper_cli.py`
- ffmpeg install: `test_ffmpeg_install.py`, `test_routes_ffmpeg_install.py`; frontend
  `ffmpeg-install.test.js`, `ffmpeg-deps-guard.test.js`
- Desktop shell: `test_desktop_*.py`
- i18n: `test_i18n.py`, `test_i18n_api_copy.py`, `test_i18n_inventory.py` (Python inventory guard);
  frontend `i18n.test.js`, `i18n-catalog.test.js`, `i18n-html.test.js`, `i18n-modules.test.js`,
  `i18n-inventory.test.js` (JS inventory guard), `language-switcher.test.js`, `*-locale.test.js`
- UI dispatch/auth: `test_server.py`, `test_router.py`, `test_http_server.py`, route-specific `test_routes_*.py`
- Frontend modules: `npm test` with Node 22.12+; `node --check` works on individual ES modules for syntax only

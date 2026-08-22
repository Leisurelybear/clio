# Directory Tree — Current Code Map

> Load on demand when locating ownership boundaries or test coverage. Keep `AGENTS.md` short; put detailed file maps here.

```
vlog-video-analysis/
├── main.py                    # Thin CLI entry; delegates to clio.main
├── config.example.yaml         # Global config example; no local paths or keys
├── docs/project.example.yaml   # Per-project config example
├── templates/
│   ├── trip_context.md         # Trip background and AI rules, injected into prompts
│   └── vlog_template.md        # Voice-over style template
├── clio/
│   ├── main.py                 # CLI parser and command dispatch
│   ├── pipeline.py             # Shared CLI/UI pipeline orchestration
│   ├── analyze.py              # AI-facing helpers: analyze, script, plan, refine
│   ├── prompts.py              # Prompt constants and output contracts
│   ├── compress.py             # Single-file ffmpeg compression
│   ├── cut.py                  # Segment cutting wrapper
│   ├── transcribe.py           # Whisper ASR core
│   ├── whisper_cli.py          # Whisper CLI install/check entry
│   ├── utils.py                # Subprocess wrappers, ffmpeg discovery, JSON extraction, atomic IO
│   ├── vmeta.py                # `.vmeta` and `.vindex` sidecar models
│   ├── identity.py             # Canonical media identity helpers
│   ├── progress.py             # `.progress.json` tracker for CLI/UI runs
│   ├── processing_state.py     # Per-file step status
│   ├── prompt_overrides.py     # Prompt override lookup and caching
│   ├── shutdown.py             # beforeStop hooks and subprocess cleanup
│   ├── config/
│   │   ├── models.py           # GlobalConfig, ProjectConfig, AppConfig
│   │   ├── loader.py           # config.yaml/project.yaml loading and auto-upgrade
│   │   ├── validators.py       # ownership and validation helpers
│   │   ├── parsers.py          # provider/task parsing
│   │   └── descriptions.py     # UI field descriptions
│   ├── ai/
│   │   ├── base.py             # TaskName enum and provider protocol
│   │   ├── factory.py          # Provider cache and lookup
│   │   ├── gemini.py           # Gemini multimodal File API
│   │   ├── openai_compat.py    # OpenAI-compatible text providers
│   │   └── token_usage.py      # Token usage store and aggregation
│   ├── tasks/
│   │   ├── compress.py         # Compress all/selected files, vmeta/vindex writing
│   │   ├── analyze.py          # Analyze compressed clips
│   │   ├── scripts.py          # Generate voiceover JSON
│   │   ├── plan.py             # Generate day plans
│   │   ├── refine.py           # Refine text/script outputs
│   │   ├── transcribe.py       # Batch transcription
│   │   ├── cut.py              # Batch cut export
│   │   ├── label.py            # Burn index labels into compressed clips
│   │   ├── reindex.py          # Rebuild vmeta/vindex sidecars
│   │   ├── verify.py           # Verify metadata integrity
│   │   └── _helpers.py         # Shared task utilities and CSV/text output helpers
│   ├── export/
│   │   └── jianying.py         # JianYing/CapCut draft export
│   ├── ui/
│   │   ├── server.py           # stdlib http.server dispatcher and auth gate
│   │   ├── handler_protocol.py # Route handler protocol
│   │   ├── services/
│   │   │   ├── config_cache.py
│   │   │   ├── file_service.py
│   │   │   └── project_service.py
│   │   ├── routes/             # Focused route modules
│   │   │   ├── config_routes.py
│   │   │   ├── env_routes.py
│   │   │   ├── export.py
│   │   │   ├── fs.py
│   │   │   ├── plan.py
│   │   │   ├── processing_state_routes.py
│   │   │   ├── projects.py
│   │   │   ├── notifications.py # Persistent notification inbox API and SSE
│   │   │   ├── prompts.py
│   │   │   ├── refine.py
│   │   │   ├── run.py
│   │   │   ├── static_files.py
│   │   │   ├── tasks.py         # Unified Task Center API and SSE
│   │   │   ├── texts.py
│   │   │   ├── token_routes.py
│   │   │   ├── transcripts.py
│   │   │   ├── videos.py
│   │   │   ├── whisper_check.py
│   │   │   ├── whisper_download.py
│   │   │   └── whisper_models.py
│   │   └── static/
│   │       ├── index.html
│   │       ├── style.css
│   │       └── src/
│   │           ├── main.js
│   │           ├── api.js
│   │           ├── state.js
│   │           ├── sidebar.js
│   │           ├── sidebar-data.js
│   │           ├── sidebar-video-filter.js
│   │           ├── sidebar-rerun.js
│   │           ├── sidebar-browse.js
│   │           ├── runner.js
│   │           ├── viewer.js
│   │           ├── editor.js
│   │           ├── editor-config.js
│   │           ├── editor-plan.js
│   │           ├── editor-texts.js
│   │           ├── editor-voiceover.js
│   │           ├── editor-refine.js
│   │           ├── layout.js
│   │           ├── theme.js
│   │           ├── notification-center.js # Persistent inbox, unread badge, SSE, filters/read state
│   │           ├── task-center.js        # Global task list/detail, cancel/retry, active badge
│   │           ├── runtime-warnings.js  # Register frontend warnings in the notification inbox
│   │           ├── toast.js
│   │           └── utils.js
│   └── tests/                 # 1300+ pytest cases
├── docs/
│   ├── cli-reference.md
│   ├── archive/               # Archived completed roadmap/history sections
│   ├── analysis/              # Code review and audit reports
│   ├── review/                # Detailed review documents
│   ├── refactor/              # Older refactor notes
│   └── superpowers/
│       ├── agents/            # AI bootloader references
│       ├── plans/             # Implementation plans
│       ├── specs/             # Designs/specs
│       └── reviews/           # Superpowers review outputs
└── .opencode/skills/          # Project skills for repeatable AI workflows
```

## Test Map

Use focused tests first, then full regression:

- Core utilities: `clio/tests/test_utils.py`, `test_utils_expanded.py`
- Config split: `test_config_v2.py`, `test_routes_config.py`, `test_config_cache.py`
- Pipeline/run: `test_pipeline.py`, `test_routes_run.py`, `test_progress.py`, `test_processing_state.py`
- Task Center: `test_task_center_manager.py`, `test_task_center_models.py`, `test_task_center_store.py`
- Notifications: `test_notifications.py`; frontend coverage includes `notification-center.test.js` and `task-center.test.js`
- Media identity: `test_identity.py`, `test_vmeta.py`, `test_routes_videos.py`, `test_file_service.py`
- UI dispatch/auth: `test_server.py`, route-specific `test_routes_*.py`
- Frontend modules: `npm test` with Node 18+; `node --check` works on individual ES modules for syntax only

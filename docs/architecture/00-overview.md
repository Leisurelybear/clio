# Clio — Architecture Overview

> Series index: this is document `00`. Sibling documents (01–13) detail each subsystem.
> Written 2026-09-07 from a point-in-time inventory of the codebase.

## 1. What Clio Does

Clio is a **local, single-user media preprocessing workbench** for travel vlogs. It turns raw
footage into editorial assets without a full editing suite:

1. compress raw clips (ffmpeg, GoPro 4K → small preview files),
2. have an AI (Gemini video model) review each clip and write analysis JSON,
3. generate voice-over scripts and transcribe speech (local Whisper or cloud ASR),
4. draft a day-plan (which clips, in what order, subtitles, voiceover),
5. preview that plan against the real media, cut segments, and export a JianYing/CapCut draft.

The user edits the final cut manually in JianYing; Clio handles everything up to that point.
Both a **CLI** (`main.py`) and a **local Web UI** / **desktop shell** drive the same pipeline.

## 2. End-to-End Data Flow

```text
 project_dir/
 ├── input/                       raw footage (GoPro .MP4 …)
 ├── compressed/{n}_*.mp4         ffmpeg-compressed previews (.vmeta sidecar)
 ├── texts/{n}_*.json             AI analysis (timeline, mood, summary, voiceover hint)
 ├── scripts/{n}_*.json           generated voice-over scripts
 ├── transcripts/*_transcript.json  Whisper/cloud ASR output
 ├── plans/day1_plan.json         assembled plan (Plan domain model)
 ├── output/cuts/day1/            cut segments
 ├── export/day1_jianying/        JianYing draft_content.json (+ copy to draft dir)
 ├── videos.json                  selected-video manifest (media identity)
 ├── summary.csv                  run summary
 └── .token_usage.json            token accounting
```

Pipeline steps (each maps to a `clio/tasks/` module, orchestrated in `clio/pipeline.py`):

```text
0  auto_reindex_if_needed      rebuild .vmeta/.vindex when missing/stale   (reindex)
1  run_compress_all            raw → compressed previews                  (compress)
2  run_analyze_all             compressed → texts JSON (Gemini)           (analyze)
3  run_generate_scripts        texts → voiceover JSON                     (scripts)
4  run_transcribe_all          audio → transcript JSON                    (transcribe)
5  run_plan_vlog / all_days    analyses + transcripts → plan JSON         (plan)
6  run_label_videos            burn index labels into compressed clips    (label)
   ── downstream, user-driven ──────────────────────────────────────────────
7  run_cut_all                 plan → cut segments                        (cut)
8  export_plan                 plan → JianYing draft                      (export)
```

Every step skips existing artifacts by default (`skip_existing=True`, toggled by `--force`),
so a failed run is resumable.

## 3. Runtime Entry Points & Deployment Model

Three entry surfaces share the same backend modules:

| Surface | Entry | Process model |
|---|---|---|
| CLI | `main.py` (thin) → `clio.main.main()` | One-shot process; `before_stop()` cleanup in `finally`; hourly file logging for long commands (`serve` only logs setup). |
| Web UI | `python main.py serve --host/--port/--token` → `clio.ui.run()` | Long-lived `BoundedThreadingHTTPServer`; stdlib `http.server`; SSE long-poll; background threads per AI call / Task Center task. |
| Desktop | `python main.py desktop` → `clio.desktop.app.main()` | Bundles the same localhost server in a background thread + a pywebview window; single-instance lock; per-launch token; native file dialogs via `DesktopApi` js_api. |

Background execution is unified in the **Task Center** (`clio/task_center/`): pipeline, rerun,
cut/export, whisper-install, and waveform all run as managed tasks persisted in a SQLite store,
remotely cancelable, with SSE event streams and a notification inbox. See `08-task-center.md`.

## 4. Module Dependency Map

Layering (top → bottom; arrows point from consumer to dependency):

```text
entry         main.py, clio/main.py, clio/desktop/app.py
    │
orchestration clio/pipeline.py           run pipeline steps, progress, cancel
    │
tasks         clio/tasks/*               compress, analyze, scripts, plan, refine,
    │                                     transcribe, cut, label, reindex, verify,
    │                                     waveform, cover, migrate, compare_models
    ├───────────────────────┐
domain       clio/plan_model.py, plan_readiness.py, analyze_windows.py
AI/ASR       clio/ai/*, clio/analyze.py, clio/prompts.py, clio/asr/*, clio/transcribe.py
media	    clio/compress.py, clio/cut.py, clio/identity.py, clio/vmeta.py, clio/gpmf.py
    │
UI backend   clio/ui/server.py, router, routes/*, services/*      (Web/desktop only)
desktop      clio/desktop/*                                        (desktop only)
task center  clio/task_center/*                                    (async execution)
    │
config       clio/config/*           models, loader, validators, parsers, descriptions
infra        clio/utils.py, log.py, session_log.py, progress.py, processing_state.py,
             shutdown.py, privacy.py, ratelimit.py, whisper_cache.py
```

Dependencies are unidirectional: `tasks → domain → (ai|asr|media → config → infra)`. UI routes
dispatch to both tasks (for background work) and domain modules (for reads/preview).

## 5. Configuration Split

- **`config.yaml`** (global, repo-level): proxy, server (token/retention), naming, paths (ffmpeg,
  ffprobe, logs), ai (providers, ttl), compress (codec/fps/crf/audio), whisper (cache/hf endpoint).
- **`project.yaml`** (per `project_dir`): paths.output_dir, ai.tasks/context, compress size,
  analyze, script, plan, export, preview, whisper (enabled/model/language/device/engine).
- **`.env`** (gitignored): API keys only — `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `DASHSCOPE_API_KEY`, …
- **`config.example.yaml` / `docs/project.example.yaml`**: committed examples, no secrets/paths.
- Loading auto-upgrades schemas (V1→V2 split) and auto-generates a missing `config.yaml` from the
  bundled example. Runtime access is via a merged read-only view `AppConfig`. See `02-config-system.md`.

## 6. Support Matrix

| Dimension | Supported | Notes |
|---|---|---|
| Python | 3.11, 3.12 | PEP 604 unions; CI matrix Ubuntu/Windows/macOS |
| ASR local | faster-whisper | ctranslate2; CUDA optional, CPU fallback |
| ASR cloud | Aliyun DashScope `paraformer-v2` | `whisper.engine: cloud`; keys in `.env` |
| AI providers | Gemini (video), OpenAI-compatible text (DeepSeek/Tongyi/Moonshot/OpenAI) | provider registry, capability tags |
| ffmpeg | ffprobe + ffmpeg | `paths.ffmpeg`/`ffprobe` or PATH discovery |
| Export | JianYing/CapCut 5.9 draft JSON | `FORMAT_REGISTRY`, extensible |
| Web UI | stdlib http.server | no build step; ES modules |
| Desktop | Windows x64 + macOS universal2 | PyInstaller onedir; pywebview |
| Tests | pytest (backend) + Vitest (frontend) | coverage `fail_under=70`; mypy subset gated in CI |

## 7. Series Index

| Doc | Topic |
|---|---|
| 00 | This overview |
| 01 | CLI & pipeline orchestration |
| 02 | Config system (global/project split, AppConfig) |
| 03 | AI providers, prompts, token usage |
| 04 | ASR & transcription (local + cloud engines) |
| 05 | Media identity (`.vmeta`/`.vindex`, matching) |
| 06 | Media processing tasks (compress/cut/label/waveform/…) |
| 07 | Analysis & plan domain (windows, scripts, plan model, readiness) |
| 08 | Task Center (lifecycle, SQLite store, SSE, notifications) |
| 09 | HTTP server (routes, auth, project resolution, file streaming) |
| 10 | UI frontend (ES modules, viewers, preview) |
| 11 | Desktop shell (pywebview, single instance, dialogs) |
| 12 | JianYing export |
| 13 | Observability (logging, session log, progress, token accounting, privacy) |

## 8. Key Design Decisions Worth Knowing

1. **Resumability over correctness-per-uniqueness** — most outputs are keyed by numeric index
   (`001`, `002`, …); `skip_existing` + fingerprint caches make reruns cheap and stale artifacts
   garbage-collected by the compress/analyze commits.
2. **Compatibility via read-only combined views** — the V2 config split keeps legacy merged
   dataclasses (`PathsConfig`, `AIConfig`, …) as deprecated aliases so old callers compile.
3. **SSE is long-poll over SQLite cursors**, not pub/sub — each subscriber polls `store.events(after_seq)`
   every ~250 ms; `Last-Event-ID` enables resume. This trades throughput for zero moving parts.
4. **Cancellation is cooperative** — a `threading.Event` flows through `TaskReporter`;
   handlers must call `raise_if_cancelled()` at checkpoints. No thread kills.
5. **Writes are atomic** — JSON/output writes go through `_save_atomic` (unique temp + `os.replace`
   + fsync parent + stale `*.tmp` sweep) to survive crashes.
6. **API keys never persist in task history** — `sanitize_task_payload` strips `api_key`/`prompt`
   keys; secrets live only in `.env` + memory (`private_input_data` for Task Center).
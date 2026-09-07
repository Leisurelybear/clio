# Clio — CLI & Pipeline Orchestration

> Series: `01` of `00-overview.md`. Covers `main.py`, `clio/main.py`, `clio/pipeline.py`,
> `clio/doctor.py`, `clio/shutdown.py`.

## 1. Responsibilities

- Expose every pipeline capability as a one-shot CLI command (`compress`, `analyze`, `scripts`,
  `plan`, `run`, `cut`, `export`, `refine`, `transcribe`, `verify`, `reindex`, `migrate`, …) and
  long-lived services (`serve`, `desktop`).
- Resolve the project directory and load config **once** per invocation (`_prepare_config`), then
  drive the step functions through the generic step registry in `clio/pipeline.py`.
- Guarantee cleanup on exit (kill lingering ffmpeg subprocesses, close AI pools) via
  `clio/shutdown.py` installed at startup and invoked in `finally`.

## 2. Module Map

| File | Responsibility |
|---|---|
| `main.py` (repo root) | Thin wrapper: `from clio.main import main; raise SystemExit(main())` |
| `clio/main.py` | `argparse` parser, all subcommands, dispatch branch (`:136`); `_prepare_config` (`:42`) resolves project + optional output override; `run_check` (`:55`) env checks |
| `clio/pipeline.py` | Step registry `_STEP_FUNCS` (`:64`), 7-step `run_full_pipeline` (`:37`), selective `run_pipeline_steps` (`:76`); re-exports task functions for backward compat |
| `clio/doctor.py` | `collect_doctor_checks` (`:110`) diagnostic inventory; `run_doctor` (`:200`); `DoctorItem` model + exit code policy |
| `clio/shutdown.py` | `register_process`/`unregister_process` process registry; `before_stop` (`:61`) idempotent cleanup; `install_hooks` (`:132`) atexit + SIGTERM |

## 3. Command Dispatch

`main()` parses a global `-c/--config` and `--force`, then a required `command` subparser.
Every step subcommand takes `-p/--project`, hidden `-i/--input`, and `-o/--output` via
`_add_io_args` (`:21`). Key command branches:

| Command | Behavior |
|---|---|
| `check` | `run_check`: venv, ffmpeg/ffprobe, project videos (offline count), AI provider keys, proxy |
| `doctor` | `run_doctor`: same inventory + writability + Node.js version; FAIL items → exit 1 |
| `compress` / `analyze` / `scripts` / `label` / `plan` / `run` | `auto_reindex_if_needed` first then the corresponding `run_*` from `clio/tasks/`; `plan --all-days`/`--no-transcripts`; `run` = `run_full_pipeline` |
| `cut` / `export` | Load `plans/<day>_plan.json` → `Plan.from_dict` → readiness check (`collect_project_indices` + `check_plan_export_readiness` + `readiness_block_payload`); exit 1 with printed issues when blocked |
| `refine` | `--target texts/scripts/all`, single-file `-i` for `--fix`; warns when no trip context configured |
| `transcribe` | `run_transcribe_all` with `--force` flipping `skip_existing` |
| `whisper install/check` | `clio/whisper_cli.py` |
| `compare-models` | `clio/tasks/compare_models.run_compare_models` |
| `migrate` / `migrate-config` | old-project migration / provider-field backfill |
| `tokens` | print `.token_usage.json` stats |
| `serve` | `clio.ui.run` with host/port/`--no-browser`/`--token` |
| `desktop` | `clio.desktop.app.main` (self-contained; skips the shared setup/cleanup path) |

Exceptions are converted to `错误: …` on stderr with exit code 1; `before_stop()` always runs in
`finally`.

## 4. Pipeline Step Registry

`clio/pipeline.py` is the compatibility façade between the CLI/UI and `clio/tasks/*`:

- `_STEP_LABELS` (`:55`): user-facing Chinese labels for the 6 UI/CLI steps.
- `_STEP_FUNCS` (`:64`): `compress → run_compress_all`, `analyze → run_analyze_all`,
  `voiceover → run_generate_scripts`, `transcribe → run_transcribe_all`, `plan → run_plan_vlog`,
  `label → run_label_videos`.
- `_STEP_DAY_ARG` (`:73`): `plan` alone receives `day_label` positionally.
- `run_pipeline_steps` (`:76`): validates unknown steps, iterates with per-step progress
  (`ProgressTracker.update`), propagates `cancel_event`/`files`/`overwrite`/`context_override`/
  `task_prompts` kwargs, treats a non-zero int result as failure, and returns
  `{steps, processed, warning_count, error_count}`.
- `run_full_pipeline` (`:37`): the immutable 7-step scripted flow used by `main.py run`.

Step functions follow a common signature: `run_X(config, tracker=None, cancel_event=None,
files=None, overwrite=False, **kwargs) -> int | list | None`. Non-AI steps silently accept and
ignore AI-only kwargs so one call site works for every step.

## 5. Cleanup Contract (`clio/shutdown.py`)

- ffmpeg subprocesses register/unregister themselves via `register_process`; `before_stop` drains
  the registry, `terminate()` then `kill()` stragglers with timeout.
- `_clear_provider_cache()` closes AI HTTP clients.
- `install_hooks` registers `atexit` + SIGTERM handler; `_signal_handler` runs cleanup then
  re-raises the signal; `_sprint` is exception- and re-encode-safe (Windows cp1252 console +
  closed-stdout guard), because a raise here would flip the exit code to 120.
- Idempotent via `_called`; `reset_stop_flag` exists for tests.

## 6. Cross-Module Dependencies

- → `clio/config` (`load_config`, `apply_run_paths`, `AppConfig`)
- → `clio/tasks/*` (all step functions)
- → `clio/plan_model` + `clio/plan_readiness` (cut/export pre-flight)
- → `clio/log` (`setup_logging`, `timed`)
- → `clio/utils` (`discover_ffmpeg_bin`, `safe_basename`)

## 7. Design Notes

- **One config load per invocation** — CLI commands resolve project/output before loading, so config,
  output, lock, and tasks always agree.
- **`--force` is a trait toggle, not a flag pass-through** — it sets `config.analyze.skip_existing =
  False` globally so every step skips the `skip_existing` gate.
- **Retry surface** — cut/export re-check plan readiness before acting, so a stale plan tree cannot
  silently export.

## 8. Risks / Maintenance Notes

1. `clio/main.py` is a 600+ line single module mixing parser construction, argument plumbing, and
   dispatch. Splitting per-command dispatch into `clio/tasks/`-adjacent runners would reduce
   duplicate `Plan.from_dict`/readiness blocks shared by `cut` and `export`.
2. Hidden `-i/--input` being overloaded (directory vs single-file) across commands is subtle:
   `compress/analyze/scripts` null it out when it points at a file (`main.py:398-403`), while
   `refine` keeps it. Easy to trip over when adding a new command.
3. `desktop` intentionally bypasses the common logging/hooks setup — new global cleanup must not
   assume it always ran.
4. `run_pipeline_steps` only inspects int results; tasks returning lists of failures rely on their
   own error counting. The `summary.failed` field is always `0` today (cosmetic dead field).
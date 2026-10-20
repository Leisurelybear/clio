# Maintenance Instructions for Future AI Agents

> Use this after the normal takeover checklist when planning non-trivial code changes.

## 1. Treat configuration as a two-layer contract

`config.yaml` is global and `project.yaml` is project-local. Relative project paths must resolve from the project directory, not from the repo root or global config path. The loader also auto-upgrades user config files by injecting missing dataclass defaults, so config changes must update:

- the dataclass model
- the parser/loader path
- `config.example.yaml` or `docs/project.example.yaml`
- tests that prove merge, path resolution, and auto-upgrade behavior

Do not store API keys in YAML examples. UI provider/model editing should continue to use the existing config/env endpoints rather than adding provider-specific backend APIs.

## 2. Preserve the pipeline step contract end to end

`run_pipeline_steps()` is the shared contract between CLI and UI. When adding or changing a step, keep these inputs coherent through route handlers, preview, orchestration, and task modules:

- `files`
- `overwrite`
- `cancel_event`
- `context_override`
- `task_prompts`
- `tracker`

Do not assume a step runs only from the full pipeline. Rerun, selected-file runs, preview summaries, and cancellation all need to remain consistent.

## 3. Reuse existing artifact matching helpers

The project relies heavily on generated filenames, sidecar JSON files, split segments, compressed stems, and index prefixes. Do not reimplement matching logic inline unless the existing helper cannot express the case.

Prefer existing helpers in `clio.tasks._helpers`, `clio.ui.services.file_service`, `clio.vmeta`, and `clio.ui.routes.videos`. Always sort filesystem iteration before assigning indices or comparing generated artifacts, because Windows and Linux directory order differ.

## 4. Be careful with AI provider lifecycle and retry semantics

Provider objects are cached in `clio.ai.factory`; tests rely on clearing that cache. `ProviderConfig.retry_attempts` means extra retries, while `with_retry(attempts=...)` means total calls. Convert using `retry_attempts + 1`.

For Gemini video analysis, keep these invariants:

- upload once
- wait until the File API object is active
- pass cancellation into polling
- call `generate_content`
- delete the uploaded file in `finally`

Rate limiting should sleep outside shared locks when concurrency is involved.

## 5. Keep tests behavioral, not implementation copies

The useful Python tests call production entry points and assert observable results. Avoid tests that duplicate production loops or only prove "does not raise" unless no observable state exists.

Route-dispatch tests are allowed to be mock-heavy, but handler behavior belongs in handler/service tests. Frontend tests require Node 22.12 or newer; Vite 8 also accepts Node 20.19+, but the project standardizes on the Node 22 LTS line.

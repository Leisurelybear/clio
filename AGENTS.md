# AGENTS.md - AI Maintenance Manual & Project Memory

> Quick reference for AI assistants taking over maintenance.
> User preference: Chinese for conversation, English for commit messages and this document.

## 1. Project in One Sentence

An AI preprocessing pipeline: raw travel vlog footage -> ffmpeg compression -> Gemini reviews video + DeepSeek writes script -> JianYing (CapCut) manual editing.

## 2. Tech Stack

- Python 3.11+ (PEP 604 `X | None`, dataclass)
- ffmpeg / ffprobe (video processing; GoPro 4K -> 640p 5MB compressed)
- google-genai (Gemini 2.5 Flash video File API)
- httpx (DeepSeek / OpenAI compatible calls)
- PyYAML (config parsing; split into `config.yaml` global + `project.yaml` per-project)
- pytest (unit tests, auto-run in CI; 1200+ test cases)

Dependencies in `requirements.txt`; `setup.ps1`/`setup.sh` creates venv + installs ffmpeg + copies `.env` in one click.

## 3. Directory Structure (Simplified)

```text
vlog-video-analysis/
├── main.py                    CLI entry
├── clio/
│   ├── config/                AppConfig + load_config (config package)
│   ├── shutdown.py            beforeStop hook
│   ├── pipeline.py            High-level pipeline orchestration
│   ├── analyze.py             AI interaction functions
│   ├── compress.py            ffmpeg wrapper
│   ├── prompts.py             All prompt templates
│   ├── transcribe.py          Whisper ASR core
│   ├── whisper_cli.py         Whisper CLI
│   ├── utils.py               ffmpeg discovery, file IO, extract_json
│   ├── log.py                 Logging (hourly rotating, TeeWriter)
│   ├── cut.py                 Segment cutting (ffmpeg wrapper)
│   ├── plan_model.py          Plan/PlanSegment domain + save validation (R-026)
│   ├── plan_readiness.py      Export/cut readiness (error/warning tiers)
│   ├── analyze_windows.py     Long-clip logical windows (slice/merge; no physical split)
│   ├── identity.py            MediaIdentity + is_legacy_split_* gate
│   ├── vmeta.py               .vmeta/.vindex sidecar metadata
│   ├── progress.py            Progress tracker (used by UI + CLI)
│   ├── tasks/                 Pipeline steps (per-step modules)
│   ├── desktop/               pywebview host + single-instance coordination
│   │   ├── app.py             Window host, close policy, focus callback (R-039)
│   │   ├── server_host.py     Non-blocking localhost server lifecycle
│   │   ├── single_instance.py clio.lock + focus probe + web-8765 detection (R-039)
│   │   └── state.py           desktop-state.json (last_dir persistence)
│   ├── ui/                    Web UI (stdlib http.server)
│   │   ├── server.py          HTTP server
│   │   ├── routes/            Route handlers (split into focused modules)
│   │   └── static/            Frontend (no build step, ES modules)
│   │       └── src/           ES modules: editor-plan.js, plan-edit.js,
│   │                          sidebar.js, sidebar-data.js, sidebar-video-filter.js,
│   │                          runner.js, editor-config.js, ...
│   └── ai/                    AI providers
│       ├── base.py            TaskName enum, Provider Protocol
│       ├── factory.py         Provider lookup by name
│       ├── gemini.py          Gemini multimodal
│       └── openai_compat.py   DeepSeek / OpenAI / Tongyi / Moonshot
├── templates/                  vlog_template.md, trip_context.md
├── config.example.yaml / .env.example
├── requirements.txt / requirements-locked.txt
├── .github/workflows/test.yml
└── clio/tests/                pytest unit tests (1200+ cases)
```

> See `docs/superpowers/agents/directory-tree.md` for full tree with file-level annotations and test coverage details.

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
  - Task binding (Project tab): dropdowns filtered by capability (`gemini` -> video tasks)
  - Collapse state remembered per session (`_collapsedCards` Map)
- Task binding mutations write to `project.yaml` via existing `PUT /api/config/project`
- Default providers (`gemini`, `openai`, `deepseek`) cannot be deleted
- Video tasks (`video_analyze`) only show gemini-type providers in dropdown

## 5. User Preferences

- Language: Chinese for conversation, English for commits/docs/AGENTS.md
- Commit granularity: one feature per commit, do not batch
- History rewriting: force-push accepted
- No API keys / local paths in config files
- Add reasonable tests for new feature modules and behavior changes. Pure docs/config-only changes may skip tests with explicit verification notes.
- Push must be explicitly confirmed. Local commits fine, `git push` requires user approval.

## 6. AI Transfer Protocol

Upon taking over, the AI should:

1. `git log --oneline -10` - recent changes
2. `git status` - uncommitted changes
3. Read `config.example.yaml` + `docs/project.example.yaml` - config structure
4. Read `templates/trip_context.md` - current trip background
5. Read `docs/superpowers/agents/gotchas.md` - known pitfalls (only if modifying affected modules)
6. Read `CHANGELOG.md` - project history (only if needed)
7. Ask the user what they want to do

For new features: discuss plan first -> user confirms -> implement -> one commit -> confirm before push.

## 7. Quick Reference

### Running Tests

```bash
python -m pytest clio/tests/ -v
python -m pytest clio/tests/test_utils.py -v
npm test                # frontend Vitest (requires Node 18+)
```

GitHub Actions runs pytest on Python 3.11/3.12 (Ubuntu + Windows); Vitest in CI.

### Code Formatting

```bash
ruff format clio main.py
ruff check clio main.py
```

Pre-commit hook auto-runs ruff on staged `.py` files (`.githooks/pre-commit`).

CI also gates a mypy subset (Phase 4d). Reproduce locally with:

```bash
mypy --check-untyped-defs --show-error-codes
```

The gate scope lives in `[tool.mypy] files` in pyproject.toml (progressive, per-module; only add modules that report zero issues).

### Verification Flow

```bash
python main.py check
python main.py analyze --force
python main.py analyze
python main.py refine
python main.py serve --no-browser
```

### Dependency Locking

`requirements.txt` (loose) for daily dev; `requirements-locked.txt` (pinned) for CI.

## 8. On-Demand Loading Index

| If you need to... | Load this |
|---|---|
| Understand the project quickly | AGENTS.md (already loaded) |
| See project history and recent changes | `CHANGELOG.md` |
| Know known pitfalls and traps | `docs/superpowers/agents/gotchas.md` |
| Plan non-trivial maintenance safely | `docs/superpowers/agents/maintenance-instructions.md` |
| Check active refactoring items | `docs/superpowers/agents/optimization-plan.md` |
| Evaluate repeatable workflows for skill extraction | `docs/superpowers/agents/skill-candidates.md` |
| See full directory tree with annotations | `docs/superpowers/agents/directory-tree.md` |
| Understand the model registry (provider list + task binding) | `AGENTS.md section 4.7` |
| Understand R-017 implementation details | `docs/superpowers/plans/2026-07-02-model-registry-plan.md` |
| Add a new AI provider | Skill: `adding-ai-provider` |
| Add a new AI task | Skill: `adding-new-task` |
| Add a new CLI subcommand | Skill: `adding-cli-subcommand` |
| Change config schema or config UI | Skill: `vlog-config-split-changes` |
| Fix original/compressed/split matching | Skill: `vlog-artifact-identity-fixes` |
| Work through review findings | Skill: `vlog-review-iteration` |

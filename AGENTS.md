# AGENTS.md - AI Maintenance Manual & Project Memory

> Conversation in Chinese; commits, maintained docs, and this file use English.
> Read this file first, then load only the detailed references needed for the task.

## 1. Project and Stack

An AI preprocessing pipeline for travel vlogs: raw footage -> ffmpeg compression -> Gemini video
analysis -> DeepSeek text planning/voiceover -> JianYing (CapCut) manual editing.

- Python 3.11+, dataclasses, PEP 604 unions
- `google-genai`, `httpx`, PyYAML, pytest
- ffmpeg/ffprobe for compression, cutting, probing, and audio extraction
- stdlib Web UI with ES modules and Vitest; no frontend build step

## 2. Directory Map

| Area | Primary entry points |
| --- | --- |
| CLI / pipeline | `main.py`, `clio/pipeline.py`, `clio/tasks/` |
| AI / ASR | `clio/ai/`, `clio/asr/`, `clio/analyze.py`, `clio/prompts.py` |
| Media | `clio/compress.py`, `clio/cut.py`, `clio/ffmpeg_install.py` |
| Planning / export | `clio/plan_input.py`, `clio/plan_model.py`, `clio/plan_readiness.py`, `clio/export/` |
| Same-day consistency | `clio/tasks/consistency.py`, `clio/ai/structured.py`, `clio/prompts.py` |
| Task runtime | `clio/task_center/`, `clio/progress.py`, `clio/shutdown.py` |
| Web backend | `clio/ui/server.py`, `handler_factory.py`, `route_table.py`, `handler_security.py`, `request_body.py`, `clio/ui/routes/`, `clio/ui/services/` |
| Web frontend | `clio/ui/static/src/`, `clio/ui/static/locales/` |
| Desktop | `clio/desktop/` |
| Tests | `clio/tests/`, `clio/ui/static/src/__tests__/` |
| Templates / config | `templates/`, `config.example.yaml`, `docs/project.example.yaml` |

Full annotated tree: `docs/superpowers/agents/directory-tree.md`.

## 3. Workflow Contract

1. Inspect `git status`, recent commits, `ROADMAP.md`, and the relevant design/plan.
2. For non-trivial work, confirm the approach before implementation.
3. Keep each independent feature/fix in its own small commit.
4. Update `ROADMAP.md` and the active review tracker as work changes state.
5. Verify with focused tests, then the relevant full suite.
6. Local commits are allowed. Never `git push` without explicit user approval.

Commit messages: Conventional Commits in English, for example
`fix(ui): reject null-origin writes`. Types: `feat`, `fix`, `refactor`, `docs`, `chore`.

## 4. Non-Negotiable Rules

### Configuration

- `config.yaml` is global; `project.yaml` is project-local. No schema overlap.
- Relative project paths resolve from the project directory.
- Never put API keys or local environment paths in committed examples.
- After config changes, update the model, loader, examples, README, UI copy/locales, and tests.

### AI and prompts

- Built-in prompts live in `clio/prompts.py`.
- Wrap user/trip context with `_wrap_with_context()`.
- AI JSON goes through `extract_json()`; validate and retry rather than silently accepting bad data.
- Fixed-output tasks pass `StructuredOutput` definitions from `clio/ai/structured.py`; refine tasks use JSON object mode and keep tolerant parsing as fallback.
- Staged planning runs outline → shot selection → review inside the single `vlog_plan` binding; persist only the final validated plan artifact.
- Provider retry semantics: `retry_attempts` means extra attempts, so total calls are
  `retry_attempts + 1`.
- Provider objects are cached in `clio.ai.factory`; tests must clear the cache when needed.

### Media identity and artifacts

- Reuse `MediaIdentity` and existing matching helpers; do not duplicate filename/stem matching.
- Sort filesystem iteration before assigning indices.
- Preserve `.vmeta` / `.vindex` compatibility and legacy read-only artifact paths.
- Plan, cut, preview, and export must agree on the same media identity and timeline offsets.
- Respect artifact overwrite markers and confirmation gates for user-edited plan/voiceover files.

### Web/UI safety

- Dynamic HTML uses the escaping `html` tagged template from `utils.js`.
- `unsafeHtml()` is only for trusted, code-owned markup; never for user/filesystem/AI/log data.
- Route changes must preserve auth, path containment, symlink rejection, and JSON body limits.
- SSE, polling, and live buffers must stay separate from historical data.
- Frontend copy goes through the i18n catalogs; keep zh-CN and en key sets identical.

### Tests

- Add behavioral tests for new feature modules and bug fixes.
- Pure documentation/config-only changes may skip tests if `git diff --check` is recorded.
- Prefer production entry points over tests that duplicate implementation loops.
- Vitest uses Node 22.12+.

### User preferences

- Chinese conversation; English commits and maintained docs.
- One feature per commit; do not batch unrelated fixes.
- History rewriting and force-push are accepted only when the user requests/approves them.
- Explicit push approval is required.

## 5. AI Transfer Protocol

1. Run `git log --oneline -10` and `git status`.
2. Read `ROADMAP.md`; open the relevant analysis/design/plan document.
3. Read `config.example.yaml` and `docs/project.example.yaml` for config work.
4. Read `templates/trip_context.md` for content/prompt work.
5. Read `docs/superpowers/agents/gotchas.md` for affected modules.
6. Continue existing work before creating parallel work; preserve unrelated untracked files.
7. Update documentation status in the same logical change when a task completes.

## 6. Verification

```powershell
python -m pytest clio/tests/ -v
npm run typecheck
npm test
ruff format clio main.py
ruff check clio main.py
mypy --check-untyped-defs --show-error-codes
python main.py check
```

Use focused tests for iteration, then the full relevant suite before committing. Always run
`git diff --check` for documentation-only changes.

## 7. Detailed Convention Index

The full contracts moved to
[`docs/superpowers/agents/project-conventions.md`](docs/superpowers/agents/project-conventions.md):

| Topic | Section |
| --- | --- |
| Docs naming, commits, workflow, code style | `4.0`-`4.3` |
| Config, prompts, refine modes | `4.4`-`4.6` |
| Model registry and task binding | `4.7` |
| Notification center | `4.8` |
| Historical logs | `4.9` |
| Verified ffmpeg installer | `4.10` |
| Plan preview media | `4.11` |
| Internationalization | `4.12` |
| HTTP access logs | `4.13` |
| Network file picker | `4.14` |
| Sidebar bulk selection | `4.15` |
| Sidebar video metadata | `4.16` |
| Artifact overwrite protection | `4.17` |
| Safe HTML rendering | `4.18` |
| Bounded plan input | `4.19` |
| Generated plan cut snapping | `4.20` |
| Same-day entity consistency | `4.21` |
| Staged vlog planning | `4.22` |

## 8. On-Demand References

| Need | Load |
| --- | --- |
| Known traps | `docs/superpowers/agents/gotchas.md` |
| Non-trivial maintenance | `docs/superpowers/agents/maintenance-instructions.md` |
| Active refactoring | `docs/superpowers/agents/optimization-plan.md` |
| Full file-level tree | `docs/superpowers/agents/directory-tree.md` |
| Project history | `CHANGELOG.md` |
| Documentation rules | `docs/CONVENTIONS.md` |
| Current review state | `docs/analysis/2026-11-05-P0-P2-Issues.md` |

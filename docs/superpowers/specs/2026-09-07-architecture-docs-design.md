# Architecture Documentation Series — Design

> **Status:** Approved (2026-09-07)
> **Audience:** Maintainers, AI agents, and new contributors onboarding to the Clio codebase.
> **Location:** `docs/architecture/` (stable reference — no date prefix, per `docs/CONVENTIONS.md`).

## Goal

Produce a point-in-time inventory of the existing Clio feature architecture as a series of
English-language documents, one per subsystem, so that module responsibilities, entry points,
data flow, and cross-module dependencies are discoverable without reading every source file.
Each document is committed independently as a small, self-contained unit.

## Scope & Non-Goals

**In scope**
- Static inventory ("map the logic") of the current implementation, with code references
  (`file:line`).
- A risk/variance notes section per module, recording issues noticed during review.

**Out of scope**
- No code changes, refactors, or bug fixes. Findings are recorded in the documents.
- No new design proposals beyond documenting what exists.

## Document Series

| # | File | Coverage |
|---|------|----------|
| 00 | `overview.md` | Top-level architecture: end-to-end data flow, process/deployment model, module dependency map, support matrix |
| 01 | `cli-pipeline.md` | `main.py`/`clio/main.py` CLI entry, argparse dispatch, `pipeline.py` orchestration, 7-step pipeline |
| 02 | `config-system.md` | Global/Project config split, `AppConfig` merged view, V1→V2 migration, validation, auto-upgrade |
| 03 | `ai-providers.md` | `TaskName`/provider abstraction, Gemini + OpenAI-compat providers, provider cache/TTL, prompts, token usage |
| 04 | `asr-transcription.md` | ASR provider registry (local whisper / Aliyun), engine switching, whisper CLI, transcript output contract |
| 05 | `media-identity.md` | `.vmeta`/`.vindex` sidecars, `identity.py`, `index.py`, original/compressed/split matching |
| 06 | `media-processing.md` | compress, cut, label, waveform, cover, reindex, verify media tasks |
| 07 | `analysis-plan-domain.md` | analyze windows, scripts, plan/refine tasks, `plan_model.py`, `plan_readiness.py` |
| 08 | `task-center.md` | TaskManager/StateMachine/Store/Executor/Reporter, SQLite schema, notifications, SSE |
| 09 | `http-server.md` | `server.py`/router, auth gates, CSRF/local-session guard, project resolution, file streaming |
| 10 | `ui-frontend.md` | ES-module frontend layers, state, Task Center/notification SSE consumers, viewer/plan preview |
| 11 | `desktop-shell.md` | pywebview host, single-instance lock, focus probe, background server host, native dialogs |
| 12 | `export.md` | JianYing `draft_content.json` generation, FORMAT_REGISTRY, material/track mapping |
| 13 | `observability.md` | hourly file logging, session log, progress tracker, processing state, token accounting, privacy redaction |

The series is ordered so each document can be read independently; cross-references point to
sibling documents by file name.

## Common Document Template

Each subsystem document follows the same skeleton:

1. **Responsibilities** — one-paragraph summary of what the subsystem owns.
2. **Module Map** — file list with 1-line responsibilities and key `file:line` anchors.
3. **Core Types & Entry Points** — dataclasses/enums/functions external callers rely on.
4. **Key Data Flows** — for the 1–3 dominant flows (e.g. run submission, transcription, export).
5. **Cross-Module Dependencies** — which sibling subsystems it depends on / is consumed by.
6. **Design Notes** — decisions that affect maintainers (cache keys, atomic writes, contracts).
7. **Risks / Maintenance Notes** — issues worth attention, recorded but not fixed.

## Division of Labor

- Series lives in `docs/architecture/`; each file is a single independent commit
  (`docs(architecture): <topic>`).
- The overview (00) is written last or in parallel after module docs are drafted, so its
  module map reflects reality; it is committed first to give readers an entry point, then
  updated if later modules reveal corrections.
- No test changes are required for documentation-only commits (per user preference), with
  explicit verification notes in each commit.
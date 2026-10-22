# I18n Completion Plan (R-025 phase 2)

Status: In progress (2026-10-22)

## Why

Phase 1 shipped the catalog/resolver layer and a complete backend/CLI migration, but the Web UI
never consumed it:

- Only `app.title` is referenced; the remaining 62 shared UI keys are dead weight.
- `index.html` carries a single `data-i18n` attribute.
- 55 JS modules, 1 imports `i18n.js` (`main.js`), and it never calls `t()`.
- `ui.language` only affects `<html lang>` plus the page title, and only after a full reload.
- Backend copy ignores `ui.language` entirely: `t()` reads `CLIO_LANG` only, so setting
  `ui.language: en` leaves CLI/doctor/dependency/task-center messages in Chinese.

## Goals

1. `ui.language` is the single source of truth for both backend and frontend copy.
2. Switching language in Settings applies immediately, without a reload.
3. All user-facing Web UI copy (shell, settings, notifications, warnings, sidebar, editor,
   task center, logs) resolves through the shared catalogs.
4. A machine-checkable inventory prevents silent regressions back to hard-coded copy.

## Non-Goals

- Do not translate AI prompts (`clio/prompts.py`) or user/AI-authored content.
- Do not translate content already persisted to disk (logs, transcripts, plan titles).
- Do not add a third locale in this pass; `zh-CN` and `en` only.
- Do not introduce ICU/pluralization; `{placeholder}` interpolation stays the only feature.

## Key Decisions

### D1: Locale precedence

`t()` resolves the effective locale as: explicit `locale=` argument > `CLIO_LANG` environment
variable > active locale set from `ui.language` > `DEFAULT_LOCALE` (`zh-CN`).

The environment variable stays above config so one-off runs (`CLIO_LANG=en python main.py check`)
keep working, and so existing tests that pin `CLIO_LANG` keep their meaning.

### D2: Active locale plumbing

`clio/i18n.py` gains `set_active_locale()` / `get_active_locale()`. It is set whenever a config is
resolved:

- `clio/main.py` after `load_config`
- `clio/doctor.py` inside `run_doctor`
- `clio/desktop/app.py` after loading config
- `clio/ui/services/config_cache.py` on every `get()` (hit and miss), which covers the Web UI
  including `PUT /api/config/global`

### D3: Chinese copy stays in code, catalogs add translations

Config field labels live in `editor-config.js` (`FIELD_LABELS`) and config tooltips in
`clio/config/descriptions.py`. Those stay the zh-CN source of truth; the catalogs carry the
translated values under `settings.field.*` and `settings.desc.*`. A Python test asserts `en.json`
covers every `descriptions.py` path, and a Vitest asserts `en.json` covers every `FIELD_LABELS`
path, so the two sides cannot drift silently.

### D4: Partial catalogs are allowed

A locale file only needs the keys it overrides. Lookup falls back `locale -> en -> zh-CN -> key`,
so an incomplete translation degrades to English rather than to a raw key.

### D5: Locale changes propagate by event

`setLocale()` dispatches `clio:locale-changed` when the locale actually changes. Modules that build
DOM from JS strings re-render on that event; `applyTranslations()` re-runs for static HTML.

## Milestones

Each row is one commit; focused tests must pass before the next row starts.

| # | Type | Scope |
|---|---|---|
| 1 | docs | This plan + ROADMAP status + remaining-string baseline |
| 2 | feat(i18n) | Active locale from config (Python) + `fallback` argument + tests |
| 3 | feat(ui) | `clio:locale-changed`, `hasKey()`, instant apply, settings re-render + Vitest |
| 4 | feat(ui) | Localize settings labels, tooltips, and select option labels |
| 5 | feat(ui) | Localize notification center, ffmpeg install, runtime warnings, toasts, empty states |
| 6 | feat(ui) | Localize task center, logs, waveform |
| 7 | feat(ui) | Localize sidebar, video menu, file browser, viewer, transcription/subtitle controls |
| 7b | fix(ui) | Re-render sidebar copy when the locale changes |
| 8a | feat(ui) | Bind the static `index.html` shell to `data-i18n` attributes |
| 8b | feat(ui) | Localize `runner.js` and `main.js` |
| 8c | feat(ui) | Localize `editor-config.js` prompts, providers and task bindings |
| 8c2 | feat(ui) | Localize `editor-config.js` token stats and Whisper model management |
| 8d | feat(ui) | Localize `editor-plan.js` and plan editing helpers |
| 8e | feat(ui) | Localize `editor-texts.js` and the remaining editor modules |
| 9 | feat(i18n) | Localize backend route/task user-facing messages |
| 10 | test(i18n) | Remaining-string inventory + parity guards (Python + Vitest) |
| 11 | docs | README language switch, AGENTS.md conventions, ROADMAP/CHANGELOG |

## Progress Log

| Date | Milestone | State |
|---|---|---|
| 2026-10-22 | 1 docs | Done |
| 2026-10-22 | 2 backend locale | Done |
| 2026-10-22 | 3 instant switching | Done |
| 2026-10-22 | 4 settings copy | Done |
| 2026-10-22 | 5 notifications / warnings / ffmpeg / task center | Done |
| 2026-10-22 | 6 logs / waveform | Done |
| 2026-10-22 | 7 sidebar / menus / viewer / transcription | Done |
| 2026-10-22 | 7b sidebar locale re-render | Done |
| 2026-10-22 | 8a `index.html` shell | Done |
| 2026-10-22 | 8b `runner.js` / `main.js` | Done |
| 2026-10-22 | 8c `editor-config.js` providers / prompts | Done |
| 2026-10-22 | 8c2, 8d-8e, 9-11 | Pending |

## Remaining-String Baseline (2026-10-22)

Chinese string literals per file before this pass (comments excluded):

| Area | Files | Literals |
|---|---|---|
| `clio/ui/static/src/*.js` | 35 | 811 |
| `clio/ui/static/index.html` | 1 | ~110 lines |
| `clio/**/*.py` (non-test, non-prompt) | 60+ | ~930 |

Largest JS offenders: `editor-config.js` (198), `ffmpeg-install.js` (52), `editor-plan.js` (52),
`video-menu.js` (51), `sidebar-data.js` (46), `editor-texts.js` (46), `main.js` (43).

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest clio/tests -q
npm test
ruff check clio main.py
ruff format --check clio main.py
```

Manual smoke: switch `界面语言` to `English` in Settings, save, and confirm the settings pane,
sidebar, notification center, and `<html lang>` all flip without a reload; then run
`python main.py check` and confirm it follows `ui.language`.

## Acceptance Criteria

- `ui.language` drives CLI, doctor, Web UI routes, and Web UI copy.
- Switching language applies immediately and persists across restarts.
- No user-facing Web UI module still hard-codes Chinese copy (guarded by the inventory test).
- Missing keys degrade to English, never to a raw key or a crash.


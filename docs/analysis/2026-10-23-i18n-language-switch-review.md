# Review — i18n Completion and Header Language Switch

- **Date:** 2026-10-23
- **Baseline:** `c532d71` (branch `main`, local commits only; not pushed)
- **Scope:** the R-025 i18n completion batch and the header language switcher
  (`52cbbf1` … `c532d71`), plus a regression pass over the 2026-10-21/22 features
  (Task Center SSE, notification center, ffmpeg installer, plan preview media, log history).
- **Preceding reviews:** [`2026-10-21-review-iteration-summary.md`](2026-10-21-review-iteration-summary.md),
  [`2026-10-22-coverage-audit.md`](2026-10-22-coverage-audit.md)

## 1. Verification Snapshot

| Check | Result |
|---|---|
| `python -m pytest clio/tests -q` | `2409 passed, 12 skipped` in 78.86s |
| `npm test` (Vitest) | `741 passed` across `73` files |
| `ruff check clio main.py` | clean |
| `ruff format --check clio main.py` | `249 files already formatted` |
| `mypy --check-untyped-defs --show-error-codes` | clean (39 configured files) |
| Catalog parity (independent check) | `clio/locales` 746/746 keys, `clio/ui/static/locales` 1198/1198 keys, zero one-sided keys |
| `t('key')` resolution (independent check) | 0 unresolved literal keys in `clio/**/*.py` and `clio/ui/static/src/**/*.js` |

No public HTTP contract, config schema, or frontend state shape changed in the reviewed batch.

## 2. Findings

### F1 — Localized button label used as a state machine (low, latent)

`clio/ui/static/src/ffmpeg-install.js:233` chooses between "re-probe" and "install" by comparing
the button's rendered label:

```js
if (startButton.textContent === t('ffmpeg.dialog.reprobe')) { /* re-probe branch */ }
```

This is the anti-pattern the i18n plan explicitly retired elsewhere (see the `refine_busy` code).
Two consequences:

- `ffmpeg-install.js` is the only copy-rendering module that does not subscribe to
  `onLocaleChange`. Its labels are frozen at open time while `t()` inside the handler is evaluated
  at click time, so a locale switch while the dialog is open makes the comparison fail and a
  re-probe click starts a real install.
- Any future decoration of that button (icon, whitespace, suffix) breaks the branch silently.

The same re-probe logic already exists on the dedicated `#ffi-reprobe` handler, so the branch is
duplicated. Suggested fix: keep an explicit `mode` variable (`'install' | 'reprobe'`) and switch on
it.

### F2 — Duplicate locale list (low)

`clio/ui/static/src/language-switcher.js:13` re-declares `SUPPORTED_LOCALES` even though
`clio/i18n.js` already defines and exports it. Adding a third locale would require editing both
files; importing from `i18n.js` keeps one source of truth.

### F3 — Process-wide locale mutated from the request path (low, informational)

`clio/ui/services/config_cache.py::_apply_locale` (and `routes/config_routes.py`) sets the
process-global `clio.i18n` active locale on every config read/write, and `clio/ui/server.py` runs a
`BoundedThreadingHTTPServer`. Concurrent requests therefore share one mutable locale. This is
benign today because `ui.language` lives only on `GlobalConfig` (`AppConfig.ui` delegates to the
global config), so every thread resolves the same value. If `ui` ever becomes project-scoped, this
becomes a real cross-request race and the locale must move into the request context.

### F4 — Legacy localized-message match still in the frontend (low)

`clio/ui/static/src/editor-refine.js:55` still falls back to
`e.message.includes('正在 AI 审阅')`. It is intentional (older servers do not send `code`) and is
allow-listed by the JS inventory guard, but the `code` field is authoritative; the string match
should be dropped once the compatibility window closes.

### F5 — Repository hygiene (minor)

Nine stray `.pytest-*` directories (`.pytest-full-final*`, `.pytest-review-*`, ~2000 subdirs) are
left over from earlier review runs at the repo root. Their contents are all git-ignored
(`config.yaml`, `videos.json`, `projects.json`, `project.yaml`), so `git status` stays clean, but
they are dead weight. Safe to delete manually.

## 3. Verified Non-Issues

- Both catalogs have byte-identical key sets per locale; the fallback chain
  (`locale -> en -> zh-CN -> key`) means a partial catalog degrades to English, never to a raw key.
- Every literal `t()` key in Python and JS resolves in both catalogs (independent scan).
- No `data-i18n` element in `index.html` wraps child markup, so `applyTranslations()` cannot destroy
  icons — the class of bug that commit `a1ddd46` fixed for the save button.
- `clio/tasks/migrate.py::is_already_new_structure()` checks every supported locale instead of a
  hard-coded Chinese substring.
- No `TODO` / `FIXME` / `HACK` markers remain in production code.
- The locale plumbing is wired at every entry point that resolves a config: `clio/main.py`,
  `clio/doctor.py`, `clio/desktop/app.py`, `clio/ui/services/config_cache.py`, and
  `clio/ui/routes/config_routes.py`.

## 4. Documentation Updates

- `docs/superpowers/agents/directory-tree.md`: rebuilt from the real tree. The previous version was
  missing `task_center/`, `desktop/`, `locales/`, `i18n.py`, `ffmpeg_install.py`, `plan_model.py`,
  `plan_preview.py`, `plan_readiness.py`, `analyze_windows.py`, `doctor.py`, `index.py`,
  `privacy.py`, `ratelimit.py`, `schema.py`, `whisper_cache.py`, `_constants.py`, `_str_enum.py`,
  most of `ui/static/src/**`, and several route modules. The Test Map now lists the i18n guards and
  the newer suites.
- `README.md` / `README.en.md`: the language section now names the header `中` / `EN` button as the
  quick entry point (it was only documented in `AGENTS.md`).
- `docs/cli-reference.md`: added an Environment Variables table covering `CLIO_LANG`,
  `CLIO_FFMPEG_DIR`, and `CLIO_ALLOW_DESKTOP_UNDER_PYTEST`; `CLIO_LANG` was previously undocumented.
- `CHANGELOG.md`: corrected the 2026-10-23 verification line (738/72 -> 741/73 Vitest) and the Web
  UI catalog key count (1196 -> 1198).

## 5. Suggested Follow-Ups

Ordered by value, not by size:

1. Replace the `ffmpeg-install.js` label comparison with an explicit mode flag (F1).
2. Import `SUPPORTED_LOCALES` in `language-switcher.js` from `i18n.js` (F2).
3. Add a short comment (or an assertion) documenting why the global locale is safe under the
   threaded server, and what would have to change if `ui` became project-scoped (F3).
4. Delete the stray `.pytest-*` directories and consider a `.gitignore` entry for `.pytest-*` so a
   future run cannot leave them behind (F5).
5. Retire the `正在 AI 审阅` fallback match once the compatibility window closes (F4).
6. Unrelated but still open from earlier reviews: R-043 frontend coverage for the large legacy
   modules (`editor-plan.js`, `viewer.js`, `main.js`, `runner.js`), and R-027c server-side log
   search (`?q=`).

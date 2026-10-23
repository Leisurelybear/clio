# Scan — Localization Regression Sweep (Core Web UI + Backend)

- **Date:** 2026-10-23
- **Baseline:** `c766c85` (branch `main`; local commits only, not pushed)
- **Trigger:** user report — "cannot switch videos, some tab switches are broken", suspected i18n fallout
- **Scope:** every user-facing surface that renders localized copy, plus every backend `t()` call site
- **Preceding review:** [`2026-10-23-i18n-language-switch-review.md`](2026-10-23-i18n-language-switch-review.md)

## 1. Result In One Line

One real regression found: **tab switching silently failed whenever the editor had unsaved edits**,
because the i18n `t()` helper was shadowed by the tab-loop callback parameter `t` in `main.js`.
It is fixed and covered by a new boot-level test. Every other scanned surface passed.

The reported "cannot switch videos" is **not** a code regression: in the user's project every
original-source entry reports `missing: true` because the NAS share is offline, so clicking a video
opens the relink dialog. See section 6.

## 2. The Regression (fixed)

### R1 — i18n `t()` shadowed by the tab callback parameter

`clio/ui/static/src/main.js` wired the editor tab strip like this:

```js
$$('.tab').forEach(t => t.onclick = () => {
  const toTab = t.dataset.tab;
  if (shouldConfirmDirtyTabSwitch({ dirty: state.dirty, fromTab: state.currentTab, toTab })) {
    if (!confirm(t('shell.tab_dirty_confirm'))) return;   // t is the DOM element, not the helper
```

Introduced by `b96df09` (*feat(ui): localize the run pane and project management copy*), which added
both `import { t }` and the `t('shell.tab_dirty_confirm')` call into a callback whose parameter was
already named `t`. Inside that arrow function the parameter shadows the module import, so the guard
threw `TypeError: t is not a function` on every click while `state.dirty` was true and the tab
silently refused to change. With no unsaved edits the guard is skipped, so switching worked — which
matches the user's "some tabs are broken" wording.

- **Fix:** rename the parameter to `tabButton` and add a why-comment. `clio/ui/static/src/main.js:502`.
- **Guard:** `clio/ui/static/src/__tests__/main-tab-switch.test.js` boots the real `index.html` +
  `main.js` under jsdom and asserts `state.currentTab` advances for both the clean and dirty cases.
  Verified it fails against the pre-fix source (`TypeError` + `expected 'texts' to be 'transcript'`)
  and passes after the fix.

## 3. Verification Snapshot

| Check | Command / method | Result |
|---|---|---|
| Python unit tests | `python -m pytest clio/tests -q` | `2402 passed, 12 skipped` |
| Frontend tests | `npm test` (Vitest) | `753 passed` across `74` files |
| Python literal `t()` keys resolve | regex scan vs merged catalog | `752` keys, `0` unresolved |
| JS literal `t()` keys resolve | regex scan vs UI catalog | `878` keys, `0` unresolved |
| Dynamic key families resolve | `47` concrete keys x `2` locales | `0` missing |
| Backend `t` shadowing | AST scope scan (comprehension-aware) | `0` hazards |
| Boot-level integration probe | temporary jsdom harness (removed) | `0` errors, no raw-key leaks |

No public HTTP contract, config schema, or frontend state shape changed.

## 4. Scanned Feature Inventory

Legend: **PASS** = verified working; **FIXED** = regression found and repaired this pass.

| # | Feature / surface | How verified | Result |
|---|---|---|---|
| 1 | Editor tab strip (texts / voiceover / transcript) | `main-tab-switch.test.js`, boot probe | FIXED (R1) |
| 2 | Sidebar video switching | boot probe, `sidebar-select-video.test.js` | PASS |
| 3 | Source switch (compressed / original) | boot probe, `video-selection.test.js` | PASS |
| 4 | Header language switch (zh-CN <-> en) | boot probe, `language-switcher.test.js` | PASS |
| 5 | Settings / config pane (global + project tabs, providers, task binding) | boot probe, `editor-config.test.js`, `config-schema.test.js` | PASS |
| 6 | Run pane (step cards, labels, hints, skip hints) | boot probe, `runner.test.js`, `runner-locale.test.js` | PASS |
| 7 | Task Center (status / kind labels, SSE lifecycle) | `task-center*.test.js`, `task-center-lifecycle.test.js` | PASS |
| 8 | Notification center (severity labels, inbox) | `notification-center.test.js` | PASS |
| 9 | Logs pane (level chips, live + history) | `logs-locale.test.js`, `logs-ui.test.js`, `logs-history.test.js` | PASS |
| 10 | Plan editor + preview media | `plan-locale.test.js`, `plan-edit.test.js`, `plan-preview-media.test.js` | PASS |
| 11 | Editor texts / voiceover / refine | `editor-locale.test.js`, `editor-texts-transcription.test.js` | PASS |
| 12 | File browser + relink / batch relink | `file-browser.test.js`, `sidebar-rerun-refresh.test.js` | PASS |
| 13 | ffmpeg install dialog | `ffmpeg-install.test.js`, `ffmpeg-deps-guard.test.js` | PASS |
| 14 | Toasts + runtime warnings | `toast.test.js`, `runtime-warnings.test.js` | PASS |
| 15 | Empty states | `empty-states.test.js` | PASS |
| 16 | Transcription status + subtitle settings | `transcription-status.test.js`, `subtitle-settings.test.js` | PASS |
| 17 | Video menu / selection / filters | `video-menu.test.js`, `sidebar-video-filter.test.js` | PASS |
| 18 | Waveform + timeline | `waveform.test.js`, `plan-waveform.test.js` | PASS |
| 19 | Tokens / API key pane | `tokens-locale.test.js` | PASS |
| 20 | Desktop notifications | `desktop-notify.test.js` | PASS |
| 21 | i18n catalog parity + `t()` resolution | `i18n-catalog.test.js`, `i18n-inventory.test.js`, independent scans | PASS |
| 22 | Backend `t()` shadowing | AST scope scan | PASS |
| 23 | Static `data-i18n` markup | `i18n-html.test.js` | PASS |

## 5. Key Insight — The Backend Merges Both Catalogs

`clio/i18n.py::_load_catalog` merges **two** files per locale, in this order:

```python
_CATALOG_RESOURCES = (
    "ui/static/locales/{locale}.json",   # loaded first
    "locales/{locale}.json",             # loaded second -> wins on conflict
)
```

So `t()` on the backend sees the union of the backend catalog and the Web UI catalog. Keys that
live only in the UI catalog (`task.status.*`, `task.kind.*`, `run.step.*`, `notifications.severity.*`,
`transcription.engine_local`, ...) are therefore **resolvable from Python too** — this is correct by
design, not a defect. A naive per-file parity check against `clio/locales` alone would wrongly flag
them; the scans in section 3 compare Python keys against the merged catalog.

Both catalogs are flat dot-notation maps (e.g. the literal key `"run.step.compress.label"`), so any
scanner must check exact flat keys, not nested objects.

## 6. Investigated But Not A Defect

- **"Cannot switch videos".** The video-switch code path works (boot probe moved `currentVideo`
  between entries). In the user's project the original-source view returns every entry with
  `missing: true` because the NAS paths are unreachable, so a click opens the relink modal. This is
  data/environment, not a regression. It was written before the `/api/video` empty-response defect
  was found: see [`2026-10-23-ui-unreachable-concurrency-budget.md`](2026-10-23-ui-unreachable-concurrency-budget.md)
  - the endpoint also returned zero bytes whenever the shared worker pool was saturated, a second,
  independent reason a video click could appear to do nothing.
- **Dynamic-key "misses".** An early scan reported `run.step.*`, `logs.level.*`, and `task.kind.*`
  missing. That was a bug in the scanner: it probed the wrong key shape (`run.step.compress` instead
  of `run.step.compress.label`), the wrong case (`logs.level.DEBUG` vs `logs.level.debug`), and the
  wrong family (`task.kind.*` vs the real `task.status.*` / `task.kind.*` value sets). Re-checking the
  exact runtime values extracted from the source (`STATUS_KEYS`, `KIND_KEYS`, `SEVERITIES`,
  `_LOG_LEVELS`, the step keys) gives `0` missing out of `47` keys x `2` locales.
- **Python `t` shadowing.** Several backend modules import the i18n `t` and also bind a local `t`
  (`analyze.py`, `gpmf.py`, `tasks/waveform.py`, `ui/server.py`, `ui/services/project_service.py`).
  A comprehension-aware AST scan reports `0` hazards: every rebind is either in a different function
  or inside a comprehension/generator scope, which does not leak in Python 3. (The first, naive AST
  pass flagged `analyze.py:559`, but that `for t in timeline` is a generator-expression target.)
- **`editor.js:45` latent pattern.** `$$('.tab').forEach(t => ...)` still names its parameter `t`, but
  that module imports only `onLocaleChange`, not `t`, and the callback never calls `t()`, so it is
  harmless today. Flagged as a latent trap in section 8.
- **ffmpeg install dialog.** The earlier review's F1 fix is present: `ffmpeg-install.js` keeps an
  explicit `startMode` (`'install' | 'reprobe'`) instead of comparing the localized button label.

## 7. Locale Re-render Subscription Audit

Of the `34` front-end modules that call `t()`, `10` subscribe to `onLocaleChange` and re-render their
JS-built DOM on a language switch: `editor-config`, `editor-plan`, `file-browser`,
`language-switcher`, `notification-center`, `runner`, `runtime-warnings`, `sidebar-data`,
`task-center`, `viewer`. The other `24` either build their DOM on demand (per open/refresh) or are
re-rendered by a parent, which the boot probe confirmed shows correct localized copy after a switch.

## 8. Latent Risks / Follow-ups

1. **Rename the shadowing parameter in `editor.js:45`** (cosmetic today, but the same trap that
   produced R1). A one-line rename removes the class of bug from the file.
2. **Consider a lint rule** (`no-shadow` on the imported `t`, or a naming convention that reserves
   `t` for the i18n helper) so a future edit cannot reintroduce R1.
3. **Keep a permanent boot-level smoke test.** The temporary integration probe caught nothing beyond
   R1, but a lightweight, permanent version would guard tab/video/source/language switching together.
4. Retire the `正在 AI 审阅` fallback match in `editor-refine.js` once the compatibility window closes
   (carried over from the preceding review, F4).

## 9. Artifacts

- **Added:** `clio/ui/static/src/__tests__/main-tab-switch.test.js` (permanent regression guard).
- **Changed:** `clio/ui/static/src/main.js` (parameter rename + why-comment).
- **Temporary, removed after use:** `_scan_keys*.mjs`, `_scan_locale.mjs`, `._scan_dyn.mjs`,
  `._scan_shadow.py`, `clio/ui/static/src/__tests__/zz_core_probe.test.js`, `logs/_probe_result.txt`.
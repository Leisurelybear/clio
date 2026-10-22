# UT Coverage Audit and Hardening

Date: 2026-10-22

## Scope

Comprehensively exercise the features shipped in the 2026-10-21/22 batches (Task Center SSE,
notification center, ffmpeg installer, plan preview media, cut hardening), audit unit-test
coverage for gaps, fix what surfaced, and record the resulting metrics.

No public HTTP contract, config schema, or frontend state shape changed.

## Findings and Fixes

### Backend

- `TaskStore.notification_events()` was dead code: no caller anywhere in the tree after the
  notification inbox moved to `notification_snapshot()`. Removed it so the store surface matches
  its real consumers.
- Notification route validation (`limit`/`offset`/`unread`/`severity` parsing, stream cursor and
  `Last-Event-ID` handling, POST field validation) was only partially covered. Added direct route
  tests for every rejection branch.
- Plan/cut routes had no coverage for the managed-task executor (`_run_cut_task`), the legacy
  background-thread fallback, readiness parsing failures, or the concurrency guards. Added tests
  covering those paths.

### Frontend

- `task-center.js` module-level stream state (`_stream`, `_streamCursor`) leaks across tests in a
  single file. The new `task-center-actions.test.js` sequences must strictly increase past the
  accumulated cursor; the helper now advances by a large stride so a later test cannot be
  swallowed as a stale revision. This is a test-harness fix, not a production change.
- Added coverage for the desktop-notify decision table, the plan-preview media resolution edges,
  the `/api/video` URL builder, the notification center stream/reconnect/read actions, and the
  ffmpeg install dialog state machine (failure, cancel, re-detect, closed-before-resolve).

## Metrics

| Area | Before | After |
|------|--------|-------|
| Python tests | 2312 passed, 12 skipped | 2391 passed, 12 skipped |
| Python line coverage | 82.18% | 83.95% |
| Frontend tests | 606 passed / 60 files | 679 passed / 62 files |
| Frontend statements | 41.26% | 42.99% |
| Frontend lines | 42.44% | 44.30% |
| Frontend branches | 38.13% | 40.89% |

Recent-feature frontend modules (statements / lines):

| Module | Statements | Lines |
|--------|-----------|-------|
| `notification-center.js` | 97.5% | 99.2% |
| `ffmpeg-install.js` | 98.2% | 99.0% |
| `desktop-notify.js` | 100% | 100% |
| `plan-preview-media.js` | 100% | 100% |
| `video-url.js` | 100% | 100% |
| `task-center.js` | 99.2% | 100% |

## Known Remaining Gaps

These are intentionally left uncovered because they are defensive or unreachable in the current
control flow; they are documented rather than chased:

- `task-center.js` `subscribeTask` deliver guard (`!active || payload.task.id !== taskId`): the
  inner subscription callback already filters by task id, and `active` cannot flip before the
  snapshot resolves, so both operands are always false.
- `task-center.js` `subscribeTask` `if (!active) return () => {}` after the snapshot await: the
  stop function is only handed out after that await, so `active` cannot be false there.
- `ffmpeg-install.js` `attachTask` "replace existing subscription" branch: the start button is
  disabled while a task is running, so a second attach cannot be triggered from the UI.
- Large legacy modules (`editor-plan.js`, `viewer.js`, `main.js`, `runner.js`) remain the dominant
  uncovered surface; raising their coverage is tracked separately under R-043.

## Verification

```bash
python -m pytest clio/tests -q --cov=clio
npx vitest run --coverage
ruff format clio main.py && ruff check clio main.py
mypy --check-untyped-defs --show-error-codes
```

- Python: `2391 passed, 12 skipped`, coverage `83.95%`.
- Vitest: `679 passed` across `62` files; statements `42.99%`, lines `44.30%`, branches `40.89%`.
- Ruff clean; mypy clean across the configured 39-file scope.

## Boundaries

- No production behavior change beyond removing one dead store method.
- No new public HTTP fields or SSE wire-format changes.
- The frontend cursor-stride fix lives only in test code.

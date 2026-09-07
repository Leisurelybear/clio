# Clio — Task Center

> Series: `08` of `00-overview.md`. Covers `clio/task_center/` — the unified task lifecycle,
> SQLite history, executors, reporters, and the notification inbox it also owns.

## 1. Responsibilities

- Run pipeline steps as **managed tasks** (threads) with a strict status machine, cancel
  request/acks, progress + log events, and per-kind concurrency limits.
- Persist full task history (input summary, events, results) in a **SQLite store** with lazy
  retention cleanup so the UI can reconstruct state after server restart.
- Own the **notification inbox** (R-042): terminal statuses and warning/error events are
  registered transactionally; frontend toasts/status funnel through the same store.

## 2. Module Map

| File | Responsibility |
|---|---|
| `models.py` | Enums (`TaskStatus`:15, `TaskKind`:58, `TaskVisibility`:67, `TaskEventType`:72, `TaskEventLevel`:80, `NotificationSeverity`:86); `TaskRecord`:108, `TaskEvent`:195, `Notification`:223; `create_task`:288, `utc_now_iso`:93, `normalized_progress`:97, `sanitize_task_payload`:45 (strips prompts/secrets/paths for history retryability) |
| `schema.py` | SQLite schema `TASK_STORE_SCHEMA_VERSION=4` (`tasks`, `task_events`, `notifications`, `task_meta`); in-place v1→v4 upgrade; idempotent bootstrap with `INSERT OR IGNORE`; `TaskStoreSchemaError` |
| `store.py` | `TaskStore` — `create/get/list/active_tasks/snapshot/save_with_event` (transactional), `events`, `recent_events`, notifications CRUD + `mark_*_read`, `cleanup` (retention_days + max_terminal_tasks; unread notifications exempt). Optimistic `TaskUpdateConflictError` on stale writes; every row write happens inside `_connect` context |
| `state_machine.py` | `_ALLOWED_TRANSITIONS` (queued→running/failed/cancelled/interrupted; running→…; cancelling→terminal…; terminal = sink); `transition_task` stamps `started_at`/`finished_at`/`cancel_requested`/error fields |
| `manager.py` | `TaskManager` — `submit`:125 (register → create → sanitize → thread), `retry`:193, `request_cancel`:212 (`cancellable` guard + `CANCELLING`), `is_cancel_requested`:242, `update_progress`:249, `append_log`:292, `wait`:316, `recover_interrupted`:330 (restart: running/cancelling → `INTERRUPTED`), `shutdown`:364, periodic `_maybe_cleanup` |
| `executor.py` | `TaskContext`:15, `TaskHandler` Protocol:22, `TaskHandlerRegistration`:30 (cancellable, concurrency), `TaskExecutorRegistry`:55 (`register`/`require`/`kinds`), `_CachedEntry` |
| `reporter.py` | `TaskReporter`:15 (`cancel_requested`, `raise_if_cancelled`, `progress`, `log`); `TaskProgressReporter`:48 — wraps a legacy `ProgressTracker` so old step functions report into task events |

## 3. Status Machine

```text
        ┌──────────┬──────────────────────────────► SUCCEEDED ─────────┐
        │          │        ┌───────────────────────► FAILED ──────────┤
QUEUED ─┤          │        │                       └──────── (sink)    │
        │          ▼        │
        └──► RUNNING ──► CANCELLING ──► CANCELLED / INTERRUPTED (sink)
              │            ▲
              │            └─ request_cancel (only when registration.cancellable)
              └───── (handler honors cancel_event → FINISH_CANCELLED)
```

- Terminal = `{succeeded, failed, cancelled, interrupted}`. `INTERRUPTED` is the recovery status
  for tasks still `RUNNING`/`CANCELLING` in the DB at startup (`recover_interrupted`).
- `cancelling` is reachable only from `running` and only for `cancellable=True` kinds; the
  executor thread subscribes to `cancel_event` and cooperatively exits (non-cancellable kinds
  simply ignore the request).

## 4. Submit → Run Flow

```text
registry.register(kind, handler, cancellable, concurrency_key, max_concurrency)
submit(kind, title, input_data, private_input_data, …)
  ├─ require(kind) — TaskHandlerNotRegisteredError if unknown
  ├─ create_task → store.create (QUEUED)
  ├─ spawn daemon thread `_run(task_id, cancel_event)`
  └─ _run:
       transition QUEUED→RUNNING (store.save_with_event STATUS)
       build TaskContext(cancel_event, reporter, private_input_data)
       handler(context)                    ← payload reloaded per task
       └─ outcome: SUCCEEDED{result} / FAILED{error} /
                   CANCELLED (handler called reporter.cancelled / cancel_event)
       finally: semaphore release, schedule cleanup, _notify()
```

- **Concurrency policy**: per-kind `BoundedSemaphore(max_concurrency)` keyed by
  `concurrency_key`; `reject_if_active: True` raises `TaskAlreadyRunningError` (used for e.g.
  `WHISPER_INSTALL`); otherwise extra requests **queue** on the thread + semaphore.
- **Privacy**: `input_data` is sanitized (`_PRIVATE_TASK_KEYS`) before persistence so history
  stays retryable without persisting prompts/API keys/paths; the live `context` receives the
  full `private_input_data` (in-memory + thread only).
- **Reporter bridge**: `TaskProgressReporter.update` maps `ProgressTracker` legacy line
  "n/total · message" onto `update_progress` + `append_log`, so steps written for the CLI
  (progress prints) report into task events unchanged.

## 5. Store & History

- One SQLite file (`.clio/tasks.db` — see `13-observability.md`); `busy_timeout_ms=5000`,
  row update guarded by `updated_at` (optimistic lock → `TaskUpdateConflictError`).
- `save_with_event` writes task + event **in one transaction**; events carry
  `type {created,status,progress,log,cancel_requested}` and `level {info,warning,error}`.
- Notifications: `older unread are never deleted`; `_cleanup_notifications` trims only read
  rows by `retention_days` + `max_notifications` (R-042).
- Recovery bootstraps the `notification_revision` from `MAX(seq)` so the SSE tail cursor is
  stable across restarts.

## 6. Executors & Registered Kinds

| Kind | Handler |
|---|---|
| `PIPELINE` | full 10-step backfill/changed-source pipeline (CLI `--backfill`) |
| `RERUN` | re-run a prior step with changed settings |
| `CUT_EXPORT` | cut + export combined run |
| `EXPORT` | JianYing export (see `12-export.md`) |
| `WHISPER_INSTALL` | whisper venv bootstrap; cancellable, `reject_if_active` |
| `WAVEFORM` | lazy peaks generation (see `06-media-processing.md`) |

Frontend run-panel buttons map to `submit`, `request_cancel`, `wait`, and the task list reads
`store.snapshot(query)` + SSE event tail.

## 7. Cross-Module Dependencies

- → `clio/tasks/` step runners (registered as thread handlers)
- → `clio/progress.py` (legacy tracker bridged by `TaskProgressReporter`)
- → `clio/ui/routes/` (REST + SSE consumers; `POST /api/tasks`, `/api/notifications`)
- → `clio/desktop` (single-instance guard reuses task store paths)
- → `clio/config` (`.clio/` project dir resolution)

## 8. Risks / Maintenance Notes

1. `private_input_data` intentionally bypasses `sanitize_task_payload`; a future handler that
   logs `context.input` could leak prompts/keys — keep the sanitization split documented.
2. `INTERRUPTED` is *not* a user action status; a restart mid-run silently replaces `RUNNING`
   with `INTERRUPTED`, so the UI must treat it as "the task did not finish" rather than "failure".
3. `retry` re-uses the original `input_data` but *not* `private_input_data` — re-running a task
   whose handler needs private context will fail unless the caller re-supplies it.
4. `cancellable=True` is enforced at `request_cancel` (throws `TaskNotCancellableError`) but the
   handler must still honor `cancel_event` cooperatively; a busy ffmpeg child process holds the
   thread until it returns (see `run_ffmpeg` cancel plumbing).
5. Cleanup runs on a timer (`cleanup_interval_sec`, default 1h) inside the manager thread; a
   long-lived server with *no active tasks* still prunes read notifications but never touches
   unread history by design.
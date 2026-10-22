# Task Center SSE Lifecycle Summary

Date: 2026-10-22

## Scope

Close the remaining endpoint-level lifecycle gap for Task Center SSE while reducing repeated
database work during event bursts. The wire format and frontend event handling are unchanged.

## Implementation

- Added `TaskStore.get_many()` to load task snapshots in one query, deduplicate repeated IDs,
  and chunk large requests at 500 IDs per query.
- Updated `handle_get_tasks_stream()` to load one task snapshot map per 200-event page
  instead of calling `TaskStore.get()` once per event.
- Preserved missing-task behavior: a deleted task still emits its event with `task: null`.
- Preserved event ordering and the existing cursor rules based on `after` and `Last-Event-ID`.

## Lifecycle Coverage

The new `clio/tests/test_http_sse_lifecycle.py` module starts a real
`BoundedThreadingHTTPServer` and covers:

- A late subscriber starts at the snapshot cursor and receives only newly created events.
- Reconnect with `Last-Event-ID` skips old events and resumes from newer ones.
- A terminal transition between the detail snapshot and stream subscription is replayed.
- Two independent streams observe the same cancellation event; a repeated cancel is idempotent.
- Restart recovery marks an active task `interrupted`, and the retry endpoint reconstructs and
  completes it from persisted input.

## Verification

- `python -m pytest clio/tests/test_http_sse_lifecycle.py clio/tests/test_routes_tasks.py clio/tests/test_task_center_store.py -q`
- Result: `34 passed` on Python 3.11.9.
- Full pytest: `2312 passed, 12 skipped`.
- Vitest: `606 passed` across `60` files.
- Ruff check/format clean; mypy clean across the configured 39-file scope.

## CI Cleanup

The full mypy gate surfaced a branch-local type inference conflict in the FFmpeg installer:
the zip path bound `src` as a non-optional stream while the tar path reassigned it to an
optional stream. Renaming those variables to `zip_src` / `tar_src` is behavior-neutral and
restores the CI gate without changing installation logic.

## Boundaries

- No notification schema or frontend state changes.
- No public HTTP fields changed; clients continue to consume `id` plus JSON `data` events.
- SSE remains a polling adapter over SQLite rather than introducing a separate broker.

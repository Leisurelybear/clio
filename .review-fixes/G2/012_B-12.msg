perf(task-center): stop querying SQLite under the SSE wake-up lock

wait_for_events and wait_for_notification_revision ran SQLite queries
while holding _change_condition, the lock every writer takes after commit
to notify. Each SSE client therefore serialized all writers. Writers now
bump a revision counter under the lock; waiters snapshot it, query outside
the lock and only wait while the revision is unchanged, so no commit is
missed.

Refs: B-12

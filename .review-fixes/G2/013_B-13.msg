fix(task-center): keep task finalization and recovery from crashing

_run called _finish_failed/_finish_cancelled without a guard: a busy
SQLite (busy_timeout exceeded) or a data error escaped the worker thread
and left the task RUNNING until restart. Terminal transitions now retry a
few times and log instead of raising, and cleanup errors are logged.
recover_interrupted listed full rows, so one corrupt task made TaskManager
construction fail; it now walks active IDs and skips unreadable rows.

Refs: B-13

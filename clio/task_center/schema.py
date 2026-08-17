from __future__ import annotations

import sqlite3

TASK_STORE_SCHEMA_VERSION = 4

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS task_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL,
    project_id TEXT,
    project_name TEXT,
    project_path TEXT,
    parent_id TEXT,
    retry_of TEXT,
    visibility TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    heartbeat_at TEXT,
    updated_at TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT '',
    current INTEGER NOT NULL DEFAULT 0 CHECK (current >= 0),
    total INTEGER NOT NULL DEFAULT 0 CHECK (total >= 0),
    progress_pct REAL,
    message TEXT NOT NULL DEFAULT '',
    cancellable INTEGER NOT NULL DEFAULT 0 CHECK (cancellable IN (0, 1)),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    error_code TEXT,
    error_message TEXT,
    input_data_json TEXT NOT NULL DEFAULT '{}',
    input_summary_json TEXT NOT NULL DEFAULT '{}',
    result_summary_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS task_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    level TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_project_updated ON tasks(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_kind_updated ON tasks(kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_events_task_seq ON task_events(task_id, seq);

CREATE TABLE IF NOT EXISTS notifications (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    source_type TEXT NOT NULL DEFAULT '',
    source_id TEXT,
    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL,
    project_id TEXT,
    project_name TEXT,
    link TEXT,
    dedupe_key TEXT UNIQUE,
    data_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(read_at, created_at DESC, seq DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_project ON notifications(project_id, created_at DESC, seq DESC);
"""


def initialize_schema(connection: sqlite3.Connection) -> None:
    # ``CREATE TABLE IF NOT EXISTS`` does not alter an existing task table.
    # Inspect the metadata before creating indexes so a v1 database can be
    # upgraded without trying to index the not-yet-present column.
    meta_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_meta'"
    ).fetchone()
    stored_version: int | None = None
    if meta_exists is not None:
        version_row = connection.execute("SELECT value FROM task_meta WHERE key = 'schema_version'").fetchone()
        if version_row is not None:
            try:
                stored_version = int(version_row[0])
            except (TypeError, ValueError) as e:
                raise TaskStoreSchemaError(f"invalid task store schema version: {version_row[0]!r}") from e
            if stored_version not in (1, 2, 3, TASK_STORE_SCHEMA_VERSION):
                raise TaskStoreSchemaError(
                    f"unsupported task store schema version: {stored_version} (expected {TASK_STORE_SCHEMA_VERSION})"
                )

    tasks_exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'").fetchone()
    if stored_version in (None, 1) and tasks_exists is not None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()}
        if "updated_at" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN updated_at TEXT")
            connection.execute(
                "UPDATE tasks SET updated_at = COALESCE(heartbeat_at, finished_at, started_at, created_at) "
                "WHERE updated_at IS NULL"
            )

    connection.executescript(_SCHEMA_SQL)
    # Multiple server threads/processes can open the same new database at
    # startup.  INSERT OR IGNORE makes the bootstrap idempotent instead of
    # allowing a race between SELECT and INSERT to abort initialization.
    connection.execute(
        "INSERT OR IGNORE INTO task_meta(key, value) VALUES ('schema_version', ?)",
        (str(TASK_STORE_SCHEMA_VERSION),),
    )
    connection.execute(
        "INSERT OR IGNORE INTO task_meta(key, value) "
        "SELECT 'notification_revision', CAST(COALESCE(MAX(seq), 0) AS TEXT) FROM notifications"
    )
    if stored_version in (1, 2, 3):
        connection.execute(
            "UPDATE task_meta SET value = ? WHERE key = 'schema_version'",
            (str(TASK_STORE_SCHEMA_VERSION),),
        )
    row = connection.execute("SELECT value FROM task_meta WHERE key = 'schema_version'").fetchone()
    try:
        version = int(row[0])
    except (TypeError, ValueError) as e:
        raise TaskStoreSchemaError(f"invalid task store schema version: {row[0]!r}") from e
    if version != TASK_STORE_SCHEMA_VERSION:
        raise TaskStoreSchemaError(
            f"unsupported task store schema version: {version} (expected {TASK_STORE_SCHEMA_VERSION})"
        )


class TaskStoreSchemaError(RuntimeError):
    pass

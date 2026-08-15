from __future__ import annotations

import sqlite3

TASK_STORE_SCHEMA_VERSION = 1

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
"""


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(_SCHEMA_SQL)
    row = connection.execute("SELECT value FROM task_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO task_meta(key, value) VALUES ('schema_version', ?)",
            (str(TASK_STORE_SCHEMA_VERSION),),
        )
        return
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

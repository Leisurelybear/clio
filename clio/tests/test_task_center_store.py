from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

import pytest

from clio.task_center.models import (
    TaskEvent,
    TaskEventType,
    TaskKind,
    TaskStatus,
    TaskVisibility,
    create_task,
)
from clio.task_center.schema import TaskStoreSchemaError
from clio.task_center.state_machine import transition_task
from clio.task_center.store import (
    TaskAlreadyExistsError,
    TaskQuery,
    TaskStore,
    TaskStoreDataError,
    TaskUpdateConflictError,
)


def _event(task_id: str, message: str, *, created_at: str = "2026-08-16T00:00:01.000Z") -> TaskEvent:
    return TaskEvent(
        task_id=task_id,
        type=TaskEventType.LOG,
        created_at=created_at,
        message=message,
    )


def test_create_and_reopen_preserves_private_task_data(tmp_path):
    path = tmp_path / "task-center.sqlite3"
    store = TaskStore(path)
    task = create_task(
        TaskKind.PIPELINE,
        "处理素材",
        task_id="task-1",
        project_id="project-1",
        project_path="G:/private/project",
        input_data={"steps": ["compress", "analyze"]},
        input_summary={"step_count": 2},
    )
    store.create(task)

    loaded = TaskStore(path).require(task.id)

    assert loaded == task
    assert loaded.project_path == "G:/private/project"
    assert loaded.input_data["steps"] == ["compress", "analyze"]


def test_create_appends_created_event(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = create_task(TaskKind.RERUN, "重新分析", task_id="task-1")

    store.create(task)

    events = store.events(task_id=task.id)
    assert len(events) == 1
    assert events[0].seq == 1
    assert events[0].type is TaskEventType.CREATED
    assert events[0].data == {"kind": "rerun", "status": "queued"}


def test_duplicate_task_id_is_rejected(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="same")
    store.create(task)

    with pytest.raises(TaskAlreadyExistsError, match="same"):
        store.create(task)


def test_save_with_event_is_atomic_and_checks_expected_status(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    queued = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1")
    store.create(queued)
    running = transition_task(queued, TaskStatus.RUNNING, at="2026-08-16T00:00:01.000Z")
    status_event = TaskEvent(
        task_id=queued.id,
        type=TaskEventType.STATUS,
        created_at="2026-08-16T00:00:01.000Z",
        data={"from": "queued", "to": "running"},
    )

    saved, event = store.save_with_event(running, status_event, expected_status=TaskStatus.QUEUED)

    assert saved.status is TaskStatus.RUNNING
    assert event.seq == 2
    assert store.require(queued.id).status is TaskStatus.RUNNING
    with pytest.raises(TaskUpdateConflictError, match="expected queued"):
        store.save_with_event(running, status_event, expected_status=TaskStatus.QUEUED)
    assert len(store.events(task_id=queued.id)) == 2


def test_list_filters_orders_and_paginates(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    tasks = [
        create_task(
            TaskKind.PIPELINE,
            "旧任务",
            task_id="a",
            created_at="2026-08-16T00:00:00.000Z",
            project_id="p1",
        ),
        create_task(
            TaskKind.RERUN,
            "新任务",
            task_id="b",
            created_at="2026-08-16T00:00:02.000Z",
            project_id="p1",
        ),
        create_task(
            TaskKind.WAVEFORM,
            "波形",
            task_id="c",
            created_at="2026-08-16T00:00:03.000Z",
            project_id="p2",
            visibility=TaskVisibility.BACKGROUND,
        ),
    ]
    for task in tasks:
        store.create(task)

    query = TaskQuery(project_id="p1", kinds=(TaskKind.PIPELINE, TaskKind.RERUN), limit=1)

    assert [task.id for task in store.list(query)] == ["b"]
    assert [task.id for task in store.list(replace(query, offset=1))] == ["a"]
    assert store.count(query) == 2
    assert [task.id for task in store.list(TaskQuery(visibility=TaskVisibility.BACKGROUND))] == ["c"]


def test_event_cursor_is_global_and_monotonic(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    first = create_task(TaskKind.PIPELINE, "一", task_id="a")
    second = create_task(TaskKind.RERUN, "二", task_id="b")
    store.create(first)
    store.create(second)
    store.append_event(_event(first.id, "a-log"))
    store.append_event(_event(second.id, "b-log"))

    tail = store.events(after_seq=2)

    assert [event.seq for event in tail] == [3, 4]
    assert [event.message for event in tail] == ["a-log", "b-log"]
    assert store.latest_event_seq() == 4


def test_deleting_task_cascades_events(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1")
    store.create(task)
    store.append_event(_event(task.id, "log"))

    assert store.delete(task.id) is True
    assert store.get(task.id) is None
    assert store.events(task_id=task.id) == []


def test_concurrent_event_writes_from_separate_connections(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1")
    store.create(task)

    def append(index: int) -> int:
        saved = TaskStore(path).append_event(_event(task.id, f"log-{index}"))
        return saved.seq or 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        seqs = list(pool.map(append, range(40)))

    assert len(set(seqs)) == 40
    assert len(store.events(task_id=task.id)) == 41


def test_cleanup_removes_old_and_excess_terminal_tasks_only(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    active = create_task(TaskKind.PIPELINE, "运行中", task_id="active")
    store.create(active)
    for task_id, finished_at in [
        ("old", "2026-06-01T00:00:00.000Z"),
        ("newer", "2026-08-15T00:00:00.000Z"),
        ("newest", "2026-08-16T00:00:00.000Z"),
    ]:
        task = create_task(TaskKind.RERUN, task_id, task_id=task_id, created_at=finished_at)
        store.create(task)
        running = transition_task(task, TaskStatus.RUNNING, at=finished_at)
        done = transition_task(running, TaskStatus.SUCCEEDED, at=finished_at)
        store.save_with_event(done, _event(task_id, "done", created_at=finished_at), expected_status=TaskStatus.QUEUED)

    deleted = store.cleanup(
        retention_days=30,
        max_terminal_tasks=1,
        now=datetime.fromisoformat("2026-08-16T00:00:00+00:00"),
    )

    assert deleted == 2
    assert store.get("active") is not None
    assert store.get("newest") is not None
    assert store.get("old") is None
    assert store.get("newer") is None


def test_cleanup_keeps_task_while_notification_is_retained(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    finished_at = "2026-06-01T00:00:00.000Z"
    task = create_task(TaskKind.PIPELINE, "旧任务", task_id="linked", created_at=finished_at)
    store.create(task)
    running = transition_task(task, TaskStatus.RUNNING, at=finished_at)
    done = transition_task(running, TaskStatus.SUCCEEDED, at=finished_at)
    store.save_with_event(
        done,
        TaskEvent(
            task_id=task.id,
            type=TaskEventType.STATUS,
            created_at=finished_at,
            message="任务完成",
            data={"from": "running", "to": "succeeded"},
        ),
        expected_status=TaskStatus.QUEUED,
    )

    now = datetime.fromisoformat("2026-08-16T00:00:00+00:00")
    assert store.cleanup(retention_days=30, max_terminal_tasks=10, now=now) == 0
    assert store.get(task.id) is not None
    notification = store.list_notifications()[0]
    assert notification.task_id == task.id

    store.mark_notification_read(notification.id)
    assert store.cleanup(retention_days=30, max_terminal_tasks=10, now=now) == 1
    assert store.get(task.id) is None


def test_invalid_stored_json_fails_explicitly(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1")
    store.create(task)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE tasks SET input_data_json = '[]' WHERE id = ?", (task.id,))

    with pytest.raises(TaskStoreDataError, match="non-object input_data_json"):
        store.require(task.id)


def test_unknown_schema_version_fails_explicitly(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    TaskStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE task_meta SET value = '999' WHERE key = 'schema_version'")

    with pytest.raises(TaskStoreSchemaError, match="unsupported"):
        TaskStore(path)


def test_store_enables_wal_mode(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    TaskStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_store_migrates_v1_database_with_updated_at(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE task_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO task_meta(key, value) VALUES ('schema_version', '1');
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                title TEXT NOT NULL, project_id TEXT, project_name TEXT, project_path TEXT,
                parent_id TEXT, retry_of TEXT, visibility TEXT NOT NULL,
                created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                heartbeat_at TEXT, phase TEXT NOT NULL DEFAULT '', current INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0, progress_pct REAL, message TEXT NOT NULL DEFAULT '',
                cancellable INTEGER NOT NULL DEFAULT 0, cancel_requested INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, error_message TEXT, input_data_json TEXT NOT NULL DEFAULT '{}',
                input_summary_json TEXT NOT NULL DEFAULT '{}', result_summary_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE task_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                type TEXT NOT NULL, level TEXT NOT NULL, created_at TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}'
            );
            INSERT INTO tasks(id, kind, status, title, visibility, created_at, finished_at)
            VALUES ('legacy', 'pipeline', 'succeeded', '旧任务', 'foreground',
                    '2026-08-16T00:00:00.000Z', '2026-08-16T00:01:00.000Z');
            """
        )

    store = TaskStore(path)
    task = store.require("legacy")
    assert task.updated_at == task.finished_at
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM task_meta WHERE key = 'schema_version'").fetchone()[0] == "4"
        assert (
            connection.execute("SELECT value FROM task_meta WHERE key = 'notification_revision'").fetchone()[0] == "0"
        )


def test_store_migrates_v2_database_with_notification_table(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    TaskStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE notifications")
        connection.execute("UPDATE task_meta SET value = '2' WHERE key = 'schema_version'")

    TaskStore(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM task_meta WHERE key = 'schema_version'").fetchone()[0] == "4"
        assert (
            connection.execute("SELECT value FROM task_meta WHERE key = 'notification_revision'").fetchone()[0] == "0"
        )
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'notifications'"
            ).fetchone()
            is not None
        )


def test_store_repairs_missing_event_table_on_existing_database(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE task_events")

    # The store may remain alive while a stale/partial database is restored.
    # A changed SQLite schema identity must trigger the idempotent bootstrap.
    assert store.events() == []
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'task_events'"
            ).fetchone()
            is not None
        )


def test_snapshot_returns_matching_tasks_count_and_event_cursor(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.create(create_task(TaskKind.PIPELINE, "前台", task_id="foreground", project_id="p1"))
    store.create(
        create_task(
            TaskKind.WAVEFORM,
            "后台",
            task_id="background",
            project_id="p1",
            visibility=TaskVisibility.BACKGROUND,
        )
    )

    tasks, total, cursor = store.snapshot(TaskQuery(project_id="p1", visibility=TaskVisibility.FOREGROUND))

    assert [task.id for task in tasks] == ["foreground"]
    assert total == 1
    assert cursor == 2

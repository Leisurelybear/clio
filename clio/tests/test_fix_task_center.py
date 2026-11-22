"""Regression tests for task center fixes."""

from __future__ import annotations

from clio.task_center.models import (
    TaskEvent,
    TaskEventLevel,
    TaskEventType,
    TaskKind,
    TaskStatus,
    TaskVisibility,
    create_task,
)
from clio.task_center.state_machine import transition_task
from clio.task_center.store import TaskStore


def _move(store: TaskStore, task, target: TaskStatus, *, level: TaskEventLevel = TaskEventLevel.INFO, **kwargs):
    updated = transition_task(task, target, **kwargs)
    event = TaskEvent(
        task_id=task.id,
        type=TaskEventType.STATUS,
        created_at=updated.updated_at or updated.created_at,
        message=updated.message or target.value,
        level=level,
        data={"from": task.status.value, "to": target.value},
    )
    store.save_with_event(updated, event, expected_status=task.status)
    return updated


class TestBackgroundNotifications:
    def test_background_success_creates_no_notification(self, tmp_path):
        store = TaskStore(tmp_path / "tasks.sqlite3")
        task = store.create(
            create_task(TaskKind.WAVEFORM, "wave", task_id="bg-ok", visibility=TaskVisibility.BACKGROUND)
        )
        running = _move(store, task, TaskStatus.RUNNING)
        _move(store, running, TaskStatus.SUCCEEDED)

        assert store.list_notifications() == []

    def test_background_failure_still_notifies(self, tmp_path):
        store = TaskStore(tmp_path / "tasks.sqlite3")
        task = store.create(
            create_task(TaskKind.WAVEFORM, "wave", task_id="bg-fail", visibility=TaskVisibility.BACKGROUND)
        )
        running = _move(store, task, TaskStatus.RUNNING)
        _move(store, running, TaskStatus.FAILED, level=TaskEventLevel.ERROR, message="boom", error_message="boom")

        notifications = store.list_notifications()
        assert [n.task_id for n in notifications] == ["bg-fail"]

    def test_foreground_success_still_notifies(self, tmp_path):
        store = TaskStore(tmp_path / "tasks.sqlite3")
        task = store.create(create_task(TaskKind.PIPELINE, "pipe", task_id="fg-ok"))
        running = _move(store, task, TaskStatus.RUNNING)
        _move(store, running, TaskStatus.SUCCEEDED)

        assert [n.task_id for n in store.list_notifications()] == ["fg-ok"]


class TestConnectionSetup:
    def test_wal_is_set_once_per_database_identity(self, tmp_path, monkeypatch):
        import sqlite3

        store = TaskStore(tmp_path / "tasks.sqlite3")
        statements: list[str] = []
        real_connect = sqlite3.connect

        def tracing_connect(*args, **kwargs):
            connection = real_connect(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        monkeypatch.setattr("clio.task_center.store.sqlite3.connect", tracing_connect)
        store.create(create_task(TaskKind.PIPELINE, "pipe", task_id="t1"))
        store.require("t1")

        assert not [s for s in statements if "journal_mode" in s]
        with sqlite3.connect(tmp_path / "tasks.sqlite3") as connection:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

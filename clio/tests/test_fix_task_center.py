"""Regression tests for task center fixes."""

from __future__ import annotations

from datetime import datetime

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


class TestProgressDedupe:
    def test_identical_progress_updates_write_one_event(self, tmp_path):
        import threading

        from clio.task_center.manager import TaskManager

        manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
        entered = threading.Event()
        release = threading.Event()

        def handler(context):
            entered.set()
            release.wait(timeout=2)

        manager.register(TaskKind.PIPELINE, handler)
        task = manager.submit(TaskKind.PIPELINE, "pipe")
        assert entered.wait(timeout=2)

        for _ in range(20):
            manager.update_progress(task.id, phase="compress", current=1, total=3, message="a.mp4 42%")
        manager.update_progress(task.id, phase="compress", current=1, total=3, message="a.mp4 43%")
        release.set()
        manager.wait(task.id)

        progress = [e for e in manager.store.events(task_id=task.id) if e.type is TaskEventType.PROGRESS]
        assert [e.message for e in progress] == ["a.mp4 42%", "a.mp4 43%"]


def _finished_task_with_notification(store: TaskStore, task_id: str, finished_at: str):
    task = store.create(create_task(TaskKind.PIPELINE, task_id, task_id=task_id, created_at=finished_at))
    running = transition_task(task, TaskStatus.RUNNING, at=finished_at)
    done = transition_task(running, TaskStatus.SUCCEEDED, at=finished_at)
    store.save_with_event(
        done,
        TaskEvent(
            task_id=task_id,
            type=TaskEventType.STATUS,
            created_at=finished_at,
            message="done",
            data={"from": "queued", "to": "succeeded"},
        ),
        expected_status=TaskStatus.QUEUED,
    )


class TestUnreadNotificationRetention:
    def test_old_unread_notifications_and_their_tasks_expire(self, tmp_path):
        store = TaskStore(tmp_path / "tasks.sqlite3")
        _finished_task_with_notification(store, "ancient", "2026-01-01T00:00:00.000Z")
        _finished_task_with_notification(store, "recent", "2026-08-01T00:00:00.000Z")

        now = datetime.fromisoformat("2026-08-16T00:00:00+00:00")
        store.cleanup(retention_days=30, max_terminal_tasks=10, now=now)

        assert store.get("ancient") is None
        assert store.get("recent") is not None
        assert [n.task_id for n in store.list_notifications()] == ["recent"]

    def test_unread_notifications_are_capped(self, tmp_path):
        store = TaskStore(tmp_path / "tasks.sqlite3")
        for i in range(4):
            _finished_task_with_notification(store, f"t{i}", f"2026-08-1{i}T00:00:00.000Z")

        now = datetime.fromisoformat("2026-08-16T00:00:00+00:00")
        store.cleanup(retention_days=30, max_terminal_tasks=2, now=now)

        assert len(store.list_notifications()) == 2
        assert store.count() == 2


class TestWaitForEventsLocking:
    def test_waiter_does_not_query_while_holding_the_change_lock(self, tmp_path, monkeypatch):
        store = TaskStore(tmp_path / "tasks.sqlite3")
        held_during_query: list[bool] = []
        real_events = store.events

        def tracking_events(**kwargs):
            import threading

            # The condition wraps an RLock, so probe it from another thread.
            result: dict[str, bool] = {}

            def probe() -> None:
                acquired = store._change_condition.acquire(blocking=False)
                if acquired:
                    store._change_condition.release()
                result["held"] = not acquired

            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(2)
            held_during_query.append(result["held"])
            return real_events(**kwargs)

        monkeypatch.setattr(store, "events", tracking_events)
        assert store.wait_for_events(after_seq=0, timeout=0.01) == []
        assert held_during_query and not any(held_during_query)

    def test_event_committed_after_query_is_not_missed(self, tmp_path, monkeypatch):
        store = TaskStore(tmp_path / "tasks.sqlite3")
        store.create(create_task(TaskKind.PIPELINE, "pipe", task_id="t1"))
        cursor = store.latest_event_seq()
        real_events = store.events
        calls = {"n": 0}

        def events_then_commit(**kwargs):
            result = real_events(**kwargs)
            calls["n"] += 1
            if calls["n"] == 1:
                store.append_event(
                    TaskEvent(task_id="t1", type=TaskEventType.LOG, created_at="2026-08-16T00:00:01.000Z", message="x")
                )
            return result

        monkeypatch.setattr(store, "events", events_then_commit)
        events = store.wait_for_events(after_seq=cursor, timeout=5)

        assert [event.message for event in events] == ["x"]

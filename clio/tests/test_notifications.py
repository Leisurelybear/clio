from __future__ import annotations

import io
from dataclasses import replace
from unittest.mock import MagicMock

from clio.task_center.manager import TaskManager
from clio.task_center.models import (
    Notification,
    NotificationSeverity,
    TaskEvent,
    TaskEventLevel,
    TaskEventType,
    TaskKind,
    TaskStatus,
    create_task,
    utc_now_iso,
)
from clio.task_center.state_machine import transition_task
from clio.task_center.store import NotificationQuery, TaskStore
from clio.ui.routes.notifications import (
    handle_get_notifications,
    handle_get_notifications_stream,
    handle_post_notification,
    handle_post_notification_read,
    handle_post_notifications_read_all,
)


class _Handler:
    def __init__(self, manager: TaskManager):
        self.manager = manager
        self.responses: list[tuple[dict, int]] = []
        self.wfile = io.BytesIO()
        self.headers: dict[str, str] = {}
        self.send_response = MagicMock()
        self.send_header = MagicMock()
        self.end_headers = MagicMock()

    def _get_task_manager(self):
        return self.manager

    def _send_json(self, data, status=200):
        self.responses.append((data, status))


class _DisconnectOnFlush(io.BytesIO):
    def flush(self):
        raise BrokenPipeError


def _manager(tmp_path):
    return TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)


def test_task_terminal_and_attention_events_create_notifications(tmp_path):
    manager = _manager(tmp_path)
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1", project_name="东京")
    manager.store.create(task)
    running = transition_task(task, TaskStatus.RUNNING, at="2026-08-17T00:00:01.000Z")
    manager.store.save_with_event(
        running,
        TaskEvent(
            task_id=task.id,
            type=TaskEventType.STATUS,
            created_at=running.started_at or running.created_at,
            data={"from": "queued", "to": "running"},
        ),
        expected_status=TaskStatus.QUEUED,
    )
    manager.store.append_event(
        TaskEvent(
            task_id=task.id,
            type=TaskEventType.LOG,
            created_at="2026-08-17T00:00:02.000Z",
            message="一个片段没有音轨",
            level=TaskEventLevel.WARNING,
        )
    )
    succeeded = transition_task(running, TaskStatus.SUCCEEDED, at="2026-08-17T00:00:03.000Z")
    manager.store.save_with_event(
        succeeded,
        TaskEvent(
            task_id=task.id,
            type=TaskEventType.STATUS,
            created_at=succeeded.finished_at or succeeded.created_at,
            message="任务完成",
            data={"from": "running", "to": "succeeded"},
        ),
        expected_status=TaskStatus.RUNNING,
    )

    notifications = manager.store.list_notifications(NotificationQuery(limit=20))

    assert [item.severity for item in notifications] == [
        NotificationSeverity.SUCCESS,
        NotificationSeverity.WARNING,
    ]
    assert notifications[0].task_id == task.id
    assert notifications[0].link == f"?entity=tasks&task_id={task.id}"
    assert manager.store.count_unread_notifications() == 2


def test_ui_notification_dedup_and_read_state(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    item = Notification(
        id="notification-1",
        severity=NotificationSeverity.ERROR,
        title="保存失败",
        message="磁盘不可写",
        created_at=utc_now_iso(),
        dedupe_key="save:error:disk",
    )

    first = store.create_notification(item)
    second = store.create_notification(replace(item, id="notification-2"))

    assert first.id == second.id
    assert store.count_unread_notifications() == 1
    assert store.mark_notification_read(first.id).is_read is True
    assert store.count_unread_notifications() == 0
    assert store.mark_notification_read(first.id, read=False).is_read is False


def test_notification_routes_create_list_read_and_mark_all(tmp_path):
    manager = _manager(tmp_path)
    handler = _Handler(manager)
    handle_post_notification(
        handler,
        {"project": ["京都"]},
        {
            "severity": "warning",
            "title": "素材提醒",
            "message": "原视频离线",
            "source_type": "runtime_warning",
            "source_id": "offline",
            "dedupe_key": "offline:001",
        },
    )
    created, status = handler.responses[-1]
    assert status == 201
    notification_id = created["notification"]["id"]

    handle_get_notifications(handler, {"unread": ["1"]})
    listed, status = handler.responses[-1]
    assert status == 200
    assert listed["unread_count"] == 1
    assert listed["notifications"][0]["project_name"] == "京都"

    handle_post_notification_read(handler, {}, {}, notification_id)
    assert handler.responses[-1][0]["notification"]["is_read"] is True

    handle_post_notification(
        handler,
        {},
        {"severity": "error", "title": "错误", "message": "导出失败"},
    )
    handle_post_notifications_read_all(handler, {}, {})
    assert handler.responses[-1][0]["marked"] == 1
    assert manager.store.count_unread_notifications() == 0


def test_notification_stream_uses_cursor(tmp_path):
    manager = _manager(tmp_path)
    for index in range(2):
        manager.store.create_notification(
            Notification(
                id=f"notification-{index}",
                severity=NotificationSeverity.INFO,
                title="通知",
                message=f"message-{index}",
                created_at=utc_now_iso(),
            )
        )
    handler = _Handler(manager)
    handler.wfile = _DisconnectOnFlush()

    handle_get_notifications_stream(handler, {"after": ["1"]})

    output = handler.wfile.getvalue().decode("utf-8")
    assert "id: 1" not in output
    assert "id: 2" in output
    assert "message-1" in output

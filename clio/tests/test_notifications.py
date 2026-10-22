from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
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
    handle_post_notification_unread,
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


class _ResolvingHandler(_Handler):
    def _resolve_project_dir(self, qs):
        return Path(qs["project_dir"][0])


class _ProjectMapHandler(_Handler):
    def __init__(self, manager: TaskManager, projects: dict[str, Path]):
        super().__init__(manager)
        self.projects = projects

    def _resolve_project_dir(self, qs):
        project_dir = qs.get("project_dir", [None])[0]
        if project_dir:
            return Path(project_dir)
        return self.projects[qs["project"][0]]


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
    assert notifications[0].link == f"?entity=tasks&task_id={task.id}&project=%E4%B8%9C%E4%BA%AC"
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
    created_revision = store.latest_notification_seq()
    assert store.mark_notification_read(first.id).is_read is True
    assert store.latest_notification_seq() == created_revision + 1
    assert store.count_unread_notifications() == 0
    assert store.mark_notification_read(first.id, read=False).is_read is False
    assert store.latest_notification_seq() == created_revision + 2


def test_empty_dedupe_key_is_stored_as_null_without_colliding(tmp_path):
    manager = _manager(tmp_path)
    handler = _Handler(manager)

    for _ in range(2):
        handle_post_notification(
            handler,
            {},
            {"severity": "info", "message": "重复注册", "dedupe_key": "   "},
        )
        assert handler.responses[-1][1] == 201

    notifications = manager.store.list_notifications(NotificationQuery(limit=20))
    assert len(notifications) == 2
    assert len({item.id for item in notifications}) == 2
    assert all(item.dedupe_key is None for item in notifications)


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
    assert listed["total_count"] == 1
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


def test_notification_route_uses_project_dir_and_rejects_backslash_links(tmp_path):
    manager = _manager(tmp_path)
    handler = _ResolvingHandler(manager)
    project_dir = tmp_path / "same-name-a"
    project_dir.mkdir()
    handle_post_notification(
        handler,
        {"project": ["同名项目"], "project_dir": [str(project_dir)]},
        {"severity": "info", "message": "完成", "link": "?entity=video"},
    )
    saved = handler.responses[-1][0]["notification"]
    assert saved["project_id"] == str(project_dir.resolve())
    assert saved["project_name"] == "同名项目"

    handle_post_notification(
        handler,
        {},
        {"severity": "warning", "message": "外部链接", "link": "/\\evil.example"},
    )
    assert handler.responses[-1][1] == 400

    handle_post_notification(
        handler,
        {},
        {"severity": "warning", "message": "非法链接", "link": "//[invalid"},
    )
    assert handler.responses[-1][1] == 400


def test_notification_project_scope_is_consistent_across_routes(tmp_path):
    manager = _manager(tmp_path)
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    handler = _ProjectMapHandler(manager, {"A": project_a, "B": project_b})

    handle_post_notification(
        handler,
        {"project_name": ["A"]},
        {"severity": "warning", "message": "A warning"},
    )
    assert handler.responses[-1][1] == 201
    assert handler.responses[-1][0]["notification"]["project_id"] == str(project_a.resolve())
    handle_post_notification(
        handler,
        {"project": ["B"]},
        {"severity": "error", "message": "B error"},
    )
    b_notification_id = handler.responses[-1][0]["notification"]["id"]

    handle_post_notification(
        handler,
        {"project_name": ["B"]},
        {"severity": "info", "message": "B info"},
    )
    assert handler.responses[-1][0]["unread_count"] == 2

    scope_queries = (
        {"project_id": [str(project_a.resolve())]},
        {"project_dir": [str(project_a)]},
        {"project": ["A"]},
        {"project_name": ["A"]},
    )
    for scope in scope_queries:
        handle_get_notifications(handler, {**scope, "limit": ["20"]})
        listed, status = handler.responses[-1]
        assert status == 200
        assert listed["total_count"] == 1
        assert listed["unread_count"] == 1
        assert listed["notifications"][0]["project_id"] == str(project_a.resolve())

    handle_post_notifications_read_all(handler, {"project_name": ["A"]}, {})
    assert handler.responses[-1][0]["marked"] == 1
    assert handler.responses[-1][0]["unread_count"] == 0

    handle_get_notifications(handler, {"project": ["B"], "limit": ["20"]})
    assert handler.responses[-1][0]["unread_count"] == 2

    handle_post_notification_read(handler, {"project": ["A"]}, {}, b_notification_id)
    assert handler.responses[-1][1] == 404

    handle_post_notification_read(handler, {"project": ["B"]}, {}, b_notification_id)
    assert handler.responses[-1][0]["unread_count"] == 1


def test_task_event_notification_text_is_truncated(tmp_path):
    manager = _manager(tmp_path)
    task = create_task(TaskKind.PIPELINE, "T" * 400, task_id="long-task")
    manager.store.create(task)
    manager.store.append_event(
        TaskEvent(
            task_id=task.id,
            type=TaskEventType.LOG,
            created_at="2026-08-17T00:00:02.000Z",
            message="E" * 5_000,
            level=TaskEventLevel.ERROR,
        )
    )

    notification = manager.store.list_notifications(NotificationQuery(limit=1))[0]

    assert len(notification.title) == 200
    assert notification.title.endswith("：发生错误")
    assert notification.message == "E" * 4_000


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
    assert '"refresh":true' in output


def test_notification_read_all_advances_single_revision(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    for index in range(3):
        store.create_notification(
            Notification(
                id=f"notification-{index}",
                severity=NotificationSeverity.INFO,
                title="通知",
                message=str(index),
                created_at=utc_now_iso(),
            )
        )
    before = store.latest_notification_seq()

    assert store.mark_all_notifications_read() == 3
    assert store.latest_notification_seq() == before + 1
    assert store.count_unread_notifications() == 0


def test_notification_snapshot_reports_filtered_total(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    for index in range(3):
        store.create_notification(
            Notification(
                id=f"notification-{index}",
                severity=NotificationSeverity.WARNING if index else NotificationSeverity.SUCCESS,
                title="通知",
                message=str(index),
                created_at=utc_now_iso(),
            )
        )

    items, unread, total, revision = store.notification_snapshot(
        NotificationQuery(severities=(NotificationSeverity.WARNING,), limit=1)
    )

    assert len(items) == 1
    assert unread == 3
    assert total == 2
    assert revision == 3


def test_notification_unread_route_restores_unread_state(tmp_path):
    manager = _manager(tmp_path)
    handler = _Handler(manager)
    handle_post_notification(handler, {}, {"severity": "info", "message": "待处理"})
    notification_id = handler.responses[-1][0]["notification"]["id"]

    handle_post_notification_read(handler, {}, {}, notification_id)
    assert handler.responses[-1][0]["unread_count"] == 0

    handle_post_notification_unread(handler, {}, {}, notification_id)
    body, status = handler.responses[-1]
    assert status == 200
    assert body["notification"]["is_read"] is False
    assert body["unread_count"] == 1

    handle_post_notification_unread(handler, {}, {}, "missing")
    assert handler.responses[-1][1] == 404


def test_notification_rejects_non_string_dedupe_key(tmp_path):
    handler = _Handler(_manager(tmp_path))

    handle_post_notification(handler, {}, {"severity": "info", "message": "去重键类型错误", "dedupe_key": 123})

    body, status = handler.responses[-1]
    assert status == 400
    assert "dedupe_key" in body["error"]


def test_notification_query_validation_errors(tmp_path):
    handler = _Handler(_manager(tmp_path))

    handle_get_notifications(handler, {"limit": ["abc"]})
    assert handler.responses[-1][1] == 400
    assert "integer" in handler.responses[-1][0]["error"]

    handle_get_notifications(handler, {"limit": ["0"]})
    assert handler.responses[-1][1] == 400
    assert "between" in handler.responses[-1][0]["error"]

    handle_get_notifications(handler, {"unread": ["maybe"]})
    assert handler.responses[-1][1] == 400
    assert "unread" in handler.responses[-1][0]["error"]


def test_notification_query_severity_parsing(tmp_path):
    manager = _manager(tmp_path)
    handler = _Handler(manager)
    for index, severity in enumerate(
        (NotificationSeverity.INFO, NotificationSeverity.WARNING, NotificationSeverity.ERROR)
    ):
        manager.store.create_notification(
            Notification(
                id=f"n-{index}",
                severity=severity,
                title="通知",
                message=str(index),
                created_at=utc_now_iso(),
            )
        )

    handle_get_notifications(handler, {"severity": ["warning,error"], "limit": ["20"]})
    body, status = handler.responses[-1]
    assert status == 200
    assert body["total_count"] == 2

    handle_get_notifications(handler, {"severity": "info", "limit": ["20"]})
    assert handler.responses[-1][0]["total_count"] == 1

    handle_get_notifications(handler, {"severity": ["bogus"]})
    assert handler.responses[-1][1] == 400
    assert "invalid severity" in handler.responses[-1][0]["error"]


def test_notification_stream_rejects_invalid_after(tmp_path):
    handler = _Handler(_manager(tmp_path))

    handle_get_notifications_stream(handler, {"after": ["nope"]})

    assert handler.responses[-1][1] == 400
    assert "integer" in handler.responses[-1][0]["error"]


def test_notification_stream_ignores_invalid_last_event_id(tmp_path):
    manager = _manager(tmp_path)
    manager.store.create_notification(
        Notification(
            id="n-1",
            severity=NotificationSeverity.INFO,
            title="通知",
            message="m",
            created_at=utc_now_iso(),
        )
    )
    handler = _Handler(manager)
    handler.wfile = _DisconnectOnFlush()
    handler.headers = {"Last-Event-ID": "not-a-number"}

    handle_get_notifications_stream(handler, {})

    assert "id: 1" in handler.wfile.getvalue().decode("utf-8")


def test_notification_stream_emits_heartbeat(tmp_path, monkeypatch):
    handler = _Handler(_manager(tmp_path))
    ticks = iter([0.0, 100.0, 100.0])

    monkeypatch.setattr("clio.ui.routes.notifications.time.monotonic", lambda: next(ticks))

    def _stop(_seconds):
        raise BrokenPipeError

    monkeypatch.setattr("clio.ui.routes.notifications.time.sleep", _stop)

    handle_get_notifications_stream(handler, {})

    assert b": heartbeat" in handler.wfile.getvalue()


def test_notification_post_validation_errors(tmp_path):
    handler = _Handler(_manager(tmp_path))

    cases = [
        ({"severity": "bogus", "message": "x"}, "severity"),
        ({"severity": "info"}, "message"),
        ({"severity": "info", "message": "x", "title": "   "}, "title"),
        ({"severity": "info", "message": "x", "dedupe_key": "d" * 301}, "dedupe_key"),
        ({"severity": "info", "message": "x", "source_type": 5}, "source_type"),
        ({"severity": "info", "message": "x", "source_id": 5}, "source_id"),
        ({"severity": "info", "message": "x", "task_id": 5}, "task_id"),
        ({"severity": "info", "message": "x", "data": []}, "data"),
        ({"severity": "info", "message": "x", "task_id": "missing"}, "does not exist"),
    ]
    for payload, expected in cases:
        handle_post_notification(handler, {}, payload)
        body, status = handler.responses[-1]
        assert status == 400, payload
        assert expected in body["error"], payload

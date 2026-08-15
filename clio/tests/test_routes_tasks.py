from __future__ import annotations

import io
from unittest.mock import MagicMock

from clio.task_center.manager import TaskManager
from clio.task_center.models import (
    TaskEvent,
    TaskEventType,
    TaskKind,
    TaskStatus,
    TaskVisibility,
    create_task,
)
from clio.task_center.state_machine import transition_task
from clio.task_center.store import TaskStore
from clio.ui.routes.tasks import (
    handle_get_task,
    handle_get_tasks,
    handle_get_tasks_stream,
    handle_post_task_cancel,
    handle_post_task_retry,
)


class _Handler:
    def __init__(self, manager: TaskManager):
        self.manager = manager
        self.responses: list[tuple[dict, int]] = []
        self.wfile = io.BytesIO()
        self.send_response = MagicMock()
        self.send_header = MagicMock()
        self.end_headers = MagicMock()

    def _get_task_manager(self):
        return self.manager

    def _send_json(self, data, status=200):
        self.responses.append((data, status))


def _manager(tmp_path):
    return TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)


def test_list_defaults_to_foreground_and_hides_private_fields(tmp_path):
    manager = _manager(tmp_path)
    manager.store.create(
        create_task(
            TaskKind.PIPELINE,
            "处理素材",
            task_id="foreground",
            project_id="project-1",
            project_path="G:/private/project",
            input_data={"api_key": "secret"},
        )
    )
    manager.store.create(
        create_task(
            TaskKind.WAVEFORM,
            "生成波形",
            task_id="background",
            visibility=TaskVisibility.BACKGROUND,
        )
    )
    handler = _Handler(manager)

    handle_get_tasks(handler, {})

    body, status = handler.responses[-1]
    assert status == 200
    assert [task["id"] for task in body["tasks"]] == ["foreground"]
    assert "project_path" not in body["tasks"][0]
    assert "input_data" not in body["tasks"][0]


def test_list_filters_status_kind_project_and_visibility(tmp_path):
    manager = _manager(tmp_path)
    first = create_task(TaskKind.PIPELINE, "一", task_id="first", project_id="p1")
    manager.store.create(first)
    running = transition_task(first, TaskStatus.RUNNING)
    manager.store.save_with_event(
        running,
        TaskEvent(
            task_id=first.id,
            type=TaskEventType.STATUS,
            created_at=running.started_at or running.created_at,
        ),
        expected_status=TaskStatus.QUEUED,
    )
    manager.store.create(create_task(TaskKind.RERUN, "二", task_id="second", project_id="p2"))
    handler = _Handler(manager)

    handle_get_tasks(
        handler,
        {
            "project_id": ["p1"],
            "status": ["running,failed"],
            "kind": ["pipeline"],
            "visibility": ["all"],
        },
    )

    body, status = handler.responses[-1]
    assert status == 200
    assert [task["id"] for task in body["tasks"]] == ["first"]
    assert body["total"] == 1


def test_list_rejects_invalid_filters(tmp_path):
    handler = _Handler(_manager(tmp_path))

    handle_get_tasks(handler, {"status": ["unknown"]})

    body, status = handler.responses[-1]
    assert status == 400
    assert "invalid status" in body["error"]


def test_detail_returns_recent_events_in_ascending_order(tmp_path):
    manager = _manager(tmp_path)
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1")
    manager.store.create(task)
    for index in range(3):
        manager.store.append_event(
            TaskEvent(
                task_id=task.id,
                type=TaskEventType.LOG,
                created_at=f"2026-08-16T00:00:0{index + 1}.000Z",
                message=f"log-{index}",
            )
        )
    handler = _Handler(manager)

    handle_get_task(handler, {"event_limit": ["2"]}, task.id)

    body, status = handler.responses[-1]
    assert status == 200
    assert [event["message"] for event in body["events"]] == ["log-1", "log-2"]


def test_detail_returns_404_for_unknown_task(tmp_path):
    handler = _Handler(_manager(tmp_path))

    handle_get_task(handler, {}, "missing")

    assert handler.responses[-1][1] == 404


def test_cancel_endpoint_updates_queued_task(tmp_path):
    manager = _manager(tmp_path)
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1", cancellable=True)
    manager.store.create(task)
    handler = _Handler(manager)

    handle_post_task_cancel(handler, {}, {}, task.id)

    body, status = handler.responses[-1]
    assert status == 200
    assert body["task"]["status"] == "cancelled"


def test_retry_endpoint_creates_linked_task(tmp_path):
    manager = _manager(tmp_path)
    manager.register(TaskKind.RERUN, lambda context: {"ok": True})
    original = manager.submit(TaskKind.RERUN, "重新分析", task_id="original")
    assert manager.wait(original.id).status is TaskStatus.SUCCEEDED
    handler = _Handler(manager)

    handle_post_task_retry(handler, {}, {}, original.id)

    body, status = handler.responses[-1]
    assert status == 201
    assert body["task"]["retry_of"] == original.id
    assert body["task"]["id"] != original.id


class _DisconnectOnFlush(io.BytesIO):
    def flush(self):
        raise BrokenPipeError


def test_stream_sends_cursor_event_and_public_task_snapshot(tmp_path):
    manager = _manager(tmp_path)
    task = create_task(
        TaskKind.PIPELINE,
        "处理素材",
        task_id="task-1",
        project_path="G:/private/project",
        input_data={"api_key": "secret"},
    )
    manager.store.create(task)
    handler = _Handler(manager)
    handler.wfile = _DisconnectOnFlush()

    handle_get_tasks_stream(handler, {"after": ["0"]})

    output = handler.wfile.getvalue().decode("utf-8")
    assert "id: 1" in output
    assert '"task_id":"task-1"' in output
    assert "private/project" not in output
    assert "secret" not in output


def test_cancel_unknown_task_returns_404(tmp_path):
    handler = _Handler(_manager(tmp_path))

    handle_post_task_cancel(handler, {}, {}, "missing")

    assert handler.responses[-1][1] == 404

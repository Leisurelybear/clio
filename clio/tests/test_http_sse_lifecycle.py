from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

from clio.config import AppConfig
from clio.task_center.manager import TaskManager
from clio.task_center.models import TaskEvent, TaskEventType, TaskKind, TaskStatus, create_task
from clio.task_center.state_machine import transition_task
from clio.task_center.store import TaskStore
from clio.ui.http_server import BUSY_ERROR, BoundedThreadingHTTPServer
from clio.ui.server import make_handler


class _EventStream:
    def __init__(self, connection: http.client.HTTPConnection, response: http.client.HTTPResponse):
        self.connection = connection
        self.response = response

    def next_event(self) -> dict[str, Any]:
        event_id: int | None = None
        data: list[str] = []
        while True:
            line = self.response.readline()
            if not line:
                raise AssertionError("SSE stream closed before the next event")
            text = line.decode("utf-8").rstrip("\r\n")
            if not text:
                if data:
                    return {"id": event_id, "data": json.loads("\n".join(data))}
                continue
            if text.startswith(":"):
                continue
            field, _, value = text.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "id":
                event_id = int(value)
            elif field == "data":
                data.append(value)

    def close(self) -> None:
        self.response.close()
        self.connection.close()


class _EndpointServer:
    def __init__(
        self,
        config: AppConfig,
        manager: TaskManager,
        *,
        max_workers: int = 16,
        max_streams: int = 64,
    ):
        handler = make_handler(config, None, task_manager=manager)
        self.manager = manager
        self.server = BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), handler, max_workers=max_workers, max_streams=max_streams
        )
        self.host, self.port = self.server.server_address[:2]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], int]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return json.loads(response.read().decode("utf-8")), response.status
        finally:
            connection.close()

    def open_stream(self, path: str, *, last_event_id: int | None = None) -> _EventStream:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        headers = {"Accept": "text/event-stream"}
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        assert response.status == 200
        assert response.getheader("Content-Type") == "text/event-stream"
        return _EventStream(connection, response)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.manager.shutdown(timeout=0.2)


def _manager(tmp_path: Path, *, recover_on_start: bool = False) -> TaskManager:
    return TaskManager(
        TaskStore(tmp_path / "tasks.sqlite3"),
        recover_on_start=recover_on_start,
    )


def test_late_subscription_starts_after_snapshot_cursor(tmp_path, loaded_config: AppConfig):
    manager = _manager(tmp_path)
    manager.store.create(create_task(TaskKind.PIPELINE, "先完成", task_id="before", project_id="p1"))
    endpoint = _EndpointServer(loaded_config, manager)
    stream = None
    try:
        snapshot, status = endpoint.request_json("GET", "/api/tasks?limit=20")
        assert status == 200
        assert snapshot["latest_seq"] == 1

        stream = endpoint.open_stream(f"/api/tasks/stream?after={snapshot['latest_seq']}")
        manager.store.create(create_task(TaskKind.PIPELINE, "后创建", task_id="after", project_id="p1"))

        event = stream.next_event()
        assert event["id"] == 2
        assert event["data"]["event"]["task_id"] == "after"
        assert event["data"]["task"]["status"] == "queued"
    finally:
        if stream is not None:
            stream.close()
        endpoint.close()


def test_two_tabs_cancel_once_and_both_receive_the_terminal_event(tmp_path, loaded_config: AppConfig):
    manager = _manager(tmp_path)
    task = create_task(TaskKind.PIPELINE, "可取消任务", task_id="cancel-me", cancellable=True)
    manager.store.create(task)
    endpoint = _EndpointServer(loaded_config, manager)
    first_stream = None
    second_stream = None
    try:
        first_stream = endpoint.open_stream("/api/tasks/stream?after=1")
        second_stream = endpoint.open_stream("/api/tasks/stream?after=1")

        first, first_status = endpoint.request_json("POST", "/api/tasks/cancel-me/cancel", {})
        second, second_status = endpoint.request_json("POST", "/api/tasks/cancel-me/cancel", {})
        assert (first_status, second_status) == (200, 200)
        assert first["task"]["status"] == "cancelled"
        assert second["task"]["status"] == "cancelled"

        for stream in (first_stream, second_stream):
            event = stream.next_event()
            assert event["id"] == 2
            assert event["data"]["task"]["status"] == "cancelled"
    finally:
        if first_stream is not None:
            first_stream.close()
        if second_stream is not None:
            second_stream.close()
        endpoint.close()


def test_restart_marks_active_task_interrupted_then_endpoint_retry_succeeds(tmp_path, loaded_config: AppConfig):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    queued = create_task(
        TaskKind.PIPELINE,
        "崩溃恢复",
        task_id="crashed",
        project_id="p1",
        input_data={"steps": ["analyze"]},
    )
    store.create(queued)
    running = transition_task(queued, TaskStatus.RUNNING)
    store.save_with_event(
        running,
        TaskEvent(
            task_id=queued.id,
            type=TaskEventType.STATUS,
            created_at=running.started_at or running.created_at,
            data={"from": "queued", "to": "running"},
        ),
        expected_status=TaskStatus.QUEUED,
    )

    manager = _manager(tmp_path, recover_on_start=True)
    manager.register(TaskKind.PIPELINE, lambda context: {"ok": True})
    endpoint = _EndpointServer(loaded_config, manager)
    try:
        recovered, status = endpoint.request_json("GET", "/api/tasks/crashed")
        assert status == 200
        assert recovered["task"]["status"] == "interrupted"

        retried, retry_status = endpoint.request_json("POST", "/api/tasks/crashed/retry", {})
        assert retry_status == 201
        assert retried["task"]["retry_of"] == "crashed"
        completed = manager.wait(retried["task"]["id"], timeout=5)
        assert completed.status is TaskStatus.SUCCEEDED
        assert completed.input_data == {"steps": ["analyze"]}
    finally:
        endpoint.close()


def test_reconnect_with_last_event_id_skips_and_replays_the_right_events(tmp_path, loaded_config: AppConfig):
    manager = _manager(tmp_path)
    manager.store.create(create_task(TaskKind.PIPELINE, "一", task_id="one"))
    manager.store.create(create_task(TaskKind.PIPELINE, "二", task_id="two"))
    endpoint = _EndpointServer(loaded_config, manager)
    first_stream = None
    second_stream = None
    try:
        first_stream = endpoint.open_stream("/api/tasks/stream?after=0", last_event_id=1)
        replayed = first_stream.next_event()
        assert replayed["id"] == 2
        assert replayed["data"]["event"]["task_id"] == "two"
        first_stream.close()
        first_stream = None

        second_stream = endpoint.open_stream("/api/tasks/stream?after=0", last_event_id=2)
        manager.store.append_event(
            TaskEvent(
                task_id="two",
                type=TaskEventType.LOG,
                created_at="2026-10-22T10:00:00.000Z",
                message="after reconnect",
            )
        )

        live = second_stream.next_event()
        assert live["id"] == 3
        assert live["data"]["event"]["message"] == "after reconnect"
    finally:
        if first_stream is not None:
            first_stream.close()
        if second_stream is not None:
            second_stream.close()
        endpoint.close()


def test_terminal_event_between_detail_snapshot_and_stream_is_replayed(tmp_path, loaded_config: AppConfig):
    manager = _manager(tmp_path)
    task = create_task(TaskKind.PIPELINE, "竞态任务", task_id="race", project_id="p1")
    manager.store.create(task)
    endpoint = _EndpointServer(loaded_config, manager)
    stream = None
    try:
        detail, status = endpoint.request_json("GET", "/api/tasks/race?event_limit=10")
        assert status == 200
        assert detail["task"]["status"] == "queued"

        running = transition_task(task, TaskStatus.RUNNING)
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
        succeeded = transition_task(running, TaskStatus.SUCCEEDED)
        manager.store.save_with_event(
            succeeded,
            TaskEvent(
                task_id=task.id,
                type=TaskEventType.STATUS,
                created_at=succeeded.finished_at or succeeded.created_at,
                data={"from": "running", "to": "succeeded"},
            ),
            expected_status=TaskStatus.RUNNING,
        )

        stream = endpoint.open_stream(f"/api/tasks/stream?after={detail['latest_seq']}")
        first = stream.next_event()
        second = stream.next_event()
        assert [first["id"], second["id"]] == [2, 3]
        assert second["data"]["task"]["status"] == "succeeded"
    finally:
        if stream is not None:
            stream.close()
        endpoint.close()


def test_open_stream_does_not_starve_normal_requests(tmp_path, loaded_config: AppConfig):
    """Every open tab holds two SSE streams; they must not eat the request budget."""
    manager = _manager(tmp_path)
    endpoint = _EndpointServer(loaded_config, manager, max_workers=1, max_streams=2)
    stream = None
    try:
        stream = endpoint.open_stream("/api/tasks/stream?after=0")
        snapshot, status = endpoint.request_json("GET", "/api/tasks?limit=5")
        assert status == 200
        assert snapshot["latest_seq"] == 0
    finally:
        if stream is not None:
            stream.close()
        endpoint.close()


def test_request_budget_exhaustion_answers_503(tmp_path, loaded_config: AppConfig):
    """An exhausted request budget must answer 503 instead of an empty response."""
    manager = _manager(tmp_path)
    endpoint = _EndpointServer(loaded_config, manager, max_workers=1, max_streams=1)
    try:
        budget = endpoint.server.request_budget
        assert budget.acquire("/api/tasks") is True
        try:
            body, status = endpoint.request_json("GET", "/api/tasks?limit=5")
        finally:
            budget.release("/api/tasks")
        assert status == 503
        assert body == {"ok": False, "error": BUSY_ERROR}
    finally:
        endpoint.close()

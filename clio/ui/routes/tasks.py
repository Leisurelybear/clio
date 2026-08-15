from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, TypeVar

from clio._str_enum import StrEnum
from clio.task_center.executor import TaskHandlerNotRegisteredError
from clio.task_center.manager import TaskNotCancellableError
from clio.task_center.models import TaskKind, TaskStatus, TaskVisibility
from clio.task_center.state_machine import InvalidTaskTransition
from clio.task_center.store import TaskNotFoundError, TaskQuery

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol

_EnumT = TypeVar("_EnumT", bound=StrEnum)


def _first(qs: dict[str, Any], key: str) -> str | None:
    raw = qs.get(key)
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if raw is None:
        return None
    return str(raw).strip()


def _parse_int(qs: dict[str, Any], key: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = _first(qs, key)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"{key} must be an integer") from e
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _parse_enums(qs: dict[str, Any], key: str, enum_type: type[_EnumT]) -> tuple[_EnumT, ...]:
    raw_values = qs.get(key, [])
    if not isinstance(raw_values, list):
        raw_values = [raw_values]
    values: list[_EnumT] = []
    for raw in raw_values:
        for value in str(raw).split(","):
            value = value.strip()
            if not value:
                continue
            try:
                parsed = enum_type(value)
            except ValueError as e:
                allowed = ", ".join(item.value for item in enum_type)
                raise ValueError(f"invalid {key}: {value} (allowed: {allowed})") from e
            if parsed not in values:
                values.append(parsed)
    return tuple(values)


def _parse_query(qs: dict[str, Any]) -> TaskQuery:
    visibility_raw = _first(qs, "visibility")
    if visibility_raw in (None, ""):
        visibility = TaskVisibility.FOREGROUND
    elif visibility_raw == "all":
        visibility = None
    else:
        try:
            visibility = TaskVisibility(visibility_raw)
        except ValueError as e:
            raise ValueError("visibility must be foreground, background, or all") from e
    return TaskQuery(
        project_id=_first(qs, "project_id") or None,
        statuses=_parse_enums(qs, "status", TaskStatus),
        kinds=_parse_enums(qs, "kind", TaskKind),
        visibility=visibility,
        limit=_parse_int(qs, "limit", 50, minimum=1, maximum=200),
        offset=_parse_int(qs, "offset", 0, minimum=0, maximum=10_000_000),
    )


def handle_get_tasks(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    try:
        query = _parse_query(qs)
    except ValueError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)
    manager = handler._get_task_manager()
    tasks = manager.store.list(query)
    handler._send_json(
        {
            "tasks": [task.to_dict() for task in tasks],
            "total": manager.store.count(query),
            "limit": query.limit,
            "offset": query.offset,
            "latest_seq": manager.store.latest_event_seq(),
        }
    )


def handle_get_task(handler: HandlerProtocol, qs: dict[str, Any], task_id: str) -> None:
    manager = handler._get_task_manager()
    task = manager.store.get(task_id)
    if task is None:
        return handler._send_json({"ok": False, "error": "task not found"}, 404)
    try:
        event_limit = _parse_int(qs, "event_limit", 200, minimum=1, maximum=1000)
    except ValueError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)
    events = manager.store.recent_events(task_id, limit=event_limit)
    handler._send_json(
        {
            "task": task.to_dict(),
            "events": [event.to_dict() for event in events],
            "latest_seq": manager.store.latest_event_seq(),
        }
    )


def handle_get_tasks_stream(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    try:
        cursor = _parse_int(qs, "after", 0, minimum=0, maximum=9_223_372_036_854_775_807)
    except ValueError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)
    manager = handler._get_task_manager()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    last_heartbeat = time.monotonic()
    try:
        while True:
            events = manager.store.events(after_seq=cursor, limit=200)
            if events:
                for event in events:
                    cursor = event.seq or cursor
                    task = manager.store.get(event.task_id)
                    payload = {
                        "seq": cursor,
                        "event": event.to_dict(),
                        "task": task.to_dict() if task is not None else None,
                    }
                    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    handler.wfile.write(f"id: {cursor}\ndata: {body}\n\n".encode())
                handler.wfile.flush()
                last_heartbeat = time.monotonic()
                continue
            now = time.monotonic()
            if now - last_heartbeat >= 10:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
                last_heartbeat = now
            time.sleep(0.25)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        return


def handle_post_task_cancel(handler: HandlerProtocol, qs: dict[str, Any], obj: dict, task_id: str) -> None:
    del qs, obj
    manager = handler._get_task_manager()
    try:
        task = manager.request_cancel(task_id)
    except TaskNotFoundError:
        return handler._send_json({"ok": False, "error": "task not found"}, 404)
    except TaskNotCancellableError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 409)
    handler._send_json({"ok": True, "task": task.to_dict()})


def handle_post_task_retry(handler: HandlerProtocol, qs: dict[str, Any], obj: dict, task_id: str) -> None:
    del qs, obj
    manager = handler._get_task_manager()
    try:
        task = manager.retry(task_id)
    except TaskNotFoundError:
        return handler._send_json({"ok": False, "error": "task not found"}, 404)
    except (InvalidTaskTransition, TaskHandlerNotRegisteredError, ValueError) as e:
        return handler._send_json({"ok": False, "error": str(e)}, 409)
    handler._send_json({"ok": True, "task": task.to_dict()}, 201)

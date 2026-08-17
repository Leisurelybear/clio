"""Persistent notification inbox backed by the Task Center SQLite store."""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from clio.task_center.models import Notification, NotificationSeverity, notification_data, utc_now_iso
from clio.task_center.store import NotificationQuery

if TYPE_CHECKING:
    from clio.ui.handler_protocol import HandlerProtocol


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


def _parse_severities(qs: dict[str, Any]) -> tuple[NotificationSeverity, ...]:
    raw_values = qs.get("severity", [])
    if not isinstance(raw_values, list):
        raw_values = [raw_values]
    values: list[NotificationSeverity] = []
    for raw in raw_values:
        for value in str(raw).split(","):
            value = value.strip()
            if not value:
                continue
            try:
                parsed = NotificationSeverity(value)
            except ValueError as e:
                allowed = ", ".join(item.value for item in NotificationSeverity)
                raise ValueError(f"invalid severity: {value} (allowed: {allowed})") from e
            if parsed not in values:
                values.append(parsed)
    return tuple(values)


def _query(qs: dict[str, Any]) -> NotificationQuery:
    unread_raw = (_first(qs, "unread") or "").lower()
    if unread_raw not in {"", "0", "1", "true", "false"}:
        raise ValueError("unread must be 0 or 1")
    return NotificationQuery(
        unread_only=unread_raw in {"1", "true"},
        severities=_parse_severities(qs),
        project_id=_first(qs, "project_id") or None,
        limit=_parse_int(qs, "limit", 50, minimum=1, maximum=200),
        offset=_parse_int(qs, "offset", 0, minimum=0, maximum=10_000_000),
    )


def _project_name(qs: dict[str, Any]) -> str | None:
    return _first(qs, "project") or _first(qs, "project_name") or None


def handle_get_notifications(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    try:
        query = _query(qs)
    except ValueError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)
    store = handler._get_task_manager().store
    notifications, unread, total, latest_seq = store.notification_snapshot(query)
    handler._send_json(
        {
            "notifications": [item.to_dict() for item in notifications],
            "unread_count": unread,
            "total_count": total,
            "latest_seq": latest_seq,
            "limit": query.limit,
            "offset": query.offset,
        }
    )


def handle_get_notifications_stream(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    try:
        cursor = _parse_int(qs, "after", 0, minimum=0, maximum=9_223_372_036_854_775_807)
    except ValueError as e:
        return handler._send_json({"ok": False, "error": str(e)}, 400)
    store = handler._get_task_manager().store
    headers = getattr(handler, "headers", None)
    try:
        last_event_id = int(headers.get("Last-Event-ID", "0") or "0") if headers is not None else 0
    except (TypeError, ValueError):
        last_event_id = 0
    cursor = max(cursor, last_event_id)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    last_heartbeat = time.monotonic()
    try:
        while True:
            latest = store.latest_notification_seq()
            if latest > cursor:
                cursor = latest
                body = json.dumps({"seq": cursor, "refresh": True}, separators=(",", ":"))
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


def handle_post_notification(handler: HandlerProtocol, qs: dict[str, Any], obj: dict[str, Any]) -> None:
    severity_raw = obj.get("severity", "info")
    try:
        severity = NotificationSeverity(str(severity_raw))
    except ValueError:
        return handler._send_json({"ok": False, "error": "severity must be info, success, warning, or error"}, 400)
    message = obj.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > 4_000:
        return handler._send_json(
            {"ok": False, "error": "message must be a non-empty string up to 4000 characters"}, 400
        )
    title = obj.get("title", "通知")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        return handler._send_json({"ok": False, "error": "title must be a non-empty string up to 200 characters"}, 400)
    link = obj.get("link")
    if link is not None:
        try:
            parsed_link = urlsplit(link) if isinstance(link, str) else None
        except ValueError:
            parsed_link = None
        if (
            parsed_link is None
            or not link.startswith(("/", "?"))
            or link.startswith("//")
            or "\\" in link
            or "%5c" in link.lower()
            or parsed_link.scheme
            or parsed_link.netloc
            or len(link) > 500
        ):
            return handler._send_json({"ok": False, "error": "link must be a local path"}, 400)
    dedupe_key = obj.get("dedupe_key")
    if dedupe_key is not None and (not isinstance(dedupe_key, str) or len(dedupe_key) > 300):
        return handler._send_json({"ok": False, "error": "dedupe_key must be a string up to 300 characters"}, 400)
    source_type = obj.get("source_type", "ui")
    source_id = obj.get("source_id")
    if not isinstance(source_type, str) or len(source_type) > 80:
        return handler._send_json({"ok": False, "error": "source_type is invalid"}, 400)
    if source_id is not None and (not isinstance(source_id, str) or len(source_id) > 300):
        return handler._send_json({"ok": False, "error": "source_id is invalid"}, 400)
    task_id = obj.get("task_id")
    if task_id is not None and (not isinstance(task_id, str) or len(task_id) > 200):
        return handler._send_json({"ok": False, "error": "task_id is invalid"}, 400)
    data = obj.get("data", {})
    if not isinstance(data, dict):
        return handler._send_json({"ok": False, "error": "data must be an object"}, 400)
    project_name = _project_name(qs)
    project_id = None
    if project_name or _first(qs, "project_dir"):
        resolve_project = getattr(handler, "_resolve_project_dir", None)
        if callable(resolve_project):
            project_dir = resolve_project(qs)
            project_id = str(project_dir.resolve())
            project_name = project_name or project_dir.name
        else:
            project_id = project_name
    store = handler._get_task_manager().store
    if task_id is not None and store.get(task_id) is None:
        return handler._send_json({"ok": False, "error": "task_id does not exist"}, 400)
    notification = Notification(
        id=f"n-ui-{uuid.uuid4().hex}",
        severity=severity,
        title=title.strip(),
        message=message.strip(),
        created_at=utc_now_iso(),
        source_type=source_type,
        source_id=source_id,
        task_id=task_id,
        project_id=project_id,
        project_name=project_name,
        link=link,
        dedupe_key=dedupe_key,
        data=notification_data(data),
    )
    saved = store.create_notification(notification)
    handler._send_json(
        {
            "ok": True,
            "notification": saved.to_dict(),
            "unread_count": store.count_unread_notifications(),
            "latest_seq": store.latest_notification_seq(),
        },
        201,
    )


def handle_post_notification_read(
    handler: HandlerProtocol, qs: dict[str, Any], obj: dict[str, Any], notification_id: str
) -> None:
    del qs, obj
    saved = handler._get_task_manager().store.mark_notification_read(notification_id)
    if saved is None:
        return handler._send_json({"ok": False, "error": "notification not found"}, 404)
    store = handler._get_task_manager().store
    handler._send_json(
        {
            "ok": True,
            "notification": saved.to_dict(),
            "unread_count": store.count_unread_notifications(),
            "latest_seq": store.latest_notification_seq(),
        }
    )


def handle_post_notification_unread(
    handler: HandlerProtocol, qs: dict[str, Any], obj: dict[str, Any], notification_id: str
) -> None:
    del qs, obj
    saved = handler._get_task_manager().store.mark_notification_read(notification_id, read=False)
    if saved is None:
        return handler._send_json({"ok": False, "error": "notification not found"}, 404)
    store = handler._get_task_manager().store
    handler._send_json(
        {
            "ok": True,
            "notification": saved.to_dict(),
            "unread_count": store.count_unread_notifications(),
            "latest_seq": store.latest_notification_seq(),
        }
    )


def handle_post_notifications_read_all(handler: HandlerProtocol, qs: dict[str, Any], obj: dict[str, Any]) -> None:
    del obj
    store = handler._get_task_manager().store
    count = store.mark_all_notifications_read(project_id=_first(qs, "project_id") or None)
    handler._send_json(
        {
            "ok": True,
            "marked": count,
            "unread_count": store.count_unread_notifications(),
            "latest_seq": store.latest_notification_seq(),
        }
    )

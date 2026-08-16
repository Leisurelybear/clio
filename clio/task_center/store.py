from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from clio.task_center.models import (
    TERMINAL_TASK_STATUSES,
    UTC_TZ,
    Notification,
    NotificationSeverity,
    TaskEvent,
    TaskEventLevel,
    TaskEventType,
    TaskKind,
    TaskRecord,
    TaskStatus,
    TaskVisibility,
    notification_data,
    notification_id,
    utc_now_iso,
)
from clio.task_center.schema import initialize_schema


class TaskStoreError(RuntimeError):
    pass


class TaskAlreadyExistsError(TaskStoreError):
    pass


class TaskNotFoundError(TaskStoreError):
    pass


class TaskUpdateConflictError(TaskStoreError):
    pass


class TaskStoreDataError(TaskStoreError):
    pass


@dataclass(frozen=True, slots=True)
class TaskQuery:
    project_id: str | None = None
    statuses: tuple[TaskStatus, ...] = ()
    kinds: tuple[TaskKind, ...] = ()
    visibility: TaskVisibility | None = None
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 200:
            raise ValueError("task query limit must be between 1 and 200")
        if self.offset < 0:
            raise ValueError("task query offset must be non-negative")


@dataclass(frozen=True, slots=True)
class NotificationQuery:
    unread_only: bool = False
    severities: tuple[NotificationSeverity, ...] = ()
    project_id: str | None = None
    limit: int = 50
    offset: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 200:
            raise ValueError("notification query limit must be between 1 and 200")
        if self.offset < 0:
            raise ValueError("notification query offset must be non-negative")


class TaskStore:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000):
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")
        self.path = path
        self._busy_timeout_ms = busy_timeout_ms
        self._schema_lock = threading.Lock()
        self._schema_identity: tuple[int, int, int] | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Create the database eagerly so startup failures are reported at the
        # owner boundary rather than on the first asynchronous event query.
        with self._connect():
            pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            self._ensure_schema(connection)
            yield connection
        finally:
            connection.close()

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        with self._schema_lock:
            stat = self.path.stat()
            schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
            identity = (stat.st_dev, stat.st_ino, schema_version)
            if identity == self._schema_identity:
                return
            initialize_schema(connection)
            stat = self.path.stat()
            schema_version = int(connection.execute("PRAGMA schema_version").fetchone()[0])
            self._schema_identity = (stat.st_dev, stat.st_ino, schema_version)

    def create(self, task: TaskRecord) -> TaskRecord:
        event = TaskEvent(
            task_id=task.id,
            type=TaskEventType.CREATED,
            created_at=task.created_at,
            message=task.message or task.title,
            data={"kind": task.kind.value, "status": task.status.value},
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._insert_task(connection, task, updated_at=event.created_at)
                self._insert_event(connection, event)
                connection.commit()
        except sqlite3.IntegrityError as e:
            raise TaskAlreadyExistsError(f"task already exists: {task.id}") from e
        return task

    def get(self, task_id: str) -> TaskRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row is not None else None

    def require(self, task_id: str) -> TaskRecord:
        task = self.get(task_id)
        if task is None:
            raise TaskNotFoundError(f"task not found: {task_id}")
        return task

    def list(self, query: TaskQuery | None = None) -> builtins.list[TaskRecord]:
        query = query or TaskQuery()
        where: builtins.list[str] = []
        params: builtins.list[Any] = []
        if query.project_id is not None:
            where.append("project_id = ?")
            params.append(query.project_id)
        self._append_enum_filter(where, params, "status", query.statuses)
        self._append_enum_filter(where, params, "kind", query.kinds)
        if query.visibility is not None:
            where.append("visibility = ?")
            params.append(query.visibility.value)
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend((query.limit, query.offset))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def count(self, query: TaskQuery | None = None) -> int:
        query = query or TaskQuery()
        where: builtins.list[str] = []
        params: builtins.list[Any] = []
        if query.project_id is not None:
            where.append("project_id = ?")
            params.append(query.project_id)
        self._append_enum_filter(where, params, "status", query.statuses)
        self._append_enum_filter(where, params, "kind", query.kinds)
        if query.visibility is not None:
            where.append("visibility = ?")
            params.append(query.visibility.value)
        sql = "SELECT COUNT(*) FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._connect() as connection:
            return int(connection.execute(sql, params).fetchone()[0])

    def active_tasks(self) -> builtins.list[TaskRecord]:
        statuses = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING)
        placeholders = ", ".join("?" for _ in statuses)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks WHERE status IN ({placeholders}) ORDER BY updated_at DESC, id DESC",  # noqa: S608
                [status.value for status in statuses],
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def snapshot(self, query: TaskQuery | None = None) -> tuple[builtins.list[TaskRecord], int, int]:
        """Return list, count and event cursor from one SQLite read snapshot."""
        query = query or TaskQuery()
        where: builtins.list[str] = []
        params: builtins.list[Any] = []
        if query.project_id is not None:
            where.append("project_id = ?")
            params.append(query.project_id)
        self._append_enum_filter(where, params, "status", query.statuses)
        self._append_enum_filter(where, params, "kind", query.kinds)
        if query.visibility is not None:
            where.append("visibility = ?")
            params.append(query.visibility.value)
        clause = " WHERE " + " AND ".join(where) if where else ""
        list_sql = f"SELECT * FROM tasks{clause} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"  # noqa: S608
        count_sql = f"SELECT COUNT(*) FROM tasks{clause}"  # noqa: S608
        with self._connect() as connection:
            connection.execute("BEGIN")
            # Establish the read snapshot with the cursor first. A task created
            # afterwards is absent from the list and will therefore be replayed
            # by SSE after this cursor; a task created before it appears in both.
            latest_seq = int(connection.execute("SELECT COALESCE(MAX(seq), 0) FROM task_events").fetchone()[0])
            rows = connection.execute(list_sql, [*params, query.limit, query.offset]).fetchall()
            total = int(connection.execute(count_sql, params).fetchone()[0])
            connection.commit()
        return [self._row_to_task(row) for row in rows], total, latest_seq

    def save_with_event(
        self,
        task: TaskRecord,
        event: TaskEvent,
        *,
        expected_status: TaskStatus | None = None,
    ) -> tuple[TaskRecord, TaskEvent]:
        if event.task_id != task.id:
            raise ValueError("task event does not belong to task")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            params = self._task_update_params(task, event.created_at)
            sql = """
                UPDATE tasks SET
                    kind = ?, status = ?, title = ?, project_id = ?, project_name = ?, project_path = ?,
                    parent_id = ?, retry_of = ?, visibility = ?, created_at = ?, started_at = ?, finished_at = ?,
                    heartbeat_at = ?, updated_at = ?, phase = ?, current = ?, total = ?, progress_pct = ?, message = ?,
                    cancellable = ?, cancel_requested = ?, error_code = ?, error_message = ?, input_data_json = ?,
                    input_summary_json = ?, result_summary_json = ?
                WHERE id = ?
            """
            if expected_status is not None:
                sql += " AND status = ?"
                params.append(expected_status.value)
            cursor = connection.execute(sql, params)
            if cursor.rowcount != 1:
                exists = connection.execute("SELECT status FROM tasks WHERE id = ?", (task.id,)).fetchone()
                connection.rollback()
                if exists is None:
                    raise TaskNotFoundError(f"task not found: {task.id}")
                expected = expected_status.value if expected_status else "existing"
                raise TaskUpdateConflictError(f"task {task.id} status changed: expected {expected}")
            saved_event = self._insert_event(connection, event)
            connection.commit()
        return task, saved_event

    def append_event(self, event: TaskEvent) -> TaskEvent:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                saved = self._insert_event(connection, event)
                connection.execute(
                    "UPDATE tasks SET updated_at = ?, heartbeat_at = ? WHERE id = ?",
                    (event.created_at, event.created_at, event.task_id),
                )
                connection.commit()
                return saved
        except sqlite3.IntegrityError as e:
            raise TaskNotFoundError(f"task not found: {event.task_id}") from e

    def events(
        self,
        *,
        after_seq: int = 0,
        task_id: str | None = None,
        limit: int = 200,
    ) -> builtins.list[TaskEvent]:
        if after_seq < 0:
            raise ValueError("after_seq must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("event limit must be between 1 and 1000")
        where = ["seq > ?"]
        params: builtins.list[Any] = [after_seq]
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM task_events WHERE {' AND '.join(where)} ORDER BY seq ASC LIMIT ?",  # noqa: S608
                params,
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def latest_event_seq(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM task_events").fetchone()
        return int(row[0])

    def recent_events(self, task_id: str, *, limit: int = 200) -> builtins.list[TaskEvent]:
        if not 1 <= limit <= 1000:
            raise ValueError("event limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM task_events WHERE task_id = ? ORDER BY seq DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [self._row_to_event(row) for row in reversed(rows)]

    def create_notification(self, notification: Notification) -> Notification:
        """Persist a UI-originated notification, returning the deduped row."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = self._insert_notification(connection, notification)
            connection.commit()
        return saved

    def list_notifications(self, query: NotificationQuery | None = None) -> builtins.list[Notification]:
        query = query or NotificationQuery()
        where: builtins.list[str] = []
        params: builtins.list[Any] = []
        if query.unread_only:
            where.append("read_at IS NULL")
        if query.project_id is not None:
            where.append("project_id = ?")
            params.append(query.project_id)
        if query.severities:
            placeholders = ",".join("?" for _ in query.severities)
            where.append(f"severity IN ({placeholders})")
            params.extend(item.value for item in query.severities)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM notifications{clause} ORDER BY seq DESC LIMIT ? OFFSET ?",  # noqa: S608
                [*params, query.limit, query.offset],
            ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def notification_snapshot(
        self, query: NotificationQuery | None = None
    ) -> tuple[builtins.list[Notification], int, int]:
        """Return inbox rows, unread count, and cursor from one read snapshot."""
        query = query or NotificationQuery()
        where: builtins.list[str] = []
        params: builtins.list[Any] = []
        if query.unread_only:
            where.append("read_at IS NULL")
        if query.project_id is not None:
            where.append("project_id = ?")
            params.append(query.project_id)
        if query.severities:
            placeholders = ",".join("?" for _ in query.severities)
            where.append(f"severity IN ({placeholders})")
            params.extend(item.value for item in query.severities)
        clause = " WHERE " + " AND ".join(where) if where else ""
        unread_clause = " AND project_id = ?" if query.project_id is not None else ""
        unread_params = [query.project_id] if query.project_id is not None else []
        with self._connect() as connection:
            connection.execute("BEGIN")
            latest_seq = int(connection.execute("SELECT COALESCE(MAX(seq), 0) FROM notifications").fetchone()[0])
            rows = connection.execute(
                f"SELECT * FROM notifications{clause} ORDER BY seq DESC LIMIT ? OFFSET ?",  # noqa: S608
                [*params, query.limit, query.offset],
            ).fetchall()
            unread = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM notifications WHERE read_at IS NULL{unread_clause}",  # noqa: S608
                    unread_params,
                ).fetchone()[0]
            )
            connection.commit()
        return [self._row_to_notification(row) for row in rows], unread, latest_seq

    def count_unread_notifications(self, *, project_id: str | None = None) -> int:
        where = ["read_at IS NULL"]
        params: builtins.list[Any] = []
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM notifications WHERE {' AND '.join(where)}",  # noqa: S608
                params,
            ).fetchone()
        return int(row[0])

    def latest_notification_seq(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(seq), 0) FROM notifications").fetchone()
        return int(row[0])

    def notification_events(self, *, after_seq: int = 0, limit: int = 200) -> builtins.list[Notification]:
        if after_seq < 0:
            raise ValueError("after_seq must be non-negative")
        if not 1 <= limit <= 1000:
            raise ValueError("notification event limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notifications WHERE seq > ? ORDER BY seq ASC LIMIT ?",
                (after_seq, limit),
            ).fetchall()
        return [self._row_to_notification(row) for row in rows]

    def mark_notification_read(self, notification_id_value: str, *, read: bool = True) -> Notification | None:
        read_at = utc_now_iso() if read else None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE notifications SET read_at = ? WHERE id = ?",
                (read_at, notification_id_value),
            )
            row = connection.execute("SELECT * FROM notifications WHERE id = ?", (notification_id_value,)).fetchone()
            connection.commit()
        if cursor.rowcount == 0 and row is None:
            return None
        return self._row_to_notification(row) if row is not None else None

    def mark_all_notifications_read(self, *, project_id: str | None = None) -> int:
        where = ["read_at IS NULL"]
        params: builtins.list[Any] = [utc_now_iso()]
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE notifications SET read_at = ? WHERE {' AND '.join(where)}",  # noqa: S608
                params,
            )
        return cursor.rowcount

    def delete(self, task_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        return cursor.rowcount == 1

    def cleanup(
        self,
        *,
        retention_days: int = 30,
        max_terminal_tasks: int = 1000,
        now: datetime | None = None,
    ) -> int:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if max_terminal_tasks < 0:
            raise ValueError("max_terminal_tasks must be non-negative")
        current_time = now or datetime.now(UTC_TZ)
        cutoff = (
            (current_time - timedelta(days=retention_days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        terminal_values = tuple(status.value for status in TERMINAL_TASK_STATUSES)
        placeholders = ",".join("?" for _ in terminal_values)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = connection.execute(
                f"DELETE FROM tasks WHERE status IN ({placeholders}) AND finished_at < ?",  # noqa: S608
                (*terminal_values, cutoff),
            ).rowcount
            excess_rows = connection.execute(
                f"""
                SELECT id FROM tasks WHERE status IN ({placeholders})
                ORDER BY finished_at DESC, id DESC LIMIT -1 OFFSET ?
                """,  # noqa: S608
                (*terminal_values, max_terminal_tasks),
            ).fetchall()
            excess_ids = [row[0] for row in excess_rows]
            excess = 0
            if excess_ids:
                delete_placeholders = ",".join("?" for _ in excess_ids)
                excess = connection.execute(
                    f"DELETE FROM tasks WHERE id IN ({delete_placeholders})",  # noqa: S608
                    excess_ids,
                ).rowcount
            connection.commit()
        # Read notifications are disposable history; unread messages remain
        # until the user explicitly handles them.
        self._cleanup_notifications(
            retention_days=retention_days, max_notifications=max_terminal_tasks, now=current_time
        )
        return old + excess

    def _cleanup_notifications(self, *, retention_days: int, max_notifications: int, now: datetime) -> int:
        cutoff = (now - timedelta(days=retention_days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            removed = connection.execute(
                "DELETE FROM notifications WHERE read_at IS NOT NULL AND created_at < ?",
                (cutoff,),
            ).rowcount
            excess_rows = connection.execute(
                "SELECT id FROM notifications WHERE read_at IS NOT NULL ORDER BY seq DESC LIMIT -1 OFFSET ?",
                (max(0, max_notifications),),
            ).fetchall()
            if excess_rows:
                ids = [row[0] for row in excess_rows]
                placeholders = ",".join("?" for _ in ids)
                removed += connection.execute(
                    f"DELETE FROM notifications WHERE id IN ({placeholders})",  # noqa: S608
                    ids,
                ).rowcount
            connection.commit()
        return removed

    @staticmethod
    def _append_enum_filter(
        where: builtins.list[str],
        params: builtins.list[Any],
        column: str,
        values: Iterable[TaskStatus | TaskKind],
    ) -> None:
        selected = tuple(values)
        if not selected:
            return
        placeholders = ",".join("?" for _ in selected)
        where.append(f"{column} IN ({placeholders})")
        params.extend(value.value for value in selected)

    @staticmethod
    def _json(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    @classmethod
    def _insert_task(cls, connection: sqlite3.Connection, task: TaskRecord, *, updated_at: str) -> None:
        connection.execute(
            """
            INSERT INTO tasks (
                id, kind, status, title, project_id, project_name, project_path, parent_id, retry_of,
                visibility, created_at, started_at, finished_at, heartbeat_at, updated_at, phase,
                current, total, progress_pct, message, cancellable, cancel_requested, error_code,
                error_message, input_data_json, input_summary_json, result_summary_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task.id,
                task.kind.value,
                task.status.value,
                task.title,
                task.project_id,
                task.project_name,
                task.project_path,
                task.parent_id,
                task.retry_of,
                task.visibility.value,
                task.created_at,
                task.started_at,
                task.finished_at,
                task.heartbeat_at,
                updated_at,
                task.phase,
                task.current,
                task.total,
                task.progress_pct,
                task.message,
                int(task.cancellable),
                int(task.cancel_requested),
                task.error_code,
                task.error_message,
                cls._json(task.input_data),
                cls._json(task.input_summary),
                cls._json(task.result_summary),
            ),
        )

    @classmethod
    def _task_update_params(cls, task: TaskRecord, updated_at: str) -> builtins.list[Any]:
        return [
            task.kind.value,
            task.status.value,
            task.title,
            task.project_id,
            task.project_name,
            task.project_path,
            task.parent_id,
            task.retry_of,
            task.visibility.value,
            task.created_at,
            task.started_at,
            task.finished_at,
            task.heartbeat_at,
            updated_at,
            task.phase,
            task.current,
            task.total,
            task.progress_pct,
            task.message,
            int(task.cancellable),
            int(task.cancel_requested),
            task.error_code,
            task.error_message,
            cls._json(task.input_data),
            cls._json(task.input_summary),
            cls._json(task.result_summary),
            task.id,
        ]

    @classmethod
    def _insert_event(cls, connection: sqlite3.Connection, event: TaskEvent) -> TaskEvent:
        cursor = connection.execute(
            """
            INSERT INTO task_events(task_id, type, level, created_at, message, data_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event.task_id,
                event.type.value,
                event.level.value,
                event.created_at,
                event.message,
                cls._json(event.data),
            ),
        )
        if cursor.lastrowid is None:
            raise TaskStoreError("failed to allocate task event sequence")
        saved = replace(event, seq=int(cursor.lastrowid))
        notification = cls._notification_from_event(connection, saved)
        if notification is not None:
            cls._insert_notification(connection, notification)
        return saved

    @classmethod
    def _notification_from_event(cls, connection: sqlite3.Connection, event: TaskEvent) -> Notification | None:
        target_status = str(event.data.get("to", ""))
        terminal = {
            TaskStatus.SUCCEEDED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            TaskStatus.INTERRUPTED.value,
        }
        if event.type is TaskEventType.STATUS and target_status in terminal:
            severity = {
                TaskStatus.SUCCEEDED.value: NotificationSeverity.SUCCESS,
                TaskStatus.FAILED.value: NotificationSeverity.ERROR,
                TaskStatus.CANCELLED.value: NotificationSeverity.WARNING,
                TaskStatus.INTERRUPTED.value: NotificationSeverity.WARNING,
            }[target_status]
            label = {
                TaskStatus.SUCCEEDED.value: "已完成",
                TaskStatus.FAILED.value: "失败",
                TaskStatus.CANCELLED.value: "已取消",
                TaskStatus.INTERRUPTED.value: "已中断",
            }[target_status]
        elif event.level is TaskEventLevel.WARNING:
            severity, label = NotificationSeverity.WARNING, "需要注意"
        elif event.level is TaskEventLevel.ERROR:
            severity, label = NotificationSeverity.ERROR, "发生错误"
        else:
            return None
        row = connection.execute(
            "SELECT title, project_id, project_name FROM tasks WHERE id = ?", (event.task_id,)
        ).fetchone()
        if row is None:
            return None
        title = str(row[0] or "任务")
        return Notification(
            id=notification_id(f"task-event:{event.seq}"),
            severity=severity,
            title=f"{title}：{label}",
            message=event.message or label,
            created_at=event.created_at,
            source_type="task_event",
            source_id=str(event.seq),
            task_id=event.task_id,
            project_id=row[1],
            project_name=row[2],
            link=f"?entity=tasks&task_id={event.task_id}",
            dedupe_key=f"task-event:{event.seq}",
            data={"event_seq": event.seq, "event_type": event.type.value},
        )

    @classmethod
    def _insert_notification(cls, connection: sqlite3.Connection, notification: Notification) -> Notification:
        connection.execute(
            """
            INSERT OR IGNORE INTO notifications
              (id, severity, title, message, created_at, read_at, source_type, source_id,
               task_id, project_id, project_name, link, dedupe_key, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification.id,
                notification.severity.value,
                notification.title,
                notification.message,
                notification.created_at,
                notification.read_at,
                notification.source_type,
                notification.source_id,
                notification.task_id,
                notification.project_id,
                notification.project_name,
                notification.link,
                notification.dedupe_key,
                cls._json(notification_data(notification.data)),
            ),
        )
        if notification.dedupe_key:
            row = connection.execute(
                "SELECT * FROM notifications WHERE dedupe_key = ?", (notification.dedupe_key,)
            ).fetchone()
        else:
            row = connection.execute("SELECT * FROM notifications WHERE id = ?", (notification.id,)).fetchone()
        if row is None:
            raise TaskStoreError("failed to persist notification")
        return cls._row_to_notification(row)

    @staticmethod
    def _load_json(raw: str, *, field: str, task_id: str) -> dict[str, Any]:
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as e:
            raise TaskStoreDataError(f"task {task_id} has invalid {field}") from e
        if not isinstance(data, dict):
            raise TaskStoreDataError(f"task {task_id} has non-object {field}")
        return data

    @classmethod
    def _row_to_task(cls, row: sqlite3.Row) -> TaskRecord:
        task_id = str(row["id"])
        try:
            return TaskRecord(
                id=task_id,
                kind=TaskKind(row["kind"]),
                status=TaskStatus(row["status"]),
                title=row["title"],
                project_id=row["project_id"],
                project_name=row["project_name"],
                project_path=row["project_path"],
                parent_id=row["parent_id"],
                retry_of=row["retry_of"],
                visibility=TaskVisibility(row["visibility"]),
                created_at=row["created_at"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                heartbeat_at=row["heartbeat_at"],
                updated_at=row["updated_at"],
                phase=row["phase"],
                current=int(row["current"]),
                total=int(row["total"]),
                progress_pct=float(row["progress_pct"]) if row["progress_pct"] is not None else None,
                message=row["message"],
                cancellable=bool(row["cancellable"]),
                cancel_requested=bool(row["cancel_requested"]),
                error_code=row["error_code"],
                error_message=row["error_message"],
                input_data=cls._load_json(row["input_data_json"], field="input_data_json", task_id=task_id),
                input_summary=cls._load_json(row["input_summary_json"], field="input_summary_json", task_id=task_id),
                result_summary=cls._load_json(row["result_summary_json"], field="result_summary_json", task_id=task_id),
            )
        except (TypeError, ValueError) as e:
            if isinstance(e, TaskStoreDataError):
                raise
            raise TaskStoreDataError(f"task {task_id} has invalid stored data: {e}") from e

    @classmethod
    def _row_to_event(cls, row: sqlite3.Row) -> TaskEvent:
        task_id = str(row["task_id"])
        try:
            return TaskEvent(
                seq=int(row["seq"]),
                task_id=task_id,
                type=TaskEventType(row["type"]),
                level=TaskEventLevel(row["level"]),
                created_at=row["created_at"],
                message=row["message"],
                data=cls._load_json(row["data_json"], field="event data_json", task_id=task_id),
            )
        except (TypeError, ValueError) as e:
            if isinstance(e, TaskStoreDataError):
                raise
            raise TaskStoreDataError(f"task event {row['seq']} has invalid stored data: {e}") from e

    @classmethod
    def _row_to_notification(cls, row: sqlite3.Row) -> Notification:
        try:
            data = cls._load_json(row["data_json"], field="notification data_json", task_id=str(row["id"]))
            return Notification(
                seq=int(row["seq"]),
                id=str(row["id"]),
                severity=NotificationSeverity(row["severity"]),
                title=row["title"],
                message=row["message"],
                created_at=row["created_at"],
                read_at=row["read_at"],
                source_type=row["source_type"],
                source_id=row["source_id"],
                task_id=row["task_id"],
                project_id=row["project_id"],
                project_name=row["project_name"],
                link=row["link"],
                dedupe_key=row["dedupe_key"],
                data=data,
            )
        except (TypeError, ValueError) as e:
            if isinstance(e, TaskStoreDataError):
                raise
            raise TaskStoreDataError(f"notification {row['id']} has invalid stored data: {e}") from e

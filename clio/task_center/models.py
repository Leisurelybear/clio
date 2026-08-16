from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from clio._str_enum import StrEnum

UTC_TZ = getattr(dt, "UTC", dt.timezone.utc)  # noqa: UP017  # Python 3.10 local tooling compatibility


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    }
)

_PRIVATE_TASK_KEYS = frozenset(
    {
        "api_key",
        "context_override",
        "task_prompts",
        "prompt",
        "prompts",
    }
)


def sanitize_task_payload(value: Any) -> Any:
    """Keep task history retryable without persisting prompts, secrets, or paths."""
    if isinstance(value, dict):
        return {
            str(key): sanitize_task_payload(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_TASK_KEYS
        }
    if isinstance(value, list):
        return [sanitize_task_payload(item) for item in value]
    return value


class TaskKind(StrEnum):
    PIPELINE = "pipeline"
    RERUN = "rerun"
    CUT_EXPORT = "cut_export"
    EXPORT = "export"
    WHISPER_INSTALL = "whisper_install"
    WAVEFORM = "waveform"


class TaskVisibility(StrEnum):
    FOREGROUND = "foreground"
    BACKGROUND = "background"


class TaskEventType(StrEnum):
    CREATED = "created"
    STATUS = "status"
    PROGRESS = "progress"
    LOG = "log"
    CANCEL_REQUESTED = "cancel_requested"


class TaskEventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class NotificationSeverity(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


def utc_now_iso() -> str:
    return dt.datetime.now(UTC_TZ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalized_progress(current: int, total: int) -> float | None:
    if current < 0:
        raise ValueError("task current must be non-negative")
    if total < 0:
        raise ValueError("task total must be non-negative")
    if total == 0:
        return None
    return round(min(current, total) / total * 100, 2)


@dataclass(frozen=True, slots=True)
class TaskRecord:
    id: str
    kind: TaskKind
    status: TaskStatus
    title: str
    created_at: str
    project_id: str | None = None
    project_name: str | None = None
    project_path: str | None = None
    parent_id: str | None = None
    retry_of: str | None = None
    visibility: TaskVisibility = TaskVisibility.FOREGROUND
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    updated_at: str | None = None
    phase: str = ""
    current: int = 0
    total: int = 0
    progress_pct: float | None = None
    message: str = ""
    cancellable: bool = False
    cancel_requested: bool = False
    error_code: str | None = None
    error_message: str | None = None
    input_data: dict[str, Any] = field(default_factory=dict)
    input_summary: dict[str, Any] = field(default_factory=dict)
    result_summary: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("task id must not be empty")
        if not self.title.strip():
            raise ValueError("task title must not be empty")
        expected_pct = normalized_progress(self.current, self.total)
        if self.progress_pct is not None and not 0 <= self.progress_pct <= 100:
            raise ValueError("task progress_pct must be between 0 and 100")
        if self.progress_pct is not None and expected_pct is None:
            raise ValueError("task progress_pct requires a positive total")
        if self.progress_pct is not None and self.progress_pct != expected_pct:
            raise ValueError("task progress_pct does not match current and total")
        if self.status in TERMINAL_TASK_STATUSES and self.finished_at is None:
            raise ValueError("terminal task must have finished_at")
        if self.status is TaskStatus.CANCELLING and not self.cancel_requested:
            raise ValueError("cancelling task must have cancel_requested")
        if self.parent_id == self.id or self.retry_of == self.id:
            raise ValueError("task cannot reference itself")

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_TASK_STATUSES

    def to_dict(self, *, include_private: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "kind": self.kind.value,
            "status": self.status.value,
            "title": self.title,
            "created_at": self.created_at,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "parent_id": self.parent_id,
            "retry_of": self.retry_of,
            "visibility": self.visibility.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "heartbeat_at": self.heartbeat_at,
            "updated_at": self.updated_at or self.heartbeat_at or self.created_at,
            "phase": self.phase,
            "current": self.current,
            "total": self.total,
            "progress_pct": self.progress_pct,
            "message": self.message,
            "cancellable": self.cancellable,
            "cancel_requested": self.cancel_requested,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "input_summary": sanitize_task_payload(self.input_summary),
            "result_summary": sanitize_task_payload(self.result_summary),
        }
        if include_private:
            data["project_path"] = self.project_path
            data["input_data"] = dict(self.input_data)
        return data


@dataclass(frozen=True, slots=True)
class TaskEvent:
    task_id: str
    type: TaskEventType
    created_at: str
    message: str = ""
    level: TaskEventLevel = TaskEventLevel.INFO
    data: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None

    def __post_init__(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task event task_id must not be empty")
        if self.seq is not None and self.seq <= 0:
            raise ValueError("task event seq must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "task_id": self.task_id,
            "type": self.type.value,
            "created_at": self.created_at,
            "message": self.message,
            "level": self.level.value,
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class Notification:
    """A durable user-facing message, independent from task lifecycle state."""

    id: str
    severity: NotificationSeverity
    title: str
    message: str
    created_at: str
    source_type: str = ""
    source_id: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    link: str | None = None
    read_at: str | None = None
    dedupe_key: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    seq: int | None = None

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "seq": self.seq,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "created_at": self.created_at,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "link": self.link,
            "read_at": self.read_at,
            "is_read": self.is_read,
            "data": dict(self.data),
        }


def notification_id(dedupe_key: str | None = None) -> str:
    """Generate stable IDs for deduped messages and random IDs otherwise."""
    if dedupe_key:
        digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]
        return f"n-{digest}"
    return f"n-{uuid.uuid4().hex}"


def notification_data(value: Any) -> dict[str, Any]:
    """Keep notification metadata JSON-safe and small enough for the inbox."""
    if not isinstance(value, dict):
        return {}
    try:
        cleaned = sanitize_task_payload(value)
        encoded = json.dumps(cleaned, ensure_ascii=False)
        if len(encoded) > 8_192:
            return {"detail": encoded[:8_192]}
        return dict(cleaned)
    except (TypeError, ValueError):
        return {}


def create_task(
    kind: TaskKind,
    title: str,
    *,
    task_id: str | None = None,
    created_at: str | None = None,
    project_id: str | None = None,
    project_name: str | None = None,
    project_path: str | None = None,
    parent_id: str | None = None,
    retry_of: str | None = None,
    visibility: TaskVisibility = TaskVisibility.FOREGROUND,
    cancellable: bool = False,
    input_data: dict[str, Any] | None = None,
    input_summary: dict[str, Any] | None = None,
) -> TaskRecord:
    timestamp = created_at or utc_now_iso()
    return TaskRecord(
        id=task_id or uuid.uuid4().hex,
        kind=kind,
        status=TaskStatus.QUEUED,
        title=title,
        created_at=timestamp,
        updated_at=timestamp,
        project_id=project_id,
        project_name=project_name,
        project_path=project_path,
        parent_id=parent_id,
        retry_of=retry_of,
        visibility=visibility,
        cancellable=cancellable,
        input_data=dict(input_data or {}),
        input_summary=dict(input_summary or {}),
    )

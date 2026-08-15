from __future__ import annotations

from dataclasses import replace

from clio.task_center.models import TaskRecord, TaskStatus, utc_now_iso


class InvalidTaskTransition(ValueError):
    pass


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLING,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        }
    ),
    TaskStatus.CANCELLING: frozenset(
        {
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        }
    ),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.INTERRUPTED: frozenset(),
}


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


def require_transition(current: TaskStatus, target: TaskStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTaskTransition(f"invalid task transition: {current.value} -> {target.value}")


def transition_task(
    task: TaskRecord,
    target: TaskStatus,
    *,
    at: str | None = None,
    message: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> TaskRecord:
    require_transition(task.status, target)
    changed_at = at or utc_now_iso()
    changes: dict = {"status": target, "heartbeat_at": changed_at}
    if target is TaskStatus.RUNNING:
        changes["started_at"] = task.started_at or changed_at
    if target is TaskStatus.CANCELLING:
        changes["cancel_requested"] = True
    if target in {TaskStatus.CANCELLED, TaskStatus.INTERRUPTED}:
        changes["cancel_requested"] = task.cancel_requested or target is TaskStatus.CANCELLED
    if target in {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    }:
        changes["finished_at"] = changed_at
    if message is not None:
        changes["message"] = message
    if target is TaskStatus.FAILED:
        changes["error_code"] = error_code
        changes["error_message"] = error_message or message
    elif error_code is not None or error_message is not None:
        raise ValueError("error details are only valid for failed tasks")
    return replace(task, **changes)

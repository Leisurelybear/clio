"""Unified background task lifecycle and persistence."""

from clio.task_center.manager import TaskAlreadyRunningError, TaskManager
from clio.task_center.models import (
    TaskEvent,
    TaskEventLevel,
    TaskEventType,
    TaskKind,
    TaskRecord,
    TaskStatus,
    TaskVisibility,
    create_task,
)
from clio.task_center.reporter import TaskCancelled, TaskProgressReporter, TaskReporter
from clio.task_center.state_machine import InvalidTaskTransition, transition_task
from clio.task_center.store import TaskQuery, TaskStore

__all__ = [
    "InvalidTaskTransition",
    "TaskEvent",
    "TaskEventLevel",
    "TaskEventType",
    "TaskKind",
    "TaskManager",
    "TaskAlreadyRunningError",
    "TaskRecord",
    "TaskReporter",
    "TaskProgressReporter",
    "TaskQuery",
    "TaskStatus",
    "TaskStore",
    "TaskVisibility",
    "TaskCancelled",
    "create_task",
    "transition_task",
]

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from clio.task_center.models import TaskKind, TaskRecord

if TYPE_CHECKING:
    from clio.task_center.reporter import TaskReporter


@dataclass(frozen=True, slots=True)
class TaskContext:
    task: TaskRecord
    input_data: dict[str, Any]
    reporter: TaskReporter
    cancel_event: threading.Event


class TaskHandler(Protocol):
    def __call__(self, context: TaskContext) -> dict[str, Any] | None: ...


ConcurrencyKey = str | Callable[[TaskRecord], str | None] | None


@dataclass(frozen=True, slots=True)
class TaskHandlerRegistration:
    kind: TaskKind
    handler: TaskHandler
    concurrency_key: ConcurrencyKey = None
    max_concurrency: int = 1
    cancellable: bool = False

    def __post_init__(self) -> None:
        if self.max_concurrency <= 0:
            raise ValueError("task max_concurrency must be positive")

    def key_for(self, task: TaskRecord) -> str | None:
        if callable(self.concurrency_key):
            return self.concurrency_key(task)
        return self.concurrency_key


class TaskHandlerAlreadyRegisteredError(ValueError):
    pass


class TaskHandlerNotRegisteredError(LookupError):
    pass


class TaskExecutorRegistry:
    def __init__(self) -> None:
        self._registrations: dict[TaskKind, TaskHandlerRegistration] = {}

    def register(
        self,
        kind: TaskKind,
        handler: TaskHandler,
        *,
        concurrency_key: ConcurrencyKey = None,
        max_concurrency: int = 1,
        cancellable: bool = False,
    ) -> TaskHandlerRegistration:
        if kind in self._registrations:
            raise TaskHandlerAlreadyRegisteredError(f"task handler already registered: {kind.value}")
        registration = TaskHandlerRegistration(
            kind=kind,
            handler=handler,
            concurrency_key=concurrency_key,
            max_concurrency=max_concurrency,
            cancellable=cancellable,
        )
        self._registrations[kind] = registration
        return registration

    def require(self, kind: TaskKind) -> TaskHandlerRegistration:
        try:
            return self._registrations[kind]
        except KeyError as e:
            raise TaskHandlerNotRegisteredError(f"task handler not registered: {kind.value}") from e

    def kinds(self) -> tuple[TaskKind, ...]:
        return tuple(self._registrations)

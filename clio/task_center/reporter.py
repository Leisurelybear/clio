from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clio.task_center.models import TaskEventLevel

if TYPE_CHECKING:
    from clio.task_center.manager import TaskManager


class TaskCancelled(RuntimeError):
    pass


class TaskReporter:
    def __init__(self, manager: TaskManager, task_id: str):
        self._manager = manager
        self.task_id = task_id

    @property
    def cancel_requested(self) -> bool:
        return self._manager.is_cancel_requested(self.task_id)

    def raise_if_cancelled(self) -> None:
        if self.cancel_requested:
            raise TaskCancelled("任务已取消")

    def progress(
        self,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        self._manager.update_progress(
            self.task_id,
            phase=phase,
            current=current,
            total=total,
            message=message,
        )

    def log(self, message: str, *, level: TaskEventLevel = TaskEventLevel.INFO) -> None:
        self._manager.append_log(self.task_id, message, level=level)


class TaskProgressReporter:
    """Adapt the legacy ProgressTracker protocol to a managed task reporter."""

    def __init__(self, reporter: TaskReporter, legacy_tracker: Any | None = None):
        self._reporter = reporter
        self._legacy = legacy_tracker

    def update(
        self,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        self._reporter.progress(phase=phase, current=current, total=total, message=message)
        if self._legacy is not None:
            self._legacy.update(phase=phase, current=current, total=total, message=message)

    def next(self, *, message: str | None = None) -> None:
        task = self._reporter._manager.store.require(self._reporter.task_id)
        self._reporter.progress(current=task.current + 1, message=message)
        if self._legacy is not None:
            self._legacy.next(message=message)

    def log(self, message: str, *, level: TaskEventLevel = TaskEventLevel.INFO) -> None:
        self._reporter.log(message, level=level)
        if self._legacy is not None:
            self._legacy.log(message)

    def done(self, message: str = "") -> None:
        if self._legacy is not None:
            self._legacy.done(message)

    def error(self, message: str) -> None:
        self._reporter.log(message, level=TaskEventLevel.ERROR)
        if self._legacy is not None:
            self._legacy.error(message)

    def cancelled(self, message: str = "") -> None:
        self._reporter.log(message or "任务已取消", level=TaskEventLevel.WARNING)
        if self._legacy is not None:
            self._legacy.cancelled(message)

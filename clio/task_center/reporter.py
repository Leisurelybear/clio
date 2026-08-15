from __future__ import annotations

from typing import TYPE_CHECKING

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

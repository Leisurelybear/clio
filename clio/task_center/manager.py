from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import Any

from clio.task_center.executor import (
    TaskContext,
    TaskExecutorRegistry,
    TaskHandler,
    TaskHandlerNotRegisteredError,
)
from clio.task_center.models import (
    TaskEvent,
    TaskEventLevel,
    TaskEventType,
    TaskKind,
    TaskRecord,
    TaskStatus,
    TaskVisibility,
    create_task,
    normalized_progress,
    sanitize_task_payload,
    utc_now_iso,
)
from clio.task_center.reporter import TaskCancelled, TaskReporter
from clio.task_center.state_machine import InvalidTaskTransition, transition_task
from clio.task_center.store import TaskQuery, TaskStore, TaskUpdateConflictError


class TaskNotCancellableError(ValueError):
    pass


class TaskManagerClosedError(RuntimeError):
    pass


class TaskConcurrencyPolicyError(RuntimeError):
    pass


class TaskAlreadyRunningError(TaskConcurrencyPolicyError):
    """Raised when a caller requests a single active task per concurrency key."""


@dataclass(slots=True)
class _RuntimeTask:
    thread: threading.Thread
    cancel_event: threading.Event
    private_input_data: dict[str, Any]


class TaskManager:
    def __init__(
        self,
        store: TaskStore,
        *,
        registry: TaskExecutorRegistry | None = None,
        recover_on_start: bool = True,
        cleanup_interval_sec: float = 3600.0,
        retention_days: int = 30,
        max_terminal_tasks: int = 1000,
    ):
        self.store = store
        self.registry = registry or TaskExecutorRegistry()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._runtime: dict[str, _RuntimeTask] = {}
        self._task_locks: dict[str, threading.Lock] = {}
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        self._semaphore_limits: dict[str, int] = {}
        self._closed = False
        self._cleanup_interval_sec = max(0.0, cleanup_interval_sec)
        self._retention_days = retention_days
        self._max_terminal_tasks = max_terminal_tasks
        self._last_cleanup_monotonic = time.monotonic()
        if recover_on_start:
            self.recover_interrupted()
        self.cleanup_deleted_total = self.store.cleanup(
            retention_days=retention_days,
            max_terminal_tasks=max_terminal_tasks,
        )

    def register(
        self,
        kind: TaskKind,
        handler: TaskHandler,
        *,
        concurrency_key=None,
        max_concurrency: int = 1,
        cancellable: bool = False,
    ):
        with self._lock:
            return self.registry.register(
                kind,
                handler,
                concurrency_key=concurrency_key,
                max_concurrency=max_concurrency,
                cancellable=cancellable,
            )

    def ensure_registered(
        self,
        kind: TaskKind,
        handler: TaskHandler,
        *,
        concurrency_key=None,
        max_concurrency: int = 1,
        cancellable: bool = False,
    ):
        """Register a lazy route handler once, atomically across request threads."""
        with self._lock:
            if kind in self.registry.kinds():
                return self.registry.require(kind)
            return self.registry.register(
                kind,
                handler,
                concurrency_key=concurrency_key,
                max_concurrency=max_concurrency,
                cancellable=cancellable,
            )

    def submit(
        self,
        kind: TaskKind,
        title: str,
        *,
        task_id: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        project_path: str | None = None,
        parent_id: str | None = None,
        retry_of: str | None = None,
        visibility: TaskVisibility = TaskVisibility.FOREGROUND,
        input_data: dict[str, Any] | None = None,
        private_input_data: dict[str, Any] | None = None,
        input_summary: dict[str, Any] | None = None,
        reject_if_active: bool = False,
    ) -> TaskRecord:
        registration = self.registry.require(kind)
        task = create_task(
            kind,
            title,
            task_id=task_id,
            project_id=project_id,
            project_name=project_name,
            project_path=project_path,
            parent_id=parent_id,
            retry_of=retry_of,
            visibility=visibility,
            cancellable=registration.cancellable,
            input_data=sanitize_task_payload(dict(input_data or {})),
            input_summary=input_summary,
        )
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(task.id, cancel_event),
            name=f"clio-task-{task.id[:8]}",
            daemon=True,
        )
        with self._condition:
            if self._closed:
                raise TaskManagerClosedError("task manager is closed")
            if reject_if_active:
                key = registration.key_for(task)
                if key is not None:
                    active = self.store.active_tasks()
                    for existing in active:
                        if existing.id == task.id:
                            continue
                        try:
                            existing_registration = self.registry.require(existing.kind)
                        except TaskHandlerNotRegisteredError:
                            continue
                        if existing_registration.key_for(existing) == key:
                            raise TaskAlreadyRunningError(
                                f"an active task already uses concurrency key {key!r}: {existing.id}"
                            )
            self.store.create(task)
            self._runtime[task.id] = _RuntimeTask(
                thread=thread,
                cancel_event=cancel_event,
                private_input_data=dict(private_input_data or {}),
            )
            self._task_locks.setdefault(task.id, threading.Lock())
            thread.start()
            self._condition.notify_all()
        return task

    def retry(self, task_id: str, *, new_task_id: str | None = None) -> TaskRecord:
        original = self.store.require(task_id)
        if not original.is_terminal:
            raise ValueError("only terminal tasks can be retried")
        return self.submit(
            original.kind,
            original.title,
            task_id=new_task_id,
            project_id=original.project_id,
            project_name=original.project_name,
            project_path=original.project_path,
            parent_id=original.parent_id,
            retry_of=original.id,
            visibility=original.visibility,
            input_data=original.input_data,
            input_summary=original.input_summary,
            reject_if_active=True,
        )

    def request_cancel(self, task_id: str) -> TaskRecord:
        lock = self._task_lock(task_id)
        with lock:
            task = self.store.require(task_id)
            if task.is_terminal:
                return task
            if not task.cancellable:
                raise TaskNotCancellableError(f"task is not cancellable: {task_id}")
            with self._condition:
                runtime = self._runtime.get(task_id)
                if runtime is not None:
                    runtime.cancel_event.set()
            target = TaskStatus.CANCELLED if task.status is TaskStatus.QUEUED else TaskStatus.CANCELLING
            if task.status is TaskStatus.CANCELLING:
                return task
            changed_at = utc_now_iso()
            cancel_message = "已取消" if target is TaskStatus.CANCELLED else "正在取消"
            updated = transition_task(task, target, at=changed_at, message=cancel_message)
            event = TaskEvent(
                task_id=task.id,
                type=TaskEventType.CANCEL_REQUESTED,
                created_at=changed_at,
                message=updated.message,
                level=TaskEventLevel.WARNING,
                data={"from": task.status.value, "to": target.value},
            )
            self.store.save_with_event(updated, event, expected_status=task.status)
        self._notify()
        return updated

    def is_cancel_requested(self, task_id: str) -> bool:
        with self._lock:
            runtime = self._runtime.get(task_id)
            if runtime is not None and runtime.cancel_event.is_set():
                return True
        return self.store.require(task_id).cancel_requested

    def update_progress(
        self,
        task_id: str,
        *,
        phase: str | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> TaskRecord:
        lock = self._task_lock(task_id)
        with lock:
            task = self.store.require(task_id)
            if task.status not in {TaskStatus.RUNNING, TaskStatus.CANCELLING}:
                raise InvalidTaskTransition(f"cannot update progress for {task.status.value} task")
            next_current = task.current if current is None else current
            next_total = task.total if total is None else total
            changed_at = utc_now_iso()
            updated = replace(
                task,
                phase=task.phase if phase is None else phase,
                current=next_current,
                total=next_total,
                progress_pct=normalized_progress(next_current, next_total),
                message=task.message if message is None else message,
                heartbeat_at=changed_at,
                updated_at=changed_at,
            )
            event = TaskEvent(
                task_id=task.id,
                type=TaskEventType.PROGRESS,
                created_at=changed_at,
                message=updated.message,
                data={
                    "phase": updated.phase,
                    "current": updated.current,
                    "total": updated.total,
                    "progress_pct": updated.progress_pct,
                },
            )
            self.store.save_with_event(updated, event, expected_status=task.status)
        self._notify()
        return updated

    def append_log(
        self,
        task_id: str,
        message: str,
        *,
        level: TaskEventLevel = TaskEventLevel.INFO,
    ) -> TaskEvent:
        lock = self._task_lock(task_id)
        with lock:
            task = self.store.require(task_id)
            if task.is_terminal:
                raise InvalidTaskTransition(f"cannot append log to {task.status.value} task")
            event = self.store.append_event(
                TaskEvent(
                    task_id=task_id,
                    type=TaskEventType.LOG,
                    created_at=utc_now_iso(),
                    message=message,
                    level=level,
                )
            )
        self._notify()
        return event

    def wait(self, task_id: str, *, timeout: float = 30.0) -> TaskRecord:
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                task = self.store.require(task_id)
                if task.is_terminal:
                    return task
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"task did not finish within {timeout}s: {task_id}")
                self._condition.wait(timeout=remaining)

    def recover_interrupted(self) -> int:
        recovered = 0
        active_statuses = (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.CANCELLING)
        while True:
            tasks = self.store.list(TaskQuery(statuses=active_statuses, limit=200))
            if not tasks:
                return recovered
            for task in tasks:
                lock = self._task_lock(task.id)
                with lock:
                    current = self.store.require(task.id)
                    if current.status not in active_statuses:
                        continue
                    changed_at = utc_now_iso()
                    updated = transition_task(
                        current,
                        TaskStatus.INTERRUPTED,
                        at=changed_at,
                        message="应用重启，任务执行已中断",
                    )
                    event = TaskEvent(
                        task_id=current.id,
                        type=TaskEventType.STATUS,
                        created_at=changed_at,
                        message=updated.message,
                        level=TaskEventLevel.WARNING,
                        data={"from": current.status.value, "to": TaskStatus.INTERRUPTED.value},
                    )
                    try:
                        self.store.save_with_event(updated, event, expected_status=current.status)
                    except TaskUpdateConflictError:
                        continue
                    recovered += 1

    def shutdown(self, *, timeout: float = 5.0) -> None:
        with self._condition:
            self._closed = True
            task_ids = list(self._runtime)
        for task_id in task_ids:
            try:
                self.request_cancel(task_id)
            except TaskNotCancellableError:
                continue
        deadline = time.monotonic() + max(0.0, timeout)
        for task_id in task_ids:
            with self._lock:
                runtime = self._runtime.get(task_id)
            if runtime is None:
                continue
            runtime.thread.join(timeout=max(0.0, deadline - time.monotonic()))
            if runtime.thread.is_alive():
                self._interrupt_active_task(task_id, "应用关闭，任务执行已中断")

    def runtime_task_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._runtime)

    def _run(self, task_id: str, cancel_event: threading.Event) -> None:
        semaphore: threading.BoundedSemaphore | None = None
        try:
            registration = self.registry.require(self.store.require(task_id).kind)
            task = self.store.require(task_id)
            key = registration.key_for(task)
            if key is not None:
                semaphore = self._semaphore_for(key, registration.max_concurrency)
                semaphore.acquire()
            task = self.store.require(task_id)
            if task.is_terminal:
                return
            if cancel_event.is_set():
                self.request_cancel(task_id)
                return
            running = self._transition_status(task_id, TaskStatus.RUNNING)
            with self._lock:
                runtime = self._runtime.get(task_id)
                private_input = dict(runtime.private_input_data) if runtime is not None else {}
            context = TaskContext(
                task=running,
                input_data={**running.input_data, **private_input},
                reporter=TaskReporter(self, task_id),
                cancel_event=cancel_event,
            )
            result = registration.handler(context)
            if cancel_event.is_set():
                self._finish_cancelled(task_id)
            else:
                self._finish_succeeded(task_id, result or {})
        except TaskCancelled as e:
            self._finish_cancelled(task_id, message=str(e))
        except Exception as e:
            self._finish_failed(task_id, e)
        finally:
            if semaphore is not None:
                semaphore.release()
            self._maybe_cleanup()
            with self._condition:
                self._runtime.pop(task_id, None)
                self._condition.notify_all()

    def _transition_status(
        self,
        task_id: str,
        target: TaskStatus,
        *,
        message: str | None = None,
        level: TaskEventLevel = TaskEventLevel.INFO,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> TaskRecord:
        lock = self._task_lock(task_id)
        with lock:
            task = self.store.require(task_id)
            changed_at = utc_now_iso()
            updated = transition_task(
                task,
                target,
                at=changed_at,
                message=message,
                error_code=error_code,
                error_message=error_message,
            )
            event = TaskEvent(
                task_id=task.id,
                type=TaskEventType.STATUS,
                created_at=changed_at,
                message=updated.message,
                level=level,
                data={"from": task.status.value, "to": target.value},
            )
            self.store.save_with_event(updated, event, expected_status=task.status)
        self._notify()
        return updated

    def _finish_succeeded(self, task_id: str, result: dict[str, Any]) -> TaskRecord:
        lock = self._task_lock(task_id)
        with lock:
            task = self.store.require(task_id)
            if task.is_terminal:
                return task
            changed_at = utc_now_iso()
            completed = transition_task(task, TaskStatus.SUCCEEDED, at=changed_at, message="任务完成")
            updated = replace(
                completed,
                current=completed.total if completed.total > 0 else completed.current,
                progress_pct=100.0 if completed.total > 0 else completed.progress_pct,
                result_summary=sanitize_task_payload(dict(result)),
            )
            event = TaskEvent(
                task_id=task.id,
                type=TaskEventType.STATUS,
                created_at=changed_at,
                message=updated.message,
                data={"from": task.status.value, "to": TaskStatus.SUCCEEDED.value},
            )
            self.store.save_with_event(updated, event, expected_status=task.status)
        self._notify()
        return updated

    def _finish_cancelled(self, task_id: str, *, message: str = "任务已取消") -> TaskRecord:
        task = self.store.require(task_id)
        if task.is_terminal:
            return task
        return self._transition_status(task_id, TaskStatus.CANCELLED, message=message, level=TaskEventLevel.WARNING)

    def _finish_failed(self, task_id: str, error: Exception) -> TaskRecord:
        task = self.store.require(task_id)
        if task.is_terminal:
            return task
        with self._lock:
            runtime = self._runtime.get(task_id)
            cancelled = runtime is not None and runtime.cancel_event.is_set()
        if cancelled:
            return self._finish_cancelled(task_id, message=str(error) or "任务已取消")
        return self._transition_status(
            task_id,
            TaskStatus.FAILED,
            message=str(error) or error.__class__.__name__,
            level=TaskEventLevel.ERROR,
            error_code="worker_error",
            error_message=str(error) or error.__class__.__name__,
        )

    def _semaphore_for(self, key: str, limit: int) -> threading.BoundedSemaphore:
        with self._lock:
            existing_limit = self._semaphore_limits.get(key)
            if existing_limit is not None and existing_limit != limit:
                raise TaskConcurrencyPolicyError(
                    f"concurrency key {key!r} uses conflicting limits: {existing_limit} and {limit}"
                )
            self._semaphore_limits[key] = limit
            return self._semaphores.setdefault(key, threading.BoundedSemaphore(limit))

    def _interrupt_active_task(self, task_id: str, message: str) -> TaskRecord:
        lock = self._task_lock(task_id)
        with lock:
            task = self.store.require(task_id)
            if task.is_terminal:
                return task
            changed_at = utc_now_iso()
            updated = transition_task(task, TaskStatus.INTERRUPTED, at=changed_at, message=message)
            event = TaskEvent(
                task_id=task.id,
                type=TaskEventType.STATUS,
                created_at=changed_at,
                message=message,
                level=TaskEventLevel.WARNING,
                data={"from": task.status.value, "to": TaskStatus.INTERRUPTED.value},
            )
            self.store.save_with_event(updated, event, expected_status=task.status)
        self._notify()
        return updated

    def _task_lock(self, task_id: str) -> threading.Lock:
        with self._lock:
            return self._task_locks.setdefault(task_id, threading.Lock())

    def _notify(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def _maybe_cleanup(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup_monotonic < self._cleanup_interval_sec:
                return
            self._last_cleanup_monotonic = now
        deleted = self.store.cleanup(
            retention_days=self._retention_days,
            max_terminal_tasks=self._max_terminal_tasks,
        )
        with self._lock:
            self.cleanup_deleted_total += deleted

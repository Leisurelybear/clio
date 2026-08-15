from __future__ import annotations

import threading
import time

import pytest

from clio.task_center.executor import (
    TaskExecutorRegistry,
    TaskHandlerAlreadyRegisteredError,
    TaskHandlerNotRegisteredError,
)
from clio.task_center.manager import (
    TaskConcurrencyPolicyError,
    TaskManager,
    TaskManagerClosedError,
    TaskNotCancellableError,
)
from clio.task_center.models import TaskEventLevel, TaskEventType, TaskKind, TaskStatus, create_task
from clio.task_center.reporter import TaskCancelled
from clio.task_center.state_machine import transition_task
from clio.task_center.store import TaskStore


def _manager(tmp_path, *, recover_on_start=True):
    return TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=recover_on_start)


def test_registry_requires_one_handler_per_kind():
    registry = TaskExecutorRegistry()

    def handler(context):
        return None

    registry.register(TaskKind.PIPELINE, handler)

    with pytest.raises(TaskHandlerAlreadyRegisteredError):
        registry.register(TaskKind.PIPELINE, handler)
    with pytest.raises(TaskHandlerNotRegisteredError):
        registry.require(TaskKind.RERUN)


def test_submit_runs_handler_and_persists_result_and_status_events(tmp_path):
    manager = _manager(tmp_path)

    def handler(context):
        assert context.input_data == {"steps": ["analyze"]}
        context.reporter.progress(phase="analyze", current=1, total=2, message="分析中")
        context.reporter.log("已上传")
        return {"output": "plan.json"}

    manager.register(TaskKind.PIPELINE, handler, cancellable=True)
    submitted = manager.submit(
        TaskKind.PIPELINE,
        "处理素材",
        task_id="task-1",
        input_data={"steps": ["analyze"]},
        input_summary={"step_count": 1},
    )

    finished = manager.wait(submitted.id)

    assert finished.status is TaskStatus.SUCCEEDED
    assert finished.phase == "analyze"
    assert finished.progress_pct == 50.0
    assert finished.result_summary == {"output": "plan.json"}
    events = manager.store.events(task_id=submitted.id)
    assert [event.type for event in events] == [
        TaskEventType.CREATED,
        TaskEventType.STATUS,
        TaskEventType.PROGRESS,
        TaskEventType.LOG,
        TaskEventType.STATUS,
    ]


def test_handler_failure_is_persisted(tmp_path):
    manager = _manager(tmp_path)

    def handler(context):
        raise RuntimeError("AI 请求失败")

    manager.register(TaskKind.PIPELINE, handler)
    task = manager.submit(TaskKind.PIPELINE, "处理素材")

    failed = manager.wait(task.id)

    assert failed.status is TaskStatus.FAILED
    assert failed.error_code == "worker_error"
    assert failed.error_message == "AI 请求失败"


def test_running_task_can_be_cancelled_cooperatively(tmp_path):
    manager = _manager(tmp_path)
    entered = threading.Event()

    def handler(context):
        entered.set()
        while True:
            context.reporter.raise_if_cancelled()
            time.sleep(0.005)

    manager.register(TaskKind.PIPELINE, handler, cancellable=True)
    task = manager.submit(TaskKind.PIPELINE, "处理素材")
    assert entered.wait(timeout=2)

    cancelling = manager.request_cancel(task.id)
    cancelled = manager.wait(task.id)

    assert cancelling.status is TaskStatus.CANCELLING
    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.cancel_requested is True


def test_non_cancellable_task_rejects_cancel(tmp_path):
    manager = _manager(tmp_path)
    release = threading.Event()

    def handler(context):
        release.wait(timeout=2)

    manager.register(TaskKind.PIPELINE, handler)
    task = manager.submit(TaskKind.PIPELINE, "处理素材")

    with pytest.raises(TaskNotCancellableError):
        manager.request_cancel(task.id)

    release.set()
    assert manager.wait(task.id).status is TaskStatus.SUCCEEDED


def test_queued_task_can_be_cancelled_while_waiting_for_concurrency_slot(tmp_path):
    manager = _manager(tmp_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []

    def handler(context):
        calls.append(context.task.id)
        if context.task.id == "first":
            first_entered.set()
            release_first.wait(timeout=2)

    manager.register(
        TaskKind.PIPELINE,
        handler,
        concurrency_key="project-run",
        max_concurrency=1,
        cancellable=True,
    )
    first = manager.submit(TaskKind.PIPELINE, "一", task_id="first")
    assert first_entered.wait(timeout=2)
    second = manager.submit(TaskKind.PIPELINE, "二", task_id="second")

    cancelled = manager.request_cancel(second.id)
    release_first.set()

    assert cancelled.status is TaskStatus.CANCELLED
    assert manager.wait(first.id).status is TaskStatus.SUCCEEDED
    assert manager.wait(second.id).status is TaskStatus.CANCELLED
    assert calls == ["first"]


def test_dynamic_concurrency_key_serializes_same_project_only(tmp_path):
    manager = _manager(tmp_path)
    active = 0
    max_active = 0
    lock = threading.Lock()
    release = threading.Event()
    two_entered = threading.Event()

    def handler(context):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                two_entered.set()
        release.wait(timeout=2)
        with lock:
            active -= 1

    manager.register(
        TaskKind.WAVEFORM,
        handler,
        concurrency_key=lambda task: "waveform",
        max_concurrency=2,
    )
    tasks = [manager.submit(TaskKind.WAVEFORM, str(index)) for index in range(3)]
    assert two_entered.wait(timeout=2)
    release.set()

    assert all(manager.wait(task.id).status is TaskStatus.SUCCEEDED for task in tasks)
    assert max_active == 2


def test_conflicting_limits_for_same_concurrency_key_fail_task(tmp_path):
    manager = _manager(tmp_path)
    manager.register(TaskKind.PIPELINE, lambda context: None, concurrency_key="shared", max_concurrency=1)
    manager.register(TaskKind.RERUN, lambda context: None, concurrency_key="shared", max_concurrency=2)
    first = manager.submit(TaskKind.PIPELINE, "一")
    assert manager.wait(first.id).status is TaskStatus.SUCCEEDED

    second = manager.submit(TaskKind.RERUN, "二")
    failed = manager.wait(second.id)

    assert failed.status is TaskStatus.FAILED
    assert "conflicting limits" in (failed.error_message or "")


def test_recovery_marks_leftover_active_tasks_interrupted(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    queued = create_task(TaskKind.PIPELINE, "排队", task_id="queued", cancellable=True)
    store.create(queued)
    running = transition_task(
        create_task(TaskKind.RERUN, "运行", task_id="running", cancellable=True),
        TaskStatus.RUNNING,
    )
    store.create(replace_for_store_as_queued(running))
    store.save_with_event(
        running,
        status_event(running.id, "queued", "running"),
        expected_status=TaskStatus.QUEUED,
    )

    manager = TaskManager(store)

    assert manager.store.require(queued.id).status is TaskStatus.INTERRUPTED
    assert manager.store.require(running.id).status is TaskStatus.INTERRUPTED


def replace_for_store_as_queued(task):
    return create_task(
        task.kind,
        task.title,
        task_id=task.id,
        created_at=task.created_at,
        cancellable=task.cancellable,
    )


def status_event(task_id, old, new):
    from clio.task_center.models import TaskEvent

    return TaskEvent(
        task_id=task_id,
        type=TaskEventType.STATUS,
        created_at="2026-08-16T00:00:01.000Z",
        data={"from": old, "to": new},
    )


def test_retry_creates_new_linked_task_with_same_safe_input(tmp_path):
    manager = _manager(tmp_path)
    calls = 0

    def handler(context):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("first failed")
        return {"ok": True}

    manager.register(TaskKind.RERUN, handler)
    original = manager.submit(TaskKind.RERUN, "重新分析", input_data={"video": "001.mp4"})
    assert manager.wait(original.id).status is TaskStatus.FAILED

    retry = manager.retry(original.id, new_task_id="retry")
    finished = manager.wait(retry.id)

    assert finished.status is TaskStatus.SUCCEEDED
    assert finished.retry_of == original.id
    assert finished.input_data == {"video": "001.mp4"}


def test_reporter_log_level_is_preserved(tmp_path):
    manager = _manager(tmp_path)

    def handler(context):
        context.reporter.log("注意", level=TaskEventLevel.WARNING)

    manager.register(TaskKind.PIPELINE, handler)
    task = manager.submit(TaskKind.PIPELINE, "处理素材")
    manager.wait(task.id)

    log_event = next(event for event in manager.store.events(task_id=task.id) if event.type is TaskEventType.LOG)
    assert log_event.level is TaskEventLevel.WARNING


def test_shutdown_rejects_new_tasks_and_cancels_cooperative_workers(tmp_path):
    manager = _manager(tmp_path)
    entered = threading.Event()

    def handler(context):
        entered.set()
        while not context.cancel_event.wait(timeout=0.01):
            pass
        raise TaskCancelled("关闭时取消")

    manager.register(TaskKind.PIPELINE, handler, cancellable=True)
    task = manager.submit(TaskKind.PIPELINE, "处理素材")
    assert entered.wait(timeout=2)

    manager.shutdown(timeout=2)

    assert manager.store.require(task.id).status is TaskStatus.CANCELLED
    with pytest.raises(TaskManagerClosedError):
        manager.submit(TaskKind.PIPELINE, "新任务")


def test_shutdown_marks_uncooperative_worker_interrupted(tmp_path):
    manager = _manager(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def handler(context):
        entered.set()
        release.wait(timeout=2)

    manager.register(TaskKind.PIPELINE, handler)
    task = manager.submit(TaskKind.PIPELINE, "处理素材")
    assert entered.wait(timeout=2)

    manager.shutdown(timeout=0)

    assert manager.store.require(task.id).status is TaskStatus.INTERRUPTED
    release.set()


def test_progress_rejects_negative_values(tmp_path):
    manager = _manager(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def handler(context):
        entered.set()
        release.wait(timeout=2)

    manager.register(TaskKind.PIPELINE, handler)
    task = manager.submit(TaskKind.PIPELINE, "处理素材")
    assert entered.wait(timeout=2)

    with pytest.raises(ValueError):
        manager.update_progress(task.id, current=-1, total=1)

    release.set()
    manager.wait(task.id)


def test_semaphore_policy_error_type_is_public():
    assert issubclass(TaskConcurrencyPolicyError, RuntimeError)

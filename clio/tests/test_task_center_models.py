from __future__ import annotations

from dataclasses import replace

import pytest

from clio.task_center.models import (
    TaskEvent,
    TaskEventType,
    TaskKind,
    TaskRecord,
    TaskStatus,
    TaskVisibility,
    create_task,
    normalized_progress,
)
from clio.task_center.state_machine import InvalidTaskTransition, can_transition, transition_task


def test_create_task_has_stable_queued_defaults():
    task = create_task(
        TaskKind.PIPELINE,
        "处理素材",
        task_id="task-1",
        created_at="2026-08-16T00:00:00.000Z",
        project_name="东京",
        cancellable=True,
    )

    assert task.id == "task-1"
    assert task.status is TaskStatus.QUEUED
    assert task.visibility is TaskVisibility.FOREGROUND
    assert task.current == 0
    assert task.total == 0
    assert task.progress_pct is None
    assert task.is_terminal is False


def test_public_task_dict_does_not_expose_private_execution_inputs():
    task = create_task(
        TaskKind.RERUN,
        "重新分析",
        project_path="G:/private/project",
        input_data={"api_key": "secret", "video": "001.mp4"},
        input_summary={"video": "001.mp4"},
    )

    public = task.to_dict()
    private = task.to_dict(include_private=True)

    assert "project_path" not in public
    assert "input_data" not in public
    assert private["project_path"] == "G:/private/project"
    assert private["input_data"]["api_key"] == "secret"


@pytest.mark.parametrize(
    ("current", "total", "expected"),
    [(0, 0, None), (0, 10, 0.0), (3, 10, 30.0), (12, 10, 100.0)],
)
def test_normalized_progress(current, total, expected):
    assert normalized_progress(current, total) == expected


@pytest.mark.parametrize(("current", "total"), [(-1, 1), (0, -1)])
def test_normalized_progress_rejects_negative_values(current, total):
    with pytest.raises(ValueError):
        normalized_progress(current, total)


def test_terminal_task_requires_finished_at():
    task = create_task(TaskKind.PIPELINE, "处理素材")

    with pytest.raises(ValueError, match="finished_at"):
        replace(task, status=TaskStatus.SUCCEEDED)


def test_task_event_requires_positive_sequence():
    with pytest.raises(ValueError, match="positive"):
        TaskEvent(
            seq=0,
            task_id="task-1",
            type=TaskEventType.LOG,
            created_at="2026-08-16T00:00:00.000Z",
        )


def test_running_to_cancelling_to_cancelled_sets_lifecycle_fields():
    task = create_task(TaskKind.PIPELINE, "处理素材", task_id="task-1", cancellable=True)
    running = transition_task(task, TaskStatus.RUNNING, at="2026-08-16T00:00:01.000Z")
    cancelling = transition_task(
        running,
        TaskStatus.CANCELLING,
        at="2026-08-16T00:00:02.000Z",
        message="正在取消",
    )
    cancelled = transition_task(cancelling, TaskStatus.CANCELLED, at="2026-08-16T00:00:03.000Z")

    assert running.started_at == "2026-08-16T00:00:01.000Z"
    assert cancelling.cancel_requested is True
    assert cancelled.finished_at == "2026-08-16T00:00:03.000Z"
    assert cancelled.is_terminal is True


def test_failed_task_captures_structured_error():
    task = transition_task(create_task(TaskKind.CUT_EXPORT, "导出剪辑"), TaskStatus.RUNNING)
    failed = transition_task(
        task,
        TaskStatus.FAILED,
        message="ffmpeg 退出",
        error_code="ffmpeg_failed",
        error_message="exit 1",
    )

    assert failed.error_code == "ffmpeg_failed"
    assert failed.error_message == "exit 1"
    assert failed.finished_at is not None


def test_terminal_tasks_cannot_be_restarted():
    task = transition_task(create_task(TaskKind.PIPELINE, "处理素材"), TaskStatus.RUNNING)
    done = transition_task(task, TaskStatus.SUCCEEDED)

    assert can_transition(done.status, TaskStatus.RUNNING) is False
    with pytest.raises(InvalidTaskTransition, match="succeeded -> running"):
        transition_task(done, TaskStatus.RUNNING)


def test_retry_is_a_new_queued_task_linked_to_original():
    original = transition_task(create_task(TaskKind.RERUN, "重新分析", task_id="old"), TaskStatus.RUNNING)
    original = transition_task(original, TaskStatus.FAILED, message="失败")
    retry = create_task(TaskKind.RERUN, "重新分析", task_id="new", retry_of=original.id)

    assert retry.id != original.id
    assert retry.retry_of == original.id
    assert retry.status is TaskStatus.QUEUED


def test_task_record_rejects_self_reference():
    with pytest.raises(ValueError, match="reference itself"):
        TaskRecord(
            id="same",
            kind=TaskKind.PIPELINE,
            status=TaskStatus.QUEUED,
            title="处理素材",
            created_at="2026-08-16T00:00:00.000Z",
            parent_id="same",
        )

from __future__ import annotations

import threading

import pytest

from clio.task_center.executor import project_artifact_key
from clio.task_center.manager import TaskAlreadyRunningError, TaskManager
from clio.task_center.models import TaskKind, create_task
from clio.task_center.store import TaskStore
from clio.ui.routes.export import _ensure_export_handler
from clio.ui.routes.plan import _ensure_cut_handler
from clio.ui.routes.run import _ensure_run_handlers


def test_project_artifact_key_prefers_id_and_falls_back_to_path():
    with_id = create_task(TaskKind.PIPELINE, "pipeline", project_id="project-1", project_path="/fallback")
    path_only = create_task(TaskKind.PIPELINE, "pipeline", project_path="/fallback")
    unscoped = create_task(TaskKind.PIPELINE, "pipeline")

    assert project_artifact_key(with_id) == "project-artifacts:project-1"
    assert project_artifact_key(path_only) == "project-artifacts:/fallback"
    assert project_artifact_key(unscoped) is None


def test_cross_kind_submit_rejects_shared_project_artifact_key(tmp_path):
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
    entered = threading.Event()
    release = threading.Event()

    def handler(context):
        entered.set()
        release.wait(timeout=2)

    manager.register(
        TaskKind.PIPELINE,
        handler,
        concurrency_key=project_artifact_key,
        max_concurrency=1,
    )
    manager.register(
        TaskKind.CUT_EXPORT,
        handler,
        concurrency_key=project_artifact_key,
        max_concurrency=1,
    )
    first = manager.submit(TaskKind.PIPELINE, "pipeline", project_id="project-1", reject_if_active=True)
    assert entered.wait(timeout=2)

    with pytest.raises(TaskAlreadyRunningError):
        manager.submit(TaskKind.CUT_EXPORT, "cut", project_id="project-1", reject_if_active=True)

    release.set()
    assert manager.wait(first.id).status.value == "succeeded"


def test_route_handlers_share_the_same_project_artifact_key(tmp_path):
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"), recover_on_start=False)
    _ensure_run_handlers(manager)
    _ensure_cut_handler(manager)
    _ensure_export_handler(manager)

    tasks = [
        create_task(TaskKind.PIPELINE, "pipeline", project_id="project-1"),
        create_task(TaskKind.RERUN, "rerun", project_id="project-1"),
        create_task(TaskKind.CUT_EXPORT, "cut", project_id="project-1"),
        create_task(
            TaskKind.EXPORT,
            "export",
            project_id="project-1",
            input_data={"day": "day1", "format": "jianying"},
        ),
    ]

    keys = {manager.registry.require(task.kind).key_for(task) for task in tasks}

    assert keys == {"project-artifacts:project-1"}

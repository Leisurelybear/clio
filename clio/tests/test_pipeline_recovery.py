"""Managed pipeline failures and cache-based continuation through production routes."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clio.config import AppConfig, GlobalConfig, ProjectConfig, ProviderConfig
from clio.task_center import TaskKind, TaskManager, TaskStatus, TaskStore, create_task
from clio.tasks._video_loader import save_selected_videos
from clio.tasks.analyze import AnalyzeResult
from clio.tasks.compress import run_compress_all
from clio.ui.routes.run import _ensure_run_handlers, handle_post_run_start
from clio.ui.services.run_recovery import pipeline_config_fingerprint


@pytest.fixture
def managed(tmp_path, monkeypatch):
    project = ProjectConfig()
    project.paths.output_dir = tmp_path / "output"
    cfg = AppConfig(global_cfg=GlobalConfig(), project_cfg=project, project_dir=tmp_path)
    manager = TaskManager(TaskStore(tmp_path / "tasks.sqlite3"))
    handler = MagicMock()
    handler.config_path = None
    handler._resolve_project_dir.return_value = tmp_path
    handler._get_config.return_value = cfg
    handler._get_task_manager.return_value = manager
    monkeypatch.setattr("clio.ui.routes.run.load_config", lambda *a, **kw: copy.deepcopy(cfg))
    monkeypatch.setattr("clio.ui.routes.run.preflight_config_media_deps", lambda *a, **kw: {"ok": True})
    monkeypatch.setattr("clio.pipeline.auto_reindex_if_needed", lambda *a: None)
    yield cfg, manager, handler
    manager.shutdown()


def submit(handler, manager, **options):
    handle_post_run_start(handler, {}, options)
    payload = handler._send_json.call_args.args[0]
    assert payload["ok"], payload
    return manager.wait(payload["task_id"])


def test_partial_analysis_fails_and_preserves_counts_without_running_downstream(managed, monkeypatch):
    cfg, manager, handler = managed
    analysis = AnalyzeResult(failed=1)
    analysis.failure_details = "clip-B: invalid JSON"
    downstream = MagicMock()
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"analyze": lambda *a, **kw: analysis, "voiceover": downstream})
    task = submit(handler, manager, steps=["analyze", "voiceover"])
    assert task.status is TaskStatus.FAILED
    assert task.result_summary["error_count"] == 1
    assert task.result_summary["steps"] == [{"name": "analyze", "processed": 0, "failed": 1}]
    assert "clip-B: invalid JSON" in task.error_message
    downstream.assert_not_called()
    progress = json.loads((cfg.paths.output_dir / ".progress.json").read_text(encoding="utf-8"))
    assert progress["status"] == "error"


@pytest.mark.parametrize("restart", [False, True, "interrupted"])
def test_retry_after_partial_compression_reuses_success_and_clears_force(managed, monkeypatch, restart):
    cfg, manager, handler = managed
    cfg.analyze.skip_existing = False
    originals = [cfg.project_dir / "A.mp4", cfg.project_dir / "B.mp4"]
    for source in originals:
        source.write_bytes(b"source")
    save_selected_videos(cfg.project_dir, originals)
    calls = []

    def encode(source, target, config, **kwargs):
        calls.append(source.name)
        if source.name == "B.mp4" and calls.count("B.mp4") == 1:
            raise RuntimeError("encode failed")
        target.write_bytes(b"encoded" * 10_000)

    monkeypatch.setattr("clio.tasks.compress.compress_video", encode)
    monkeypatch.setattr("clio.tasks.compress.resolve_binary", lambda *a: "ffprobe")
    monkeypatch.setattr("clio.tasks.compress.get_duration_sec", lambda *a, **kw: 10)
    monkeypatch.setattr("clio.tasks.compress._sort_items_by_capture_time", lambda items, *a: items)
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"compress": run_compress_all})
    original = submit(handler, manager, steps=["compress"], overwrite=True)
    assert original.status is TaskStatus.FAILED
    assert calls == ["A.mp4", "B.mp4"]
    if restart:
        manager.shutdown()
        if restart == "interrupted":
            # Persist the state left by a terminated process after A committed.
            original = replace(
                create_task(
                    TaskKind.PIPELINE,
                    "interrupted compression",
                    project_id=original.project_id,
                    project_path=original.project_path,
                    input_data=original.input_data,
                    input_summary=original.input_summary,
                ),
                status=TaskStatus.RUNNING,
            )
            manager.store.create(original)
        manager = TaskManager(TaskStore(manager.store.path))
        _ensure_run_handlers(manager)
        if restart == "interrupted":
            assert manager.store.require(original.id).status is TaskStatus.INTERRUPTED
    try:
        retry = manager.wait(manager.retry(original.id).id)
        assert retry.status is TaskStatus.SUCCEEDED, retry.error_message
        assert retry.retry_of == original.id
        assert retry.input_data["overwrite"] is False
        assert retry.input_data["confirm_artifact_overwrite"] is False
        assert retry.input_data["resume"] is True
        assert calls == ["A.mp4", "B.mp4", "B.mp4"]
    finally:
        if restart:
            manager.shutdown()


def test_retry_rejects_configuration_drift_without_executing(managed, monkeypatch):
    cfg, manager, handler = managed
    worker = MagicMock(side_effect=RuntimeError("fail"))
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"compress": worker})
    task = submit(handler, manager, steps=["compress"], overwrite=True)
    cfg.project_cfg.compress.max_width += 2
    with pytest.raises(ValueError, match="配置或提示词已变化"):
        manager.retry(task.id)
    assert worker.call_count == 1


def test_retry_requires_fresh_overwrite_confirmation(managed, monkeypatch):
    cfg, manager, handler = managed
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"compress": MagicMock(side_effect=RuntimeError("fail"))})
    task = submit(handler, manager, steps=["compress"])
    monkeypatch.setattr("clio.ui.routes.run.artifact_overwrite_paths", lambda *a, **kw: [Path("manual.json")])
    with pytest.raises(ValueError, match="新的覆盖确认"):
        manager.retry(task.id)


def test_retry_rechecks_config_after_submission_before_executing(managed, monkeypatch):
    cfg, manager, handler = managed
    worker = MagicMock(side_effect=RuntimeError("fail"))
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"compress": worker})
    original = submit(handler, manager, steps=["compress"])
    loads = 0

    def load(*args, **kwargs):
        nonlocal loads
        loads += 1
        current = copy.deepcopy(cfg)
        if loads > 1:
            current.project_cfg.compress.max_width += 2
        return current

    monkeypatch.setattr("clio.ui.routes.run.load_config", load)
    retry = manager.wait(manager.retry(original.id).id)
    assert retry.status is TaskStatus.FAILED
    assert "配置或提示词已变化" in retry.error_message
    assert worker.call_count == 1


def test_retry_rejects_changed_source_selection(managed, monkeypatch):
    cfg, manager, handler = managed
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"compress": MagicMock(side_effect=RuntimeError("fail"))})
    task = submit(handler, manager, steps=["compress"])
    save_selected_videos(cfg.project_dir, [cfg.project_dir / "new-source.mp4"])
    with pytest.raises(ValueError, match="配置或提示词已变化"):
        manager.retry(task.id)


def test_legacy_retry_without_config_identity_requires_new_submission(managed, monkeypatch):
    cfg, manager, _ = managed
    _ensure_run_handlers(manager)
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"compress": MagicMock(side_effect=RuntimeError("fail"))})
    task = manager.submit(
        TaskKind.PIPELINE,
        "legacy",
        project_path=str(cfg.project_dir),
        input_data={"steps": ["compress"]},
        private_input_data={"_config": cfg},
    )
    manager.wait(task.id)
    with pytest.raises(ValueError, match="配置或提示词已变化"):
        manager.retry(task.id)


def test_config_identity_detects_prompt_change_but_allows_secret_rotation(managed):
    cfg, _, _ = managed
    cfg.global_cfg.ai.providers["test"] = ProviderConfig(name="test", type="gemini", api_key="first-secret")
    initial = pipeline_config_fingerprint(cfg)
    cfg.global_cfg.ai.providers["test"].api_key = "second-secret"
    assert pipeline_config_fingerprint(cfg) == initial
    template = cfg.project_dir / "templates" / "prompts" / "video_analyze.md"
    template.parent.mkdir(parents=True)
    template.write_text("changed instructions", encoding="utf-8")
    assert pipeline_config_fingerprint(cfg) != initial


def test_task_history_contains_only_config_digest_and_no_resolved_secret(managed, monkeypatch):
    cfg, manager, handler = managed
    cfg.global_cfg.ai.providers["test"] = ProviderConfig(name="test", type="gemini", api_key="private-value")
    monkeypatch.setattr("clio.pipeline._STEP_FUNCS", {"compress": lambda *a, **kw: []})
    task = submit(handler, manager, steps=["compress"])
    serialized = json.dumps(task.to_dict())
    assert "private-value" not in serialized
    assert len(task.input_data["run_config_fingerprint"]) == 64

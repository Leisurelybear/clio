"""Archived pre-fix probes; superseded by clio/tests/test_pipeline_recovery.py.

These assertions describe the review baseline, not current desired behavior.
See the adjacent README for current regression commands.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from clio.config.models import AppConfig, GlobalConfig, ProjectConfig
from clio.pipeline import run_pipeline_steps
from clio.processing_state import ProcessingState
from clio.task_center.manager import TaskManager
from clio.task_center.models import TaskKind, TaskStatus
from clio.task_center.store import TaskStore
from clio.tasks.analyze import AnalyzeResult
from clio.tasks.compress import _compress_settings_fingerprint, _find_reusable
from clio.ui.routes.run import _ensure_run_handlers
from clio.ui.services.run_preview import _compress_step
from clio.vmeta import VideoMeta


def config(root: Path) -> AppConfig:
    project = ProjectConfig()
    project.paths.output_dir = root / "output"
    return AppConfig(global_cfg=GlobalConfig(), project_cfg=project, project_dir=root)


def partial_failure(root: Path) -> dict:
    cfg = config(root)
    result = AnalyzeResult()
    result.failed = 1
    downstream = MagicMock(return_value=[])
    manager = TaskManager(TaskStore(root / "tasks.sqlite3"))
    _ensure_run_handlers(manager)
    try:
        with (
            patch("clio.pipeline.auto_reindex_if_needed"),
            patch("clio.pipeline._STEP_FUNCS", {"analyze": lambda *a, **kw: result, "voiceover": downstream}),
        ):
            task = manager.submit(
                TaskKind.PIPELINE,
                "Offline partial failure",
                project_id=str(root),
                project_path=str(root),
                input_data={"steps": ["analyze", "voiceover"]},
                private_input_data={"_config": cfg},
            )
            final = manager.wait(task.id)
        assert final.status is TaskStatus.SUCCEEDED
        assert final.result_summary["error_count"] == 1
        downstream.assert_called_once()
        return {"probe": "partial-failure", "status": final.status.value, "error_count": 1, "downstream_ran": True}
    finally:
        manager.shutdown()


def retry_inputs(root: Path) -> dict:
    initial = config(root)
    initial.project_cfg.compress.max_width = 640
    current = config(root)
    current.project_cfg.compress.max_width = 800
    calls = []

    def compress(cfg, *args, **kwargs):
        calls.append({"max_width": cfg.compress.max_width, "overwrite": kwargs.get("overwrite")})
        if len(calls) == 1:
            raise RuntimeError("Simulated failure after earlier output was committed")
        return []

    manager = TaskManager(TaskStore(root / "retry.sqlite3"))
    _ensure_run_handlers(manager)
    try:
        with (
            patch("clio.pipeline.auto_reindex_if_needed"),
            patch("clio.pipeline._STEP_FUNCS", {"compress": compress}),
            patch("clio.ui.routes.run.load_config", return_value=current),
        ):
            task = manager.submit(
                TaskKind.PIPELINE,
                "Offline retry",
                project_id=str(root),
                project_path=str(root),
                input_data={"steps": ["compress"], "overwrite": True},
                private_input_data={"_config": initial},
            )
            assert manager.wait(task.id).status is TaskStatus.FAILED
            retried = manager.retry(task.id)
            assert manager.wait(retried.id).status is TaskStatus.SUCCEEDED
        assert calls == [{"max_width": 640, "overwrite": True}, {"max_width": 800, "overwrite": True}]
        return {"probe": "retry-inputs", "calls": calls}
    finally:
        manager.shutdown()


def compression_preview(root: Path) -> dict:
    cfg = config(root)
    cfg.compressed_dir.mkdir(parents=True, exist_ok=True)
    source = root / "source.mp4"
    source.write_bytes(b"source")
    compressed = cfg.compressed_dir / "001_source.mp4"
    compressed.write_bytes(b"compressed")
    meta = VideoMeta.build(source, compressed, 10, 10, _compress_settings_fingerprint(cfg))
    meta.write(compressed)
    cfg.project_cfg.compress.max_width += 2
    preview = _compress_step(cfg, [source], force=False)
    with patch("clio.tasks.compress.get_duration_sec", return_value=10):
        runtime = _find_reusable(source, {f"src:{source.resolve()}": [(1, compressed, meta)]}, cfg, "ffprobe")
    assert preview["will_skip"] == 1 and runtime[1] is None
    return {"probe": "compression-preview", "preview_skips": 1, "runtime_reusable": False}


def empty_steps(root: Path) -> dict:
    calls = []
    with (
        patch("clio.pipeline.auto_reindex_if_needed"),
        patch("clio.pipeline._STEP_FUNCS", {"compress": lambda *a, **kw: calls.append("compress")}),
    ):
        run_pipeline_steps(config(root), steps=[], tracker=MagicMock())
    assert calls == ["compress"]
    return {"probe": "empty-steps", "requested": [], "executed": calls}


def deferred_state(root: Path) -> dict:
    first = ProcessingState(root / "state", flush_interval_sec=3600)
    first.mark("source", "compress", "running")
    first.mark("source", "compress", "done")
    second = ProcessingState(root / "state", flush_interval_sec=3600)
    second.mark("source", "transcribe", "done")
    first.flush()
    second.flush()
    saved = ProcessingState(root / "state").get_state()["files"]["source"]
    assert saved["compress"] == "running" and saved["transcribe"] == "done"
    return {"probe": "deferred-state", "persisted_compress": "running", "actual_compress": "done"}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="clio-pipeline-review-") as temp:
        root = Path(temp)
        results = [
            partial_failure(root),
            retry_inputs(root),
            compression_preview(root),
            empty_steps(root),
            deferred_state(root),
        ]
    print(json.dumps(results, indent=2))

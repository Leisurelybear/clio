"""Tests for clio/pipeline.py — run_pipeline_steps cancel propagation."""

from __future__ import annotations

from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest

from clio.pipeline import run_pipeline_steps


def _mock_config(tmp_path: Path):
    """Create a minimal AppConfig-like object."""
    cfg = MagicMock()
    cfg.paths.output_dir = tmp_path
    cfg._project_dir = tmp_path
    cfg.analyze.skip_existing = False
    cfg.ai.providers = {}
    cfg.ai.tasks = {}
    cfg.compress.target_size_mb = 5
    cfg.compress.max_width = 640
    cfg.compress.fps = 15
    cfg.compress.codec = "libx264"
    cfg.compress.remove_audio = True
    cfg.compress.crf = 23
    cfg.whisper.enabled = False
    return cfg


class TestRunPipelineStepsCancel:
    def test_cancel_stops_after_current_step(self):
        """cancel_event 设置后 pipeline 在步骤间退出"""
        config = _mock_config(Path(""))
        cancel_event = Event()
        calls: list[str] = []

        def _fake_compress(*a, **kw):
            calls.append("compress")
            cancel_event.set()
            return None

        def _fake_analyze(*a, **kw):
            calls.append("analyze")
            return None

        fake_funcs = {"compress": _fake_compress, "analyze": _fake_analyze}
        with (
            patch("clio.pipeline._STEP_FUNCS", fake_funcs),
            patch("builtins.print"),
            patch("clio.pipeline.timed", lambda msg: MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)),
        ):
            run_pipeline_steps(config, steps=["compress", "analyze"], cancel_event=cancel_event)

        # After compress sets cancel_event, analyze should NOT run
        assert "compress" in calls
        assert "analyze" not in calls, f"analyze should not run after cancel, got calls: {calls}"

    def test_cancel_event_passed_to_compress(self):
        """cancel_event 应传递给 compress step"""
        config = _mock_config(Path(""))
        cancel_event = Event()
        compress_kwargs: dict = {}

        def _compress_mock(*a, **kw):
            compress_kwargs.update(kw)
            return None

        fake_funcs = {"compress": _compress_mock}
        with (
            patch("clio.pipeline._STEP_FUNCS", fake_funcs),
            patch("builtins.print"),
            patch("clio.pipeline.timed", lambda msg: MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)),
        ):
            run_pipeline_steps(config, steps=["compress"], cancel_event=cancel_event)

        assert compress_kwargs.get("cancel_event") is cancel_event, (
            f"Expected cancel_event to be passed, got kwargs: {compress_kwargs}"
        )

    def test_cancel_event_passed_to_transcribe(self):
        """cancel_event 应传递给 transcribe step"""
        config = _mock_config(Path(""))
        config.whisper.enabled = True
        cancel_event = Event()
        transcribe_kwargs: dict = {}

        def _transcribe_mock(*a, **kw):
            transcribe_kwargs.update(kw)
            return 0

        fake_funcs = {"transcribe": _transcribe_mock}
        with (
            patch("clio.pipeline._STEP_FUNCS", fake_funcs),
            patch("builtins.print"),
            patch("clio.pipeline.timed", lambda msg: MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)),
        ):
            run_pipeline_steps(config, steps=["transcribe"], cancel_event=cancel_event)

        assert transcribe_kwargs.get("cancel_event") is cancel_event, (
            f"Expected cancel_event to be passed, got kwargs: {transcribe_kwargs}"
        )

    def test_cancel_event_passed_to_all_steps(self):
        """cancel_event 应传递给所有 step（U-005 统一后）"""
        config = _mock_config(Path(""))
        cancel_event = Event()
        all_kwargs: dict[str, dict] = {}

        def _make_mock(name):
            def _mock(*a, **kw):
                all_kwargs[name] = kw
                return None

            return _mock

        fake_funcs = {s: _make_mock(s) for s in ("analyze", "voiceover", "plan", "label")}
        with (
            patch("clio.pipeline._STEP_FUNCS", fake_funcs),
            patch("builtins.print"),
            patch("clio.pipeline.timed", lambda msg: MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)),
        ):
            run_pipeline_steps(config, steps=list(fake_funcs), cancel_event=cancel_event)

        for step in ("analyze", "voiceover", "plan", "label"):
            assert all_kwargs[step].get("cancel_event") is cancel_event, (
                f"{step} should receive cancel_event, got: {all_kwargs[step]}"
            )

    def test_files_kwarg_propagation(self):
        config = _mock_config(Path(""))
        all_kwargs: dict[str, dict] = {}

        def _make_mock(name):
            def _mock(*a, **kw):
                all_kwargs[name] = kw
                return None

            return _mock

        fake_funcs = {s: _make_mock(s) for s in ("compress", "analyze", "voiceover", "transcribe", "plan", "label")}
        with (
            patch("clio.pipeline._STEP_FUNCS", fake_funcs),
            patch("builtins.print"),
            patch("clio.pipeline.timed", lambda msg: MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)),
        ):
            run_pipeline_steps(config, steps=list(fake_funcs), files=["001_A", "002_B"])

        for step in ("compress", "analyze", "voiceover", "transcribe", "plan", "label"):
            assert all_kwargs[step].get("files") == ["001_A", "002_B"], (
                f"{step} should receive files kwarg, got: {all_kwargs[step]}"
            )

    def test_overwrite_kwarg_propagation(self):
        config = _mock_config(Path(""))
        all_kwargs: dict[str, dict] = {}

        def _make_mock(name):
            def _mock(*a, **kw):
                all_kwargs[name] = kw
                return None

            return _mock

        fake_funcs = {s: _make_mock(s) for s in ("compress", "analyze", "voiceover", "transcribe", "plan", "label")}
        with (
            patch("clio.pipeline._STEP_FUNCS", fake_funcs),
            patch("builtins.print"),
            patch("clio.pipeline.timed", lambda msg: MagicMock(__enter__=lambda s: None, __exit__=lambda *a: None)),
        ):
            run_pipeline_steps(config, steps=list(fake_funcs), overwrite=True)

        for step in ("compress", "analyze", "voiceover", "transcribe", "plan", "label"):
            assert all_kwargs[step].get("overwrite") is True, (
                f"{step} should receive overwrite=True, got: {all_kwargs[step]}"
            )

    def test_nonzero_step_result_fails_pipeline(self, tmp_path: Path):
        config = _mock_config(tmp_path)
        tracker = MagicMock()
        with patch("clio.pipeline._STEP_FUNCS", {"transcribe": lambda *args, **kwargs: 1}):
            with pytest.raises(RuntimeError, match="退出码 1"):
                run_pipeline_steps(config, steps=["transcribe"], tracker=tracker)
        tracker.done.assert_not_called()
        tracker.error.assert_called_once()

    def test_returns_structured_summary(self, tmp_path: Path):
        config = _mock_config(tmp_path)
        tracker = MagicMock()
        with patch("clio.pipeline._STEP_FUNCS", {"analyze": lambda *args, **kwargs: ["a", "b"]}):
            result = run_pipeline_steps(config, steps=["analyze"], tracker=tracker)
        assert result == {
            "steps": [{"name": "analyze", "processed": 2, "failed": 0}],
            "processed": 2,
            "warning_count": 0,
            "error_count": 0,
        }

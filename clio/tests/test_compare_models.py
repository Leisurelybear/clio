"""Tests for clio/tasks/compare_models.py — model comparison pure logic + orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from clio.ai.base import TaskName
from clio.config import AppConfig
from clio.config.models import TaskConfig
from clio.tasks.compare_models import (
    ModelSpec,
    _config_for_model,
    _md_cell,
    _render_report,
    _summarize_result,
    parse_model_specs,
    run_compare_models,
)


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    video = tmp_path / "GL010683.mp4"
    video.write_bytes(b"not-a-real-video")
    return video


class TestModelSpec:
    def test_label(self) -> None:
        assert ModelSpec(provider="gemini", model="gemini-2.5-flash").label == "gemini:gemini-2.5-flash"


class TestParseModelSpecs:
    def test_explicit_provider_model(self, loaded_config: AppConfig) -> None:
        specs = parse_model_specs(["gemini:gemini-2.5-flash", "deepseek:deepseek-chat"], loaded_config)
        assert specs == [
            ModelSpec(provider="gemini", model="gemini-2.5-flash"),
            ModelSpec(provider="deepseek", model="deepseek-chat"),
        ]

    def test_slash_separator(self, loaded_config: AppConfig) -> None:
        specs = parse_model_specs(["gemini/gemini-2.5-flash", "deepseek/deepseek-chat"], loaded_config)
        assert specs[0].label == "gemini:gemini-2.5-flash"

    def test_comma_separated_list(self, loaded_config: AppConfig) -> None:
        specs = parse_model_specs(["gemini:gemini-2.5-flash,deepseek:deepseek-chat"], loaded_config)
        assert len(specs) == 2

    def test_provider_with_default_model(self, loaded_config: AppConfig) -> None:
        specs = parse_model_specs(["gemini", "deepseek:deepseek-chat"], loaded_config)
        assert specs[0] == ModelSpec(provider="gemini", model="gemini-2.5-flash")

    def test_whitespace_ignored(self, loaded_config: AppConfig) -> None:
        specs = parse_model_specs(["  gemini:gemini-2.5-flash  ", " , deepseek:deepseek-chat"], loaded_config)
        assert len(specs) == 2

    def test_unknown_provider_raises(self, loaded_config: AppConfig) -> None:
        with pytest.raises(ValueError, match="未知 provider"):
            parse_model_specs(["nope:nope-model", "deepseek:deepseek-chat"], loaded_config)

    def test_bare_unknown_provider_raises(self, loaded_config: AppConfig) -> None:
        with pytest.raises(ValueError, match="未知 provider"):
            parse_model_specs(["unknown-provider", "deepseek:deepseek-chat"], loaded_config)

    def test_provider_without_models_raises(self, loaded_config: AppConfig) -> None:
        loaded_config.ai.providers["empty"] = type(loaded_config.ai.providers["gemini"])(name="empty", type="openai")
        with pytest.raises(ValueError, match="未配置 models"):
            parse_model_specs(["empty", "deepseek:deepseek-chat"], loaded_config)

    def test_empty_provider_or_model_raises(self, loaded_config: AppConfig) -> None:
        with pytest.raises(ValueError, match="模型格式无效"):
            parse_model_specs(["gemini:", "deepseek:deepseek-chat"], loaded_config)

    def test_empty_entirely_raises(self, loaded_config: AppConfig) -> None:
        with pytest.raises(ValueError, match="至少需要指定两个模型"):
            parse_model_specs(["gemini:gemini-2.5-flash"], loaded_config)

    def test_fewer_than_two_specs_raises(self, loaded_config: AppConfig) -> None:
        with pytest.raises(ValueError, match="至少需要指定两个模型"):
            parse_model_specs(["gemini:gemini-2.5-flash"], loaded_config)


class TestConfigForModel:
    def test_overrides_video_analyze_task(self, loaded_config: AppConfig) -> None:
        cfg = _config_for_model(loaded_config, ModelSpec(provider="deepseek", model="deepseek-chat"))
        assert cfg.project_cfg is not None
        task = cfg.project_cfg.ai.tasks[TaskName.VIDEO_ANALYZE.value]
        assert isinstance(task, TaskConfig)
        assert task.provider == "deepseek"
        assert task.model == "deepseek-chat"

    def test_creates_project_cfg_when_missing(self, loaded_config: AppConfig) -> None:
        cfg = loaded_config
        cfg.project_cfg = None
        result = _config_for_model(cfg, ModelSpec(provider="gemini", model="gemini-2.5-flash"))
        assert result.project_cfg is not None

    def test_does_not_mutate_original(self, loaded_config: AppConfig) -> None:
        original_task = loaded_config.project_cfg.ai.tasks[TaskName.VIDEO_ANALYZE.value]
        _config_for_model(loaded_config, ModelSpec(provider="deepseek", model="deepseek-chat"))
        assert loaded_config.project_cfg.ai.tasks[TaskName.VIDEO_ANALYZE.value] == original_task


class TestSummarizeResult:
    def test_full_result(self) -> None:
        summary = _summarize_result(
            {
                "title": "沙滩日落",
                "location": "三亚",
                "summary": "漫步",
                "mood": "放松",
                "suggested_use": "片头",
                "_confidence": 0.8,
                "timeline": [{"start": 0}, {"start": 1}],
                "highlights": [{"start": 2}],
            }
        )
        assert summary["title"] == "沙滩日落"
        assert summary["confidence"] == 0.8
        assert summary["timeline_count"] == 2
        assert summary["highlights_count"] == 1

    def test_missing_fields_defaults(self) -> None:
        summary = _summarize_result({})
        assert summary["title"] == ""
        assert summary["confidence"] == 0.0
        assert summary["timeline_count"] == 0
        assert summary["highlights_count"] == 0

    def test_none_timeline_counted_zero(self) -> None:
        summary = _summarize_result({"timeline": None, "highlights": None})
        assert summary["timeline_count"] == 0
        assert summary["highlights_count"] == 0


class TestMdCell:
    def test_escapes_pipe_and_newline(self) -> None:
        assert _md_cell("a|b\nc") == "a\\|b c"

    def test_non_string_coerced(self) -> None:
        assert _md_cell(3) == "3"


class TestRenderReport:
    def test_ok_and_error_rows(self) -> None:
        report = _render_report(
            Path("clip.mp4"),
            [
                {
                    "model": "gemini:g1",
                    "ok": True,
                    "summary": {
                        "title": "T",
                        "location": "L",
                        "mood": "M",
                        "suggested_use": "U",
                        "summary": "S",
                        "confidence": 0.5,
                        "timeline_count": 2,
                        "highlights_count": 1,
                    },
                },
                {"model": "deepseek:d1", "ok": False, "error": "boom"},
            ],
        )
        assert "# Model Compare: clip.mp4" in report
        assert "| gemini:g1 | ok | T | L | 0.5 | 2 | 1 |" in report
        assert "| deepseek:d1 | error | boom" in report
        assert "### gemini:g1" in report
        assert "- Title: T" in report
        assert "- Error: boom" in report


class TestRunCompareModels:
    def test_all_success_writes_files(self, video_file: Path, loaded_config: AppConfig, capsys) -> None:
        def fake_analyze(video_path, config, **kwargs):
            return {
                "title": "T",
                "location": "L",
                "summary": "S",
                "mood": "M",
                "suggested_use": "U",
                "_confidence": 0.9,
                "timeline": [],
                "highlights": [],
            }

        with patch("clio.tasks.compare_models.analyze_video", side_effect=fake_analyze) as mock_ai:
            code = run_compare_models(
                loaded_config,
                video_file,
                ["gemini:gemini-2.5-flash", "deepseek:deepseek-chat"],
                output_dir=video_file.parent / "out",
            )
        assert code == 0
        assert mock_ai.call_count == 2
        files = list((video_file.parent / "out").glob("*.json")) + list((video_file.parent / "out").glob("*.md"))
        assert len(files) == 2
        out = capsys.readouterr().out
        assert "=== Compare gemini:gemini-2.5-flash ===" in out
        assert "对比 JSON:" in out

    def test_partial_failure_returns_1(self, video_file: Path, loaded_config: AppConfig) -> None:
        def fake_analyze(video_path, config, **kwargs):
            if "deepseek" in config.project_cfg.ai.tasks[TaskName.VIDEO_ANALYZE.value].provider:
                raise RuntimeError("provider down")
            return {"title": "T", "timeline": [], "highlights": []}

        with patch("clio.tasks.compare_models.analyze_video", side_effect=fake_analyze):
            code = run_compare_models(
                loaded_config,
                video_file,
                ["gemini:gemini-2.5-flash", "deepseek:deepseek-chat"],
                output_dir=video_file.parent / "out",
            )
        assert code == 0
        payload_path = next((video_file.parent / "out").glob("*.json"))
        assert "provider down" in payload_path.read_text(encoding="utf-8")

    def test_all_fail_returns_1(self, video_file: Path, loaded_config: AppConfig) -> None:
        with patch(
            "clio.tasks.compare_models.analyze_video",
            side_effect=RuntimeError("always down"),
        ):
            code = run_compare_models(
                loaded_config,
                video_file,
                ["gemini:gemini-2.5-flash", "deepseek:deepseek-chat"],
                output_dir=video_file.parent / "out",
            )
        assert code == 1

    def test_missing_video_raises(self, tmp_path: Path, loaded_config: AppConfig) -> None:
        with pytest.raises(FileNotFoundError):
            run_compare_models(loaded_config, tmp_path / "nope.mp4", ["gemini:g1", "deepseek:d1"])

    def test_bad_specs_raises_before_any_call(self, video_file: Path, loaded_config: AppConfig) -> None:
        with patch("clio.tasks.compare_models.analyze_video") as mock_ai:
            with pytest.raises(ValueError, match="至少需要指定两个模型"):
                run_compare_models(loaded_config, video_file, ["gemini:g1"], output_dir=video_file.parent / "out")
        mock_ai.assert_not_called()

    def test_context_override_passed_through(self, video_file: Path, loaded_config: AppConfig) -> None:
        seen: list[dict] = []

        def fake_analyze(video_path, config, **kwargs):
            seen.append(kwargs)
            return {"title": "T", "timeline": [], "highlights": []}

        with patch("clio.tasks.compare_models.analyze_video", side_effect=fake_analyze):
            run_compare_models(
                loaded_config,
                video_file,
                ["gemini:gemini-2.5-flash", "deepseek:deepseek-chat"],
                output_dir=video_file.parent / "out",
                context_override="custom context",
            )
        assert all(s.get("context_override") == "custom context" for s in seen)

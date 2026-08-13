"""Tests for clio/analyze.py — pure functions and AI wrapper."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from clio.ai.base import AIResponse
from clio.analyze import _validate_analysis, _validate_plan, _validate_voiceover, _wrap_with_context, plan_daily_vlog


def _fake_config(context: str = "", context_override: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        ai=SimpleNamespace(context=context, debug_print_prompt=False),
        plan=SimpleNamespace(max_clips_per_day=10, target_duration_sec=300),
        script=SimpleNamespace(target_words=150),
        paths=SimpleNamespace(input_dir=Path("/tmp")),
        project_dir=None,
    )


class TestWrapWithContext:
    def test_no_context_returns_prompt_unchanged(self, monkeypatch):
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)
        result = _wrap_with_context("hello", _fake_config(""))
        assert result == "hello"

    def test_with_config_context(self, monkeypatch):
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)
        result = _wrap_with_context("hello", _fake_config("my context"))
        assert "my context" in result
        assert "hello" in result
        assert "背景与规范" in result

    def test_with_context_override(self, monkeypatch):
        monkeypatch.setattr("pathlib.Path.is_file", lambda self: False)
        result = _wrap_with_context("hello", _fake_config("base ctx"), context_override="override")
        assert "base ctx" in result
        assert "override" in result
        assert "hello" in result

    def test_trip_context_file_loaded(self, monkeypatch, tmp_path: Path):
        import clio.analyze as analyze_mod

        analyze_mod._trip_context_cache.clear()
        templates = tmp_path / "templates"
        templates.mkdir()
        ctx_file = templates / "trip_context.md"
        ctx_file.write_text("## Trip Context\n\nParis 2025", encoding="utf-8")
        cfg = _fake_config("")
        cfg.project_dir = tmp_path
        # Ensure property-style access works on SimpleNamespace/MagicMock fixtures
        if not hasattr(cfg, "project_dir") or cfg.project_dir is None:
            object.__setattr__(cfg, "project_dir", tmp_path)

        result = _wrap_with_context("hello", cfg)

        assert "Paris 2025" in result
        assert "hello" in result

    def test_config_context_and_trip_context_both(self, monkeypatch):
        """Both trip_context.md and config.ai.context should appear."""
        import clio.analyze as analyze_mod

        analyze_mod._trip_context_cache.clear()
        orig_is_file = Path.is_file
        orig_read_text = Path.read_text

        def mock_is_file(self):
            if self.name == "trip_context.md":
                return True
            return orig_is_file(self)

        def mock_read_text(self, **kw):
            if self.name == "trip_context.md":
                return "Trip: Paris"
            return orig_read_text(self, **kw)

        def mock_stat(self, *args, **kwargs):
            from unittest.mock import MagicMock

            st = MagicMock()
            st.st_mtime = 1234567890.0
            st.st_mode = 0o100644
            return st

        monkeypatch.setattr("pathlib.Path.is_file", mock_is_file)
        monkeypatch.setattr("pathlib.Path.read_text", mock_read_text)
        monkeypatch.setattr("pathlib.Path.stat", mock_stat)

        result = _wrap_with_context("prompt", _fake_config("user context"))
        assert "Trip: Paris" in result
        assert "user context" in result
        assert "prompt" in result


def test_analyze_video_uses_prompt_override(tmp_path, monkeypatch):
    from clio.analyze import analyze_video

    template_file = tmp_path / "templates" / "vlog_template.md"
    template_file.parent.mkdir(parents=True)
    template_file.write_text("template", encoding="utf-8")

    prompt_dir = tmp_path / "templates" / "prompts"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "video_analyze.md").write_text("override analyze prompt", encoding="utf-8")

    cfg = _fake_config()
    cfg._project_dir = tmp_path
    cfg.script.template_file = template_file
    provider = MagicMock(provider_id="mock")
    provider.analyze_video.return_value = AIResponse('{"title":"x","summary":"y","timeline":[]}')

    monkeypatch.setattr("clio.analyze.get_video_provider", lambda *a: (provider, "model"))
    result = analyze_video("clip.mp4", cfg)

    assert result["title"] == "x"
    _, prompt, _ = provider.analyze_video.call_args.args[:3]
    assert "override analyze prompt" in prompt


def test_validate_analysis_defaults_confidence():
    result = _validate_analysis({"title": "x", "summary": "y", "timeline": []}, "clip.mp4")

    assert result["_confidence"] == 0.0


def test_validate_voiceover_defaults_confidence():
    result = _validate_voiceover({"title": "x", "voiceover": "hello"}, "clip.mp4")

    assert result["_confidence"] == 0.0


def test_validate_plan_defaults_confidence():
    result = _validate_plan({"day_title": "day1", "sequence": []}, "day1")

    assert result["_confidence"] == 0.0


def test_validate_analysis_rejects_wrong_typed_fields():
    """Wrong-typed fields must be reset to defaults, not silently merged."""
    result = _validate_analysis(
        {
            "title": 123,
            "summary": ["not a string"],
            "timeline": "not-a-list",
            "highlights": {"oops": 1},
            "_confidence": "abc",
        },
        "clip.mp4",
    )
    assert result["title"] == "clip"
    assert result["summary"] == ""
    assert result["timeline"] == []
    assert result["highlights"] == []
    assert result["_confidence"] == 0.0


def test_validate_analysis_keeps_valid_types():
    result = _validate_analysis(
        {"title": "A", "summary": "s", "timeline": [{"start": "00:01", "text": "x"}], "_confidence": 0.8},
        "clip.mp4",
    )
    assert result["title"] == "A"
    assert result["timeline"] == [{"start": "00:01", "text": "x"}]
    assert result["_confidence"] == 0.8


def test_validate_plan_rejects_wrong_typed_fields():
    result = _validate_plan(
        {
            "day_title": 99,
            "theme": ["x"],
            "total_estimated_sec": "oops",
            "sequence": "not-a-list",
            "opening_tip": 1.5,
        },
        "day1",
    )
    assert result["day_title"] == "day1"
    assert result["theme"] == ""
    assert result["total_estimated_sec"] == 180.0
    assert result["sequence"] == []
    assert result["opening_tip"] == ""


def test_validate_plan_filters_non_dict_sequence_items():
    result = _validate_plan(
        {
            "day_title": "day1",
            "sequence": [
                {"index": "001", "title": "A"},
                42,
                "bad",
                {"index": "002", "title": "B"},
            ],
        },
        "day1",
    )
    assert [s.get("index") for s in result["sequence"]] == ["001", "002"]


def test_validate_voiceover_rejects_wrong_typed_fields():
    result = _validate_voiceover(
        {"title": 1, "voiceover": None, "duration_hint_sec": "long", "_confidence": []}, "clip.mp4"
    )
    assert result["title"] == "clip"
    assert result["voiceover"] == ""
    assert result["duration_hint_sec"] == 20.0
    assert result["_confidence"] == 0.0


class TestPlanDailyVlog:
    def test_filter_valid_indices(self, monkeypatch):
        """Valid indices should be kept, invalid ones filtered."""
        clips = [
            {"index": "001", "title": "A"},
            {"index": "003", "title": "B"},
            {"index": "005", "title": "C"},
        ]
        mock_result = {
            "sequence": [
                {"index": "001", "description": "clip A"},
                {"index": "002", "description": "DNE"},  # not in clips
                {"index": "003", "description": "clip B"},
                {"index": "999", "description": "DNE"},
            ]
        }

        def make_config():
            cfg = _fake_config()
            cfg.ai.providers = {}
            return cfg

        monkeypatch.setattr("clio.analyze.get_task_provider", lambda *a: (MagicMock(), "deepseek-chat"))
        monkeypatch.setattr("clio.analyze._wrap_with_context", lambda prompt, cfg, **kw: prompt)
        monkeypatch.setattr("clio.analyze._call_ai", lambda *a, **kw: json.dumps(mock_result))

        result = plan_daily_vlog(clips, make_config())

        assert len(result["sequence"]) == 2
        assert result["sequence"][0]["index"] == "001"
        assert result["sequence"][1]["index"] == "003"

    def test_filter_int_indices_compatibility(self, monkeypatch):
        """Integer indices should be handled too (001 == 1)."""
        clips = [{"index": 1}, {"index": 3}]
        mock_result = {"sequence": [{"index": "001"}, {"index": 3}, {"index": "005"}]}
        cfg = _fake_config()
        cfg.ai.providers = {}
        monkeypatch.setattr("clio.analyze.get_task_provider", lambda *a: (MagicMock(), "model"))
        monkeypatch.setattr("clio.analyze._wrap_with_context", lambda prompt, cfg, **kw: prompt)
        monkeypatch.setattr("clio.analyze._call_ai", lambda *a, **kw: json.dumps(mock_result))

        result = plan_daily_vlog(clips, cfg)

        assert len(result["sequence"]) == 2

    def test_filter_empty_sequence(self, monkeypatch):
        """Empty sequence should pass through."""
        monkeypatch.setattr("clio.analyze.get_task_provider", lambda *a: (MagicMock(), "model"))
        monkeypatch.setattr("clio.analyze._wrap_with_context", lambda *a, **kw: "prompt")
        monkeypatch.setattr("clio.analyze._call_ai", lambda *a, **kw: '{"sequence": []}')
        cfg = _fake_config()
        cfg.ai.providers = {}
        result = plan_daily_vlog([{"index": "001", "title": "A"}], cfg)
        assert "sequence" in result
        assert result["sequence"] == []

    def test_no_sequence_key(self, monkeypatch):
        """If AI returns no sequence key, no crash."""
        monkeypatch.setattr("clio.analyze.get_task_provider", lambda *a: (MagicMock(), "model"))
        monkeypatch.setattr("clio.analyze._wrap_with_context", lambda *a, **kw: "prompt")
        monkeypatch.setattr("clio.analyze._call_ai", lambda *a, **kw: '{"title": "plan"}')
        cfg = _fake_config()
        cfg.ai.providers = {}
        result = plan_daily_vlog([{"index": "001", "title": "A"}], cfg)
        assert result["title"] == "plan"

"""Tests for plan generation (plan_daily_vlog) and plan task orchestration."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from clio.config import AppConfig
from clio.tasks.plan import (
    _analysis_day_label,
    _discover_day_labels,
    _plan_lineage_fingerprint,
    _source_inputs_from_clips,
    run_plan_all_days,
    run_plan_vlog,
)


def test_plan_prompt_includes_transcripts():
    """plan_daily_vlog injects transcripts_map into PLAN_PROMPT."""
    from clio.analyze import plan_daily_vlog

    clips = [
        {
            "index": "001",
            "title": "到达",
            "summary": "抵达机场",
            "timeline": [{"start": "00:00", "end": "00:30", "description": "到达"}],
            "location": "巴黎",
            "highlights": [],
            "suggested_use": "开场",
            "source_stem": "GL010683",
        }
    ]
    transcripts_map = {
        "gl010683": {"segments": [{"start": 0.0, "end": 2.5, "text": "今天天气真好", "avg_logprob": -0.1}]}
    }
    cfg = MagicMock(spec=AppConfig)
    cfg.ai = MagicMock(debug_print_prompt=False)
    cfg.plan = MagicMock()
    cfg.plan.max_clips_per_day = 12
    cfg.plan.target_duration_sec = 180
    cfg.whisper = MagicMock()
    cfg.whisper.max_segments_per_clip = 5
    cfg.whisper.enabled = True

    provider_mock = MagicMock()
    with (
        patch("clio.analyze.get_task_provider", return_value=(provider_mock, "deepseek-chat")),
        patch("clio.analyze._wrap_with_context", return_value="prompt") as mock_wrap,
        patch("clio.analyze._call_ai", return_value="{}"),
        patch("clio.analyze.extract_json", return_value={"sequence": [], "day_title": "test"}),
    ):
        result = plan_daily_vlog(clips, cfg, "day1", transcripts_map=transcripts_map)
        assert result["day_title"] == "test"
        args, _ = mock_wrap.call_args
        assert "今天天气真好" in args[0]


def test_plan_no_transcript_fallback():
    """No transcript provided — plan generates normally without injection."""
    from clio.analyze import plan_daily_vlog

    clips = [
        {
            "index": "001",
            "title": "到达",
            "summary": "",
            "timeline": [],
            "location": "",
            "highlights": [],
            "suggested_use": "",
        }
    ]
    cfg = MagicMock(spec=AppConfig)
    cfg.ai = MagicMock(debug_print_prompt=False)
    cfg.plan = MagicMock()
    cfg.plan.max_clips_per_day = 12
    cfg.plan.target_duration_sec = 180
    cfg.whisper = MagicMock()
    cfg.whisper.max_segments_per_clip = 5
    cfg.whisper.enabled = True

    provider_mock = MagicMock()
    with (
        patch("clio.analyze.get_task_provider", return_value=(provider_mock, "deepseek-chat")),
        patch("clio.analyze._wrap_with_context", return_value="prompt"),
        patch("clio.analyze._call_ai", return_value="{}"),
        patch("clio.analyze.extract_json", return_value={"sequence": [], "day_title": "test"}),
    ):
        result = plan_daily_vlog(clips, cfg, "day1", transcripts_map=None)
        assert result["day_title"] == "test"


def _analysis(index: str, source: str = "GL010683", day: str = "day1", **extra) -> dict:
    return {
        "index": index,
        "title": f"clip {index}",
        "summary": "summary",
        "timeline": [{"start": "00:00", "end": "00:10", "description": "d"}],
        "highlights": [],
        "location": "place",
        "suggested_use": "use",
        "source_file": f"{source}.mp4",
        "compressed_file": f"{index}_{source}.mp4",
        "day_label": day,
        "media_identity": {
            "original_stem": source,
            "original_path": f"{source}.mp4",
            "compressed_stem": f"{index}_{source}",
            "compressed_path": f"{index}_{source}.mp4",
            "index": index,
            "segment_index": None,
            "segment_offset_sec": 0.0,
        },
        **extra,
    }


def _write_analysis(config: AppConfig, data: dict) -> Path:
    config.texts_dir.mkdir(parents=True, exist_ok=True)
    path = config.texts_dir / f"{data['media_identity']['compressed_stem']}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestAnalysisDayLabel:
    def test_day_label_preferred(self) -> None:
        assert _analysis_day_label({"day_label": "day2", "day": "day3", "dayLabel": "day4"}) == "day2"

    def test_day_fallback(self) -> None:
        assert _analysis_day_label({"day": "day2"}) == "day2"

    def test_dayLabel_fallback(self) -> None:
        assert _analysis_day_label({"dayLabel": "day3"}) == "day3"

    def test_default(self) -> None:
        assert _analysis_day_label({}) == "day1"

    def test_blank_falls_back(self) -> None:
        assert _analysis_day_label({"day_label": "  "}) == "day1"

    def test_non_string_coerced(self) -> None:
        assert _analysis_day_label({"day_label": 42}) == "42"


class TestSourceInputsFromClips:
    def test_builds_index_and_stem(self) -> None:
        inputs = _source_inputs_from_clips(
            [{"index": "001", "source_stem": "GL010683"}, {"index": None, "source_stem": None}]
        )
        assert inputs == [
            {"index": "001", "source_stem": "GL010683"},
            {"index": "", "source_stem": ""},
        ]


class TestPlanLineageFingerprint:
    def test_changes_on_provider(self, loaded_config: AppConfig) -> None:
        clips = [{"index": "001", "source_stem": "GL010683", "title": "t"}]
        base = _plan_lineage_fingerprint(loaded_config, clips)
        loaded_config.ai.tasks["vlog_plan"].provider = "other-provider"
        changed = _plan_lineage_fingerprint(loaded_config, clips)
        assert base != changed

    def test_changes_on_model(self, loaded_config: AppConfig) -> None:
        clips = [{"index": "001", "source_stem": "GL010683", "title": "t"}]
        base = _plan_lineage_fingerprint(loaded_config, clips)
        loaded_config.ai.tasks["vlog_plan"].model = "other-model"
        changed = _plan_lineage_fingerprint(loaded_config, clips)
        assert base != changed

    def test_changes_on_clip_input(self, loaded_config: AppConfig) -> None:
        clips = [{"index": "001", "source_stem": "GL010683", "title": "t"}]
        base = _plan_lineage_fingerprint(loaded_config, clips)
        changed = _plan_lineage_fingerprint(loaded_config, [{"index": "001", "source_stem": "GL010684", "title": "t"}])
        assert base != changed

    def test_deterministic(self, loaded_config: AppConfig) -> None:
        clips = [{"index": "001", "source_stem": "GL010683", "title": "t"}]
        assert _plan_lineage_fingerprint(loaded_config, clips) == _plan_lineage_fingerprint(loaded_config, clips)

    def test_missing_task_does_not_crash(self, loaded_config: AppConfig) -> None:
        loaded_config.ai.tasks.pop("vlog_plan")
        assert _plan_lineage_fingerprint(loaded_config, []) != ""

    def test_changes_on_use_transcripts(self, loaded_config: AppConfig) -> None:
        clips = [{"index": "001", "source_stem": "GL010683", "title": "t"}]
        base = _plan_lineage_fingerprint(loaded_config, clips)
        loaded_config.plan.use_transcripts = not loaded_config.plan.use_transcripts
        changed = _plan_lineage_fingerprint(loaded_config, clips)
        assert base != changed


class TestDiscoverDayLabels:
    def test_collects_and_sorts(self, loaded_config: AppConfig) -> None:
        for i, day in enumerate(["day3", "day1", "day2"]):
            _write_analysis(loaded_config, _analysis(f"00{i + 1}", source=f"SRC{i}", day=day))
        assert _discover_day_labels(loaded_config) == ["day1", "day2", "day3"]

    def test_skips_corrupt_json(self, loaded_config: AppConfig) -> None:
        loaded_config.texts_dir.mkdir(parents=True, exist_ok=True)
        (loaded_config.texts_dir / "bad.json").write_text("{corrupt", encoding="utf-8")
        assert _discover_day_labels(loaded_config) == []

    def test_empty_dir(self, loaded_config: AppConfig) -> None:
        assert _discover_day_labels(loaded_config) == []


class TestRunPlanVlog:
    def test_no_clips_returns_none(self, loaded_config: AppConfig) -> None:
        assert run_plan_vlog(loaded_config, "day1") is None

    def test_full_run_writes_json_and_md(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        fake_plan = {
            "day_title": "day1",
            "theme": "主题",
            "total_estimated_sec": 180,
            "opening_tip": "开场",
            "ending_tip": "结尾",
            "sequence": [
                {"index": "001", "title": "clip 001", "reason": "r", "use_timeline": "u", "voiceover_hint": "v"}
            ],
        }
        with patch("clio.tasks.plan.plan_daily_vlog", return_value=fake_plan):
            result = run_plan_vlog(loaded_config, "day1")
        assert result is not None
        assert result["_schema_version"] == 2
        assert result["source_inputs"] == [{"index": "001", "source_stem": "GL010683"}]
        assert result["_lineage"]
        json_path = loaded_config.plans_dir / "day1_plan.json"
        md_path = loaded_config.plans_dir / "day1_plan.md"
        assert json_path.is_file()
        assert md_path.is_file()
        md = md_path.read_text(encoding="utf-8")
        assert "## 推荐剪辑顺序" in md
        assert "### 001 clip 001" in md
        out = capsys.readouterr().out
        assert "day1_plan.md" in out

    def test_skip_existing_with_same_lineage(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        fake_plan = {"day_title": "day1", "sequence": []}
        with patch("clio.tasks.plan.plan_daily_vlog", return_value=fake_plan):
            first = run_plan_vlog(loaded_config, "day1")
        assert first is not None
        with patch("clio.tasks.plan.plan_daily_vlog") as mock:
            second = run_plan_vlog(loaded_config, "day1")
        mock.assert_not_called()
        assert second == first
        assert "[跳过]" in capsys.readouterr().out

    def test_recompute_when_lineage_changes(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}):
            run_plan_vlog(loaded_config, "day1")
        loaded_config.ai.tasks["vlog_plan"].model = "different-model"
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1")
        assert mock.call_count == 1
        assert "缓存血缘变化" in capsys.readouterr().out

    def test_overwrite_forces_recompute(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}):
            run_plan_vlog(loaded_config, "day1")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1", overwrite=True)
        assert mock.call_count == 1

    def test_filter_by_day(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001", day="day1"))
        _write_analysis(loaded_config, _analysis("002", source="SRC2", day="day2"))
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1", filter_by_day=True)
        clips = mock.call_args.args[0]
        assert len(clips) == 1
        assert clips[0]["index"] == "001"

    def test_selected_files_filters(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        _write_analysis(loaded_config, _analysis("002", source="SRC2"))
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1", files=["002_SRC2"])
        clips = mock.call_args.args[0]
        assert len(clips) == 1
        assert clips[0]["index"] == "002"

    def test_invalid_index_skipped(self, loaded_config: AppConfig) -> None:
        bad = _analysis("not-a-number", source="BAD")
        bad["media_identity"]["compressed_stem"] = "BAD"
        _write_analysis(loaded_config, bad)
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            result = run_plan_vlog(loaded_config, "day1")
        assert result is None
        mock.assert_not_called()

    def test_legacy_analysis_without_identity(self, loaded_config: AppConfig) -> None:
        legacy = {
            "index": "001",
            "title": "t",
            "summary": "s",
            "timeline": [],
            "highlights": [],
            "source_file": "SRC.mp4",
            "compressed_file": "001_SRC.mp4",
            "day_label": "day1",
        }
        loaded_config.texts_dir.mkdir(parents=True, exist_ok=True)
        (loaded_config.texts_dir / "001_SRC.json").write_text(json.dumps(legacy), encoding="utf-8")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            result = run_plan_vlog(loaded_config, "day1")
        assert result is not None
        clips = mock.call_args.args[0]
        assert clips[0]["source_stem"] == "SRC"
        assert clips[0]["segment_offset_sec"] == 0.0

    def test_corrupt_existing_plan_recomputes(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        loaded_config.plans_dir.mkdir(parents=True, exist_ok=True)
        (loaded_config.plans_dir / "day1_plan.json").write_text("{corrupt", encoding="utf-8")
        (loaded_config.plans_dir / "day1_plan.md").write_text("old", encoding="utf-8")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            result = run_plan_vlog(loaded_config, "day1")
        assert mock.call_count == 1
        assert result is not None
        assert "已有规划文件损坏" in capsys.readouterr().out

    def test_legacy_cache_without_lineage_stamped(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        loaded_config.plans_dir.mkdir(parents=True, exist_ok=True)
        legacy_plan = {"day_title": "old", "sequence": [], "source_inputs": []}
        (loaded_config.plans_dir / "day1_plan.json").write_text(json.dumps(legacy_plan), encoding="utf-8")
        (loaded_config.plans_dir / "day1_plan.md").write_text("old", encoding="utf-8")
        with patch("clio.tasks.plan.plan_daily_vlog") as mock:
            result = run_plan_vlog(loaded_config, "day1")
        mock.assert_not_called()
        assert result["day_title"] == "old"
        stored = json.loads((loaded_config.plans_dir / "day1_plan.json").read_text(encoding="utf-8"))
        assert stored["_lineage"]
        assert "[跳过]" in capsys.readouterr().out

    def test_selected_files_means_no_skip(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}):
            run_plan_vlog(loaded_config, "day1")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1", files=["001_GL010683"])
        assert mock.call_count == 1

    def test_transcripts_missing_warning(self, loaded_config: AppConfig, capsys) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        loaded_config.plan.use_transcripts = True
        loaded_config.project_cfg.whisper.enabled = True
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}):
            result = run_plan_vlog(loaded_config, "day1")
        assert result["_transcripts_missing"] is True
        assert "未找到任何 transcript" in capsys.readouterr().out

    def test_transcripts_loaded_into_map(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        loaded_config.plan.use_transcripts = True
        loaded_config.project_cfg.whisper.enabled = True
        loaded_config.transcripts_dir.mkdir(parents=True, exist_ok=True)
        transcript = {
            "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
            "media_identity": {
                "original_stem": "GL010683",
                "original_path": "GL010683.mp4",
                "compressed_stem": "001_GL010683",
                "compressed_path": "001_GL010683.mp4",
                "index": "001",
                "segment_index": None,
                "segment_offset_sec": 0.0,
            },
        }
        (loaded_config.transcripts_dir / "001_GL010683_transcript.json").write_text(
            json.dumps(transcript), encoding="utf-8"
        )
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            result = run_plan_vlog(loaded_config, "day1")
        assert result is not None
        assert result.get("_transcripts_missing") is False
        assert "001_gl010683" in mock.call_args.kwargs["transcripts_map"]
        assert mock.call_args.kwargs["use_transcripts"] is not False

    def test_legacy_transcript_stem_parsing(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        loaded_config.plan.use_transcripts = True
        loaded_config.project_cfg.whisper.enabled = True
        loaded_config.transcripts_dir.mkdir(parents=True, exist_ok=True)
        legacy = {"segments": [], "source_stem": "001_GL010683_seg01"}
        (loaded_config.transcripts_dir / "legacy_transcript.json").write_text(json.dumps(legacy), encoding="utf-8")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1")
        assert "gl010683" in mock.call_args.kwargs["transcripts_map"]

    def test_corrupt_transcript_skipped(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        loaded_config.plan.use_transcripts = True
        loaded_config.project_cfg.whisper.enabled = True
        loaded_config.transcripts_dir.mkdir(parents=True, exist_ok=True)
        (loaded_config.transcripts_dir / "bad_transcript.json").write_text("{corrupt", encoding="utf-8")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1")
        assert mock.call_args.kwargs["transcripts_map"] == {}

    def test_missing_index_uses_file_stem(self, loaded_config: AppConfig) -> None:
        no_index = _analysis("001", source="SRC3")
        del no_index["index"]
        no_index["media_identity"]["index"] = ""
        loaded_config.texts_dir.mkdir(parents=True, exist_ok=True)
        (loaded_config.texts_dir / "007_SRC3.json").write_text(json.dumps(no_index), encoding="utf-8")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            result = run_plan_vlog(loaded_config, "day1")
        assert result is not None
        assert mock.call_args.args[0][0]["index"] == "007"

    def test_legacy_transcript_with_indexed_prefix(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001"))
        loaded_config.plan.use_transcripts = True
        loaded_config.project_cfg.whisper.enabled = True
        loaded_config.transcripts_dir.mkdir(parents=True, exist_ok=True)
        legacy = {"segments": [], "source_stem": "001_GL010683"}
        (loaded_config.transcripts_dir / "legacy2_transcript.json").write_text(json.dumps(legacy), encoding="utf-8")
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}) as mock:
            run_plan_vlog(loaded_config, "day1")
        assert "gl010683" in mock.call_args.kwargs["transcripts_map"]

    def test_tracker_updated_and_logged(self, loaded_config: AppConfig) -> None:
        from unittest.mock import MagicMock

        _write_analysis(loaded_config, _analysis("001"))
        tracker = MagicMock()
        with patch("clio.tasks.plan.plan_daily_vlog", return_value={"day_title": "day1", "sequence": []}):
            run_plan_vlog(loaded_config, "day1", tracker=tracker)
        assert tracker.update.called
        assert tracker.log.called

    def test_cancel_event_returns_early(self, loaded_config: AppConfig) -> None:
        import threading

        _write_analysis(loaded_config, _analysis("001"))
        event = threading.Event()
        event.set()
        assert run_plan_vlog(loaded_config, "day1", cancel_event=event) is None


class TestRunPlanAllDays:
    def test_no_labels_returns_none(self, loaded_config: AppConfig, capsys) -> None:
        assert run_plan_all_days(loaded_config) is None
        assert "没有可用的分析结果" in capsys.readouterr().out

    def test_aggregates_summary(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001", day="day1"))
        _write_analysis(loaded_config, _analysis("002", source="SRC2", day="day2"))
        fake_plan = {
            "day_title": "the title",
            "theme": "theme",
            "total_estimated_sec": 180,
            "sequence": [{"index": "001"}],
        }
        with patch("clio.tasks.plan.plan_daily_vlog", return_value=fake_plan):
            result = run_plan_all_days(loaded_config)
        assert result is not None
        assert result["_schema_version"] == 2
        assert [d["day_label"] for d in result["days"]] == ["day1", "day2"]
        assert result["days"][0]["clip_count"] == 1
        assert result["days"][0]["plan_file"] == "day1_plan.json"
        assert (loaded_config.plans_dir / "trip_plan.json").is_file()

    def test_skips_failed_days(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001", day="day1"))
        _write_analysis(loaded_config, _analysis("002", source="SRC2", day="day2"))
        with patch(
            "clio.tasks.plan.run_plan_vlog",
            side_effect=[None, {"day_title": "t", "theme": "", "total_estimated_sec": 0, "sequence": []}],
        ):
            result = run_plan_all_days(loaded_config)
        assert result is not None
        assert [d["day_label"] for d in result["days"]] == ["day2"]

    def test_cancel_stops_loop(self, loaded_config: AppConfig) -> None:
        import threading

        _write_analysis(loaded_config, _analysis("001", day="day1"))
        _write_analysis(loaded_config, _analysis("002", source="SRC2", day="day2"))
        event = threading.Event()
        event.set()
        with patch(
            "clio.tasks.plan.run_plan_vlog",
            return_value={"day_title": "t", "theme": "", "total_estimated_sec": 0, "sequence": []},
        ) as mock:
            result = run_plan_all_days(loaded_config, cancel_event=event)
        mock.assert_not_called()
        assert result is None

    def test_all_days_fail_returns_none(self, loaded_config: AppConfig) -> None:
        _write_analysis(loaded_config, _analysis("001", day="day1"))
        with patch("clio.tasks.plan.run_plan_vlog", return_value=None):
            assert run_plan_all_days(loaded_config) is None

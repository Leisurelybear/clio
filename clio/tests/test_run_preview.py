from __future__ import annotations

import json
from pathlib import Path

from clio.config.models import (
    AnalyzeConfig,
    AppConfig,
    GlobalConfig,
    GlobalPathsConfig,
    NamingConfig,
    ProjectConfig,
    ProjectPathsConfig,
    ProjectWhisperConfig,
    ScriptConfig,
)
from clio.ui.services.run_preview import build_run_preview


def _config(project_dir: Path, output_dir: Path) -> AppConfig:
    return AppConfig(
        global_cfg=GlobalConfig(paths=GlobalPathsConfig(), naming=NamingConfig(index_width=3)),
        project_cfg=ProjectConfig(
            paths=ProjectPathsConfig(output_dir=output_dir),
            analyze=AnalyzeConfig(compressed_subdir="compressed", texts_subdir="texts", skip_existing=True),
            script=ScriptConfig(scripts_subdir="scripts"),
            whisper=ProjectWhisperConfig(transcripts_subdir="transcripts"),
        ),
        project_dir=project_dir,
    )


def test_preview_compress_counts_existing_output_as_skip(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    (input_dir / "A.MP4").write_bytes(b"video")
    (input_dir / "B.mov").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "videos", "path": str(input_dir), "count": 2}
    assert preview["totals"] == {"selected_steps": 1, "will_run": 1, "will_skip": 1, "warnings": 0}
    assert preview["steps"] == [
        {
            "name": "compress",
            "label": "压缩原视频",
            "total": 2,
            "will_run": 1,
            "will_skip": 1,
            "warnings": [],
        }
    ]


def test_preview_compress_ignores_indexed_non_mp4_artifacts(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    (input_dir / "A.mp4").write_bytes(b"video")
    (compressed_dir / "001_A.mov").write_bytes(b"not a real compress artifact")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 0


def test_preview_compress_counts_existing_segment_output_as_skip(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    (input_dir / "A.mp4").write_bytes(b"video")
    (compressed_dir / "001_A_seg01.mp4").write_bytes(b"compressed segment")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][0]["will_run"] == 0
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_compress_counts_legacy_part_output_as_skip(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    (input_dir / "A.mp4").write_bytes(b"video")
    (compressed_dir / "001_A_part02.mp4").write_bytes(b"compressed segment")

    from clio.tasks._video_loader import save_selected_videos

    save_selected_videos(input_dir, [input_dir / "A.mp4"])
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][0]["will_run"] == 0
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_force_disables_skip_counts(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    (input_dir / "A.mp4").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress"],
        force=True,
        use_transcripts=True,
    )

    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 0
    assert preview["totals"]["will_run"] == 1
    assert preview["totals"]["will_skip"] == 0


def test_preview_input_file_limits_video_count(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    selected = input_dir / "clip.mp4"
    selected.write_bytes(b"video")
    (input_dir / "other.mp4").write_bytes(b"video")

    config = _config(input_dir, output_dir)
    config.input = selected

    preview = build_run_preview(
        config,
        steps=["compress"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "file", "path": str(selected), "count": 1}
    assert preview["steps"][0]["total"] == 1


def test_preview_counts_webm_source_as_video(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "clip.webm").write_bytes(b"video")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "videos", "path": str(input_dir), "count": 1}
    assert preview["steps"][0]["total"] == 1


def test_preview_analyze_counts_source_file_matched_json_outputs(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    texts_dir = output_dir / "texts"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    texts_dir.mkdir()
    (input_dir / "A.MP4").write_bytes(b"video")
    (input_dir / "B.mov").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")
    (compressed_dir / "002_B.mp4").write_bytes(b"compressed")
    (compressed_dir / "003_C.mp4").write_bytes(b"compressed")
    (texts_dir / "001_Title.json").write_text(json.dumps({"source_file": "A.MP4"}), encoding="utf-8")
    (texts_dir / "002_Broken.json").write_text("{", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["analyze"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "compressed", "path": str(compressed_dir), "count": 3}
    assert preview["steps"][0]["total"] == 2
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_analyze_ignores_compressed_without_resolved_original(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    texts_dir = output_dir / "texts"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    texts_dir.mkdir()
    (input_dir / "A.MP4").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")
    (compressed_dir / "002_missing.mp4").write_bytes(b"compressed")
    (texts_dir / "002_missing.json").write_text(json.dumps({"source_file": "missing.mp4"}), encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["analyze"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "compressed", "path": str(compressed_dir), "count": 2}
    assert preview["steps"][0]["total"] == 1
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 0


def test_preview_analyze_resolves_nested_original_even_when_project_not_recursive(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    nested_dir = input_dir / "nested"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    nested_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    (nested_dir / "A.mp4").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["analyze"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "compressed", "path": str(compressed_dir), "count": 1}
    assert preview["steps"][0]["total"] == 1
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 0


def test_preview_transcribe_uses_compressed_inputs_and_transcript_json_outputs(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    transcripts_dir = output_dir / "transcripts"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    transcripts_dir.mkdir()
    (input_dir / "A.mp4").write_bytes(b"video")
    (input_dir / "B.mp4").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")
    (compressed_dir / "002_B.mp4").write_bytes(b"compressed")
    (transcripts_dir / "001_A_transcript.json").write_text("{}", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["transcribe"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "compressed", "path": str(compressed_dir), "count": 2}
    assert preview["steps"][0]["total"] == 2
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_transcribe_invalid_transcript_json_does_not_skip(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    transcripts_dir = output_dir / "transcripts"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    transcripts_dir.mkdir()
    (input_dir / "A.mp4").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")
    (transcripts_dir / "001_A_transcript.json").write_text("{", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["transcribe"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][0]["total"] == 1
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 0


def test_preview_transcribe_ignores_compressed_without_resolved_original(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    (compressed_dir / "001_missing.mp4").write_bytes(b"compressed")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["transcribe"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "compressed", "path": str(compressed_dir), "count": 1}
    assert preview["steps"][0]["total"] == 0
    assert preview["steps"][0]["will_run"] == 0
    assert preview["steps"][0]["will_skip"] == 0


def test_preview_voiceover_counts_voiceover_json_outputs_per_analysis_json(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    scripts_dir = output_dir / "scripts"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    scripts_dir.mkdir()
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (texts_dir / "002_B.json").write_text("{}", encoding="utf-8")
    (scripts_dir / "001_A_voiceover.json").write_text("{}", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["voiceover"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][0]["name"] == "voiceover"
    assert preview["steps"][0]["label"] == "生成口播文案"
    assert preview["steps"][0]["total"] == 2
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_label_counts_labeled_outputs_per_analysis_json(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    labeled_dir = output_dir / "labeled"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    labeled_dir.mkdir()
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (texts_dir / "002_B.json").write_text("{}", encoding="utf-8")
    (labeled_dir / "001_A_labeled.mp4").write_bytes(b"labeled")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["label"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][0]["total"] == 2
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_plan_warns_when_no_analysis_json(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["plan"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][0] == {
        "name": "plan",
        "label": "vlog 剪辑规划",
        "total": 0,
        "will_run": 0,
        "will_skip": 0,
        "warnings": ["未找到分析 JSON，规划步骤可能没有输入。"],
    }
    assert preview["totals"]["warnings"] == 1


def test_preview_plan_skips_only_when_day_outputs_exist_and_json_valid(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    plans_dir = output_dir / "plans"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    plans_dir.mkdir()
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (plans_dir / "day2_plan.json").write_text("{}", encoding="utf-8")
    (plans_dir / "day2_plan.md").write_text("# plan", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["plan"],
        force=False,
        use_transcripts=True,
        day_label="day2",
    )

    assert preview["steps"][0]["total"] == 1
    assert preview["steps"][0]["will_run"] == 0
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_plan_invalid_json_does_not_skip(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    plans_dir = output_dir / "plans"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    plans_dir.mkdir()
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (plans_dir / "day2_plan.json").write_text("{", encoding="utf-8")
    (plans_dir / "day2_plan.md").write_text("# plan", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["plan"],
        force=False,
        use_transcripts=True,
        day_label="day2",
    )

    assert preview["steps"][0]["total"] == 1
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 0


def test_preview_files_selection_filters_non_plan_steps(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    texts_dir = output_dir / "texts"
    transcripts_dir = output_dir / "transcripts"
    scripts_dir = output_dir / "scripts"
    labeled_dir = output_dir / "labeled"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    texts_dir.mkdir()
    transcripts_dir.mkdir()
    scripts_dir.mkdir()
    labeled_dir.mkdir()
    (input_dir / "A.mp4").write_bytes(b"video")
    (input_dir / "B.mp4").write_bytes(b"video")
    (compressed_dir / "001_A.mp4").write_bytes(b"compressed")
    (compressed_dir / "002_B.mp4").write_bytes(b"compressed")
    (texts_dir / "001_A.json").write_text(json.dumps({"source_file": "A.mp4"}), encoding="utf-8")
    (texts_dir / "002_B.json").write_text(json.dumps({"source_file": "B.mp4"}), encoding="utf-8")
    (transcripts_dir / "001_A_transcript.json").write_text("{}", encoding="utf-8")
    (scripts_dir / "001_A_voiceover.json").write_text("{}", encoding="utf-8")
    (labeled_dir / "001_A_labeled.mp4").write_bytes(b"labeled")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress", "analyze", "transcribe", "voiceover", "label"],
        force=False,
        use_transcripts=True,
        files=["A.mp4"],
    )

    assert [(step["name"], step["total"], step["will_skip"]) for step in preview["steps"]] == [
        ("compress", 1, 1),
        ("analyze", 1, 1),
        ("transcribe", 1, 1),
        ("voiceover", 1, 1),
        ("label", 1, 1),
    ]


def test_preview_files_selection_does_not_filter_plan(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (texts_dir / "002_B.json").write_text("{}", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["plan"],
        force=False,
        use_transcripts=True,
        files=["A.mp4"],
    )

    assert preview["steps"][0]["total"] == 1
    assert preview["steps"][0]["will_run"] == 1


def test_preview_transcribe_warns_when_transcripts_disabled(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (input_dir / "A.mp4").write_bytes(b"video")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["transcribe"],
        force=False,
        use_transcripts=False,
    )

    assert preview["steps"][0]["warnings"] == ["字幕开关未启用，转录步骤不会在本次运行中执行。"]
    assert preview["steps"][0]["will_run"] == 0
    assert preview["totals"]["warnings"] == 1


def test_preview_unknown_steps_are_reported_as_warnings(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress", "unknown"],
        force=False,
        use_transcripts=True,
    )

    assert preview["steps"][1] == {
        "name": "unknown",
        "label": "unknown",
        "total": 0,
        "will_run": 0,
        "will_skip": 0,
        "warnings": ["未知步骤：unknown"],
    }
    assert preview["totals"]["selected_steps"] == 2
    assert preview["totals"]["warnings"] == 1

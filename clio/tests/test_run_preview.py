from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

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
from clio.tasks.analyze import (
    _analysis_lineage_components,
    _effective_analysis_context,
    _legacy_analysis_lineage_fingerprint,
    _lineage_fingerprint_from_components,
)
from clio.tasks.scripts import _load_script_template, _voiceover_lineage_fingerprint
from clio.ui.services.run_preview import build_run_preview
from clio.vmeta import VideoMeta


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


def _prepare_legacy_audio_project(tmp_path: Path, *, count: int = 1) -> tuple[AppConfig, list[tuple[Path, Path]]]:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    sources: list[tuple[Path, Path]] = []
    for number, name in enumerate(("A.mp4", "B.mp4")[:count], start=1):
        original = input_dir / name
        compressed = compressed_dir / f"{number:03d}_{original.stem}.mp4"
        original.write_bytes(f"source-{name}".encode())
        compressed.write_bytes(f"compressed-{name}".encode())
        VideoMeta.build(
            original,
            compressed,
            10.0,
            10.0,
            compress_settings={"remove_audio": False},
        ).write(compressed)
        sources.append((original, compressed))

    from clio.tasks._video_loader import save_selected_videos

    save_selected_videos(input_dir, [original for original, _ in sources])
    config = _config(input_dir, output_dir)
    config.global_cfg.compress.remove_audio = True
    config.texts_dir.mkdir(parents=True)
    config.scripts_dir.mkdir(parents=True)
    return config, sources


def _write_current_analysis(config: AppConfig, original: Path, compressed: Path) -> Path:
    effective_context = _effective_analysis_context(config, original, None)
    components = _analysis_lineage_components(
        config,
        None,
        effective_context,
        "",
        multi_window=False,
        compressed=compressed,
    )
    data = {
        "index": int(compressed.stem.split("_", 1)[0]),
        "title": original.stem,
        "summary": "current analysis",
        "source_file": original.name,
        "_lineage": _lineage_fingerprint_from_components(components),
        "_lineage_components": components,
    }
    path = config.texts_dir / f"{compressed.stem}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _legacy_analysis_record(
    config: AppConfig,
    original: Path,
    compressed: Path,
    *,
    source_file: str | None = None,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> tuple[dict[str, Any], str]:
    legacy_config = copy.deepcopy(config)
    legacy_config.global_cfg.compress.remove_audio = False
    effective_context = _effective_analysis_context(config, original, context_override)
    lineage = _legacy_analysis_lineage_fingerprint(
        legacy_config,
        task_prompts,
        effective_context,
        "",
        multi_window=False,
    )
    data = {
        "index": int(compressed.stem.split("_", 1)[0]),
        "title": original.stem,
        "summary": "legacy audio-preserving analysis",
        "source_file": source_file or original.name,
        "_lineage": lineage,
    }
    return data, lineage


_LEGACY_ANALYSIS_LINEAGE = object()


def _write_legacy_analysis(
    config: AppConfig,
    original: Path,
    compressed: Path,
    *,
    source_file: str | None = None,
    lineage: str | None | object = _LEGACY_ANALYSIS_LINEAGE,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    data, legacy_lineage = _legacy_analysis_record(
        config,
        original,
        compressed,
        source_file=source_file,
        task_prompts=task_prompts,
        context_override=context_override,
    )
    data["_lineage"] = legacy_lineage if lineage is _LEGACY_ANALYSIS_LINEAGE else lineage
    analysis_path = config.texts_dir / f"{compressed.stem}.json"
    analysis_path.write_text(json.dumps(data), encoding="utf-8")
    return analysis_path, data


def _current_voiceover_lineage(
    config: AppConfig,
    data: dict[str, Any],
    *,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> str:
    return _voiceover_lineage_fingerprint(
        config,
        data,
        _load_script_template(config),
        task_prompts,
        context_override,
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
    cast(Any, config).input = selected

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
    config = _config(input_dir, output_dir)
    _write_current_analysis(config, input_dir / "A.MP4", compressed_dir / "001_A.mp4")
    (texts_dir / "002_Broken.json").write_text("{", encoding="utf-8")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        config,
        steps=["analyze"],
        force=False,
        use_transcripts=True,
    )

    assert preview["input"] == {"mode": "compressed", "path": str(compressed_dir), "count": 3}
    assert preview["steps"][0]["total"] == 2
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 1


def test_preview_current_analysis_cache_needs_no_rerun_confirmation(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    _write_current_analysis(config, original, compressed)

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert "analysis_rerun" not in preview
    assert preview["steps"][0]["will_run"] == 0
    assert preview["steps"][0]["will_skip"] == 1


@pytest.mark.parametrize(
    "component",
    ("provider_model", "prompt", "context", "transcript", "compressed_media", "window"),
)
def test_preview_analysis_rerun_reports_changed_lineage_component(
    tmp_path: Path,
    component: str,
) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    analysis_path = _write_current_analysis(config, original, compressed)
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    data["_lineage_components"][component] = "stale-component"
    analysis_path.write_text(json.dumps(data), encoding="utf-8")

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert preview["analysis_rerun"] == {
        "required": True,
        "count": 1,
        "reasons": [{"key": component, "count": 1}],
    }
    assert preview["steps"][0]["will_run"] == 1
    assert preview["steps"][0]["will_skip"] == 0


def test_preview_analysis_rerun_reports_legacy_cache(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    _write_legacy_analysis(config, original, compressed, lineage="stale-legacy-lineage")

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert preview["analysis_rerun"] == {
        "required": True,
        "count": 1,
        "reasons": [{"key": "legacy", "count": 1}],
    }


def test_preview_analysis_rerun_reports_invalid_cache(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    _, compressed = sources[0]
    (config.texts_dir / f"{compressed.stem}.json").write_text("{invalid", encoding="utf-8")

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert preview["analysis_rerun"] == {
        "required": True,
        "count": 1,
        "reasons": [{"key": "invalid", "count": 1}],
    }


def test_preview_analysis_rerun_reports_source_mismatch(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    analysis_path = _write_current_analysis(config, original, compressed)
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    data["source_file"] = "other.mp4"
    analysis_path.write_text(json.dumps(data), encoding="utf-8")

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert preview["analysis_rerun"] == {
        "required": True,
        "count": 1,
        "reasons": [{"key": "source_mismatch", "count": 1}],
    }


def test_preview_analysis_rerun_respects_file_selection(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path, count=2)
    original_a, compressed_a = sources[0]
    _write_current_analysis(config, *sources[1])
    analysis_a = _write_current_analysis(config, original_a, compressed_a)
    data = json.loads(analysis_a.read_text(encoding="utf-8"))
    data["_lineage_components"]["prompt"] = "stale-component"
    analysis_a.write_text(json.dumps(data), encoding="utf-8")

    unselected = build_run_preview(
        config,
        steps=["analyze"],
        force=False,
        use_transcripts=True,
        files=[str(sources[1][0])],
    )

    assert "analysis_rerun" not in unselected
    assert unselected["steps"][0]["total"] == 1
    assert unselected["steps"][0]["will_run"] == 0

    selected = build_run_preview(
        config,
        steps=["analyze"],
        force=False,
        use_transcripts=True,
        files=[str(original_a)],
    )

    assert selected["analysis_rerun"] == {
        "required": True,
        "count": 1,
        "reasons": [{"key": "prompt", "count": 1}],
    }
    assert selected["steps"][0]["total"] == 1
    assert selected["steps"][0]["will_run"] == 1


def test_preview_analysis_rerun_counts_each_reason_once_per_video(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path, count=2)
    original_a, compressed_a = sources[0]
    original_b, compressed_b = sources[1]
    analysis_a = _write_current_analysis(config, original_a, compressed_a)
    data = json.loads(analysis_a.read_text(encoding="utf-8"))
    data["_lineage_components"]["prompt"] = "stale-component"
    data["_lineage_components"]["context"] = "stale-component"
    analysis_a.write_text(json.dumps(data), encoding="utf-8")
    (config.texts_dir / f"{compressed_b.stem}.json").write_text("{invalid", encoding="utf-8")

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert preview["analysis_rerun"] == {
        "required": True,
        "count": 2,
        "reasons": [
            {"key": "invalid", "count": 1},
            {"key": "prompt", "count": 1},
            {"key": "context", "count": 1},
        ],
    }


def test_preview_analysis_rerun_skips_explicit_or_disabled_reruns(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    analysis_path = _write_current_analysis(config, original, compressed)
    data = json.loads(analysis_path.read_text(encoding="utf-8"))
    data["_lineage_components"]["prompt"] = "stale-component"
    analysis_path.write_text(json.dumps(data), encoding="utf-8")

    assert "analysis_rerun" not in build_run_preview(config, steps=["analyze"], force=True, use_transcripts=True)

    skip_disabled = copy.deepcopy(config)
    skip_disabled.analyze.skip_existing = False
    assert "analysis_rerun" not in build_run_preview(
        skip_disabled, steps=["analyze"], force=False, use_transcripts=True
    )


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


def test_preview_detects_legacy_audio_analysis_rerun(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    _write_legacy_analysis(config, original, compressed)

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert preview["legacy_audio_removal"] == {
        "required": True,
        "analyze_count": 1,
        "voiceover_count": 0,
        "compress_count": 0,
    }


def test_preview_legacy_audio_analysis_respects_file_selection(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path, count=2)
    for original, compressed in sources:
        _write_legacy_analysis(config, original, compressed)

    preview = build_run_preview(
        config,
        steps=["analyze"],
        force=False,
        use_transcripts=True,
        files=[str(sources[1][0])],
    )

    assert preview["legacy_audio_removal"] == {
        "required": True,
        "analyze_count": 1,
        "voiceover_count": 0,
        "compress_count": 0,
    }


def test_preview_legacy_audio_analysis_skips_when_current_workflow_would_not_rerun(
    tmp_path: Path,
) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    effective_context = _effective_analysis_context(config, original, None)
    current_lineage = _legacy_analysis_lineage_fingerprint(
        config,
        None,
        effective_context,
        "",
        multi_window=False,
    )

    _write_legacy_analysis(config, original, compressed, lineage=current_lineage)
    assert "legacy_audio_removal" not in build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    _write_legacy_analysis(config, original, compressed, lineage=None)
    assert "legacy_audio_removal" not in build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    _write_legacy_analysis(config, original, compressed, lineage="other-lineage")
    assert "legacy_audio_removal" not in build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)


def test_preview_legacy_audio_analysis_skips_invalid_or_mismatched_sources(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path, count=2)
    original_a, compressed_a = sources[0]
    original_b, compressed_b = sources[1]
    _write_legacy_analysis(config, original_a, compressed_a, source_file="other.mp4")
    (config.texts_dir / f"{compressed_b.stem}.json").write_text("{invalid", encoding="utf-8")

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert "legacy_audio_removal" not in preview


def test_preview_legacy_audio_analysis_skips_explicit_or_disabled_reruns(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    _write_legacy_analysis(config, original, compressed)

    assert "legacy_audio_removal" not in build_run_preview(config, steps=["analyze"], force=True, use_transcripts=True)

    skip_disabled = copy.deepcopy(config)
    skip_disabled.analyze.skip_existing = False
    assert "legacy_audio_removal" not in build_run_preview(
        skip_disabled, steps=["analyze"], force=False, use_transcripts=True
    )

    audio_kept = copy.deepcopy(config)
    audio_kept.global_cfg.compress.remove_audio = False
    assert "legacy_audio_removal" not in build_run_preview(
        audio_kept, steps=["analyze"], force=False, use_transcripts=True
    )


def test_preview_detects_legacy_audio_voiceover_rerun(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    analysis_path, analysis_data = _write_legacy_analysis(config, original, compressed)
    (config.scripts_dir / f"{analysis_path.stem}_voiceover.json").write_text(
        json.dumps({"_lineage": _current_voiceover_lineage(config, analysis_data)}),
        encoding="utf-8",
    )

    preview = build_run_preview(
        config,
        steps=["analyze", "voiceover"],
        force=False,
        use_transcripts=True,
    )

    assert preview["legacy_audio_removal"] == {
        "required": True,
        "analyze_count": 1,
        "voiceover_count": 1,
        "compress_count": 0,
    }


def test_preview_legacy_audio_voiceover_skips_unrelated_or_missing_lineage(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path)
    original, compressed = sources[0]
    analysis_path, analysis_data = _write_legacy_analysis(config, original, compressed)
    voiceover_path = config.scripts_dir / f"{analysis_path.stem}_voiceover.json"

    voiceover_path.write_text(
        json.dumps(
            {
                "_lineage": _current_voiceover_lineage(
                    config,
                    analysis_data,
                    task_prompts={"voiceover": "different task prompt"},
                )
            }
        ),
        encoding="utf-8",
    )
    preview = build_run_preview(
        config,
        steps=["analyze", "voiceover"],
        force=False,
        use_transcripts=True,
    )
    assert preview["legacy_audio_removal"]["voiceover_count"] == 0

    voiceover_path.write_text(json.dumps({"_lineage": None}), encoding="utf-8")
    preview = build_run_preview(
        config,
        steps=["analyze", "voiceover"],
        force=False,
        use_transcripts=True,
    )
    assert preview["legacy_audio_removal"] == {
        "required": True,
        "analyze_count": 1,
        "voiceover_count": 0,
        "compress_count": 0,
    }


def test_preview_detects_legacy_audio_compression_rerun(tmp_path: Path) -> None:
    config, sources = _prepare_legacy_audio_project(tmp_path, count=2)

    preview = build_run_preview(
        config,
        steps=["compress"],
        force=False,
        use_transcripts=True,
        files=[str(sources[0][0])],
    )

    assert preview["legacy_audio_removal"] == {
        "required": True,
        "analyze_count": 0,
        "voiceover_count": 0,
        "compress_count": 1,
    }


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


def test_preview_plan_skips_sanitized_day_outputs(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    plans_dir = output_dir / "plans"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    plans_dir.mkdir()
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (plans_dir / "Ep2_-_agnes-3-flash_plan.json").write_text("{}", encoding="utf-8")
    (plans_dir / "Ep2_-_agnes-3-flash_plan.md").write_text("# plan", encoding="utf-8")

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
        day_label="Ep2 - agnes-3-flash",
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
    config = _config(input_dir, output_dir)
    _write_current_analysis(config, input_dir / "A.mp4", compressed_dir / "001_A.mp4")
    _write_current_analysis(config, input_dir / "B.mp4", compressed_dir / "002_B.mp4")
    (transcripts_dir / "001_A_transcript.json").write_text("{}", encoding="utf-8")
    (scripts_dir / "001_A_voiceover.json").write_text("{}", encoding="utf-8")
    (labeled_dir / "001_A_labeled.mp4").write_bytes(b"labeled")

    from clio.tasks._video_loader import save_selected_videos

    vids = [
        p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv", ".avi"}
    ]
    save_selected_videos(input_dir, vids)
    preview = build_run_preview(
        config,
        steps=["compress", "transcribe", "analyze", "voiceover", "label"],
        force=False,
        use_transcripts=True,
        files=["A.mp4"],
    )

    assert [(step["name"], step["total"], step["will_skip"]) for step in preview["steps"]] == [
        ("compress", 1, 1),
        ("transcribe", 1, 1),
        ("analyze", 1, 1),
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


def test_preview_warns_when_partial_plan_would_overwrite_existing_plan(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    plans_dir = output_dir / "plans"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    plans_dir.mkdir()
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (texts_dir / "002_B.json").write_text("{}", encoding="utf-8")
    (plans_dir / "day2_plan.json").write_text("{}", encoding="utf-8")
    (input_dir / "A.mp4").write_bytes(b"video")
    (input_dir / "B.mp4").write_bytes(b"video")

    from clio.tasks._video_loader import save_selected_videos

    save_selected_videos(input_dir, [input_dir / "A.mp4", input_dir / "B.mp4"])
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["plan"],
        force=False,
        use_transcripts=True,
        files=["A.mp4"],
        day_label="day2",
    )

    assert preview["plan_overwrite"] == {"required": True, "day_label": "day2"}


def test_preview_omits_overwrite_warning_for_full_or_new_plan(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    texts_dir = output_dir / "texts"
    plans_dir = output_dir / "plans"
    input_dir.mkdir()
    texts_dir.mkdir(parents=True)
    plans_dir.mkdir()
    (texts_dir / "001_A.json").write_text("{}", encoding="utf-8")
    (plans_dir / "day1_plan.json").write_text("{}", encoding="utf-8")
    (input_dir / "A.mp4").write_bytes(b"video")

    from clio.tasks._video_loader import save_selected_videos

    save_selected_videos(input_dir, [input_dir / "A.mp4"])
    config = _config(input_dir, output_dir)

    full = build_run_preview(config, steps=["plan"], force=False, use_transcripts=True)
    new_day = build_run_preview(
        config,
        steps=["plan"],
        force=False,
        use_transcripts=True,
        files=["A.mp4"],
        day_label="day2",
    )
    non_plan = build_run_preview(
        config,
        steps=["analyze"],
        force=False,
        use_transcripts=True,
        files=["A.mp4"],
        day_label="day1",
    )

    assert "plan_overwrite" not in full
    assert "plan_overwrite" not in new_day
    assert "plan_overwrite" not in non_plan


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


def test_preview_files_selection_keeps_same_named_originals_apart(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    compressed_dir = output_dir / "compressed"
    input_dir.mkdir()
    compressed_dir.mkdir(parents=True)
    a_dir = input_dir / "a"
    b_dir = input_dir / "b"
    a_dir.mkdir()
    b_dir.mkdir()
    a = a_dir / "clip.mp4"
    b = b_dir / "clip.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")
    comp_a = compressed_dir / "001_clip.mp4"
    comp_b = compressed_dir / "002_clip.mp4"
    comp_a.write_bytes(b"ca")
    comp_b.write_bytes(b"cb")
    VideoMeta.build(a, comp_a, 10.0, 10.0).write(comp_a)
    VideoMeta.build(b, comp_b, 10.0, 10.0).write(comp_b)

    from clio.tasks._video_loader import save_selected_videos

    save_selected_videos(input_dir, [a, b])
    preview = build_run_preview(
        _config(input_dir, output_dir),
        steps=["compress", "transcribe"],
        force=False,
        use_transcripts=True,
        files=[str(a)],
    )

    assert [(step["name"], step["total"]) for step in preview["steps"]] == [
        ("compress", 1),
        ("transcribe", 1),
    ]


def _write_analysis(config: AppConfig, summary: str) -> None:
    (config.texts_dir / "001_A.json").write_text(
        json.dumps(
            {
                "index": 1,
                "title": "A",
                "summary": summary,
                "source_file": "A.mp4",
            }
        ),
        encoding="utf-8",
    )


def test_preview_reports_user_edited_plan_after_input_change(tmp_path: Path) -> None:
    from clio.artifact_guard import mark_user_edited
    from clio.tasks.plan import run_plan_vlog

    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (output_dir / "texts").mkdir(parents=True)
    config = _config(input_dir, output_dir)
    _write_analysis(config, "before")
    generated = {
        "day_title": "Day 1",
        "theme": "trip",
        "total_estimated_sec": 60,
        "sequence": [{"index": "001", "title": "A", "use_timeline": "00:00-00:10"}],
    }
    with patch("clio.tasks.plan.plan_daily_vlog", return_value=generated):
        run_plan_vlog(config, day_label="day1", overwrite=True)

    plan_path = config.plans_dir / "day1_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mark_user_edited(plan, edited_at="2026-11-10T00:00:00Z")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _write_analysis(config, "after")

    preview = build_run_preview(config, steps=["plan"], force=False, use_transcripts=True)

    assert preview["artifact_overwrite"] == {
        "required": True,
        "items": ["day1_plan.json", "day1_plan.md"],
    }


def test_preview_reports_user_edited_stale_analysis(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (output_dir / "compressed").mkdir(parents=True)
    (output_dir / "texts").mkdir(parents=True)
    config = _config(input_dir, output_dir)
    original = input_dir / "A.mp4"
    compressed = config.compressed_dir / "001_A.mp4"
    original.write_bytes(b"source")
    compressed.write_bytes(b"compressed")
    VideoMeta.build(original, compressed, 10.0, 10.0).write(compressed)

    from clio.tasks._video_loader import save_selected_videos

    save_selected_videos(input_dir, [original])
    analysis = config.texts_dir / "001_A.json"
    analysis.write_text(
        json.dumps(
            {
                "title": "A",
                "source_file": "A.mp4",
                "compressed_file": compressed.name,
                "_lineage": "stale",
                "_user_edited": True,
            }
        ),
        encoding="utf-8",
    )
    analysis.with_suffix(".txt").write_text("user edit", encoding="utf-8")

    preview = build_run_preview(config, steps=["analyze"], force=False, use_transcripts=True)

    assert preview["artifact_overwrite"] == {
        "required": True,
        "items": ["001_A.json", "001_A.txt"],
    }


def test_preview_reports_user_edited_voiceover_after_prompt_context_change(tmp_path: Path) -> None:
    from clio.artifact_guard import mark_user_edited
    from clio.tasks.scripts import run_generate_scripts

    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    (output_dir / "texts").mkdir(parents=True)
    config = _config(input_dir, output_dir)
    _write_analysis(config, "same")
    with patch(
        "clio.tasks.scripts.generate_voiceover",
        return_value={"title": "A", "voiceover": "old", "edit_tip": ""},
    ):
        run_generate_scripts(config)

    out = config.scripts_dir / "001_A_voiceover.json"
    voiceover = json.loads(out.read_text(encoding="utf-8"))
    mark_user_edited(voiceover, edited_at="2026-11-10T00:00:00Z")
    out.write_text(json.dumps(voiceover), encoding="utf-8")

    unchanged = build_run_preview(config, steps=["voiceover"], force=False, use_transcripts=True)
    changed_context = build_run_preview(
        config,
        steps=["voiceover"],
        force=False,
        use_transcripts=True,
        context_override="关注海边镜头",
    )

    assert "artifact_overwrite" not in unchanged
    assert changed_context["artifact_overwrite"] == {
        "required": True,
        "items": ["001_A_voiceover.json", "001_A_voiceover.md"],
    }


def _prepare_plan_voiceover_preview(config: AppConfig, *, with_plan: bool = True, with_analysis: bool = True) -> None:
    config.texts_dir.mkdir(parents=True, exist_ok=True)
    config.plans_dir.mkdir(parents=True, exist_ok=True)
    config.scripts_dir.mkdir(parents=True, exist_ok=True)
    if with_analysis:
        _write_analysis(config, "harbor")
    if with_plan:
        plan = {
            "day_title": "Day 1",
            "theme": "harbor",
            "sequence": [{"index": "001", "title": "A", "use_timeline": "00:00-00:10"}],
            "source_inputs": [{"index": "001", "source_stem": "001_A", "match_stem": "A", "segment_offset_sec": 0}],
        }
        (config.plans_dir / "day1_plan.json").write_text(json.dumps(plan), encoding="utf-8")


def test_preview_consistency_warns_when_analysis_is_missing(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    config.texts_dir.mkdir(parents=True)

    preview = build_run_preview(config, steps=["consistency"], force=False, use_transcripts=True)

    assert preview["steps"] == [
        {
            "name": "consistency",
            "label": "统一实体与重复场景",
            "total": 0,
            "will_run": 0,
            "will_skip": 0,
            "warnings": ["未找到分析 JSON，实体一致性步骤可能没有输入。"],
        }
    ]


def test_preview_consistency_skips_only_complete_valid_outputs(tmp_path: Path) -> None:
    from clio.tasks.consistency import entity_consistency_paths

    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    config.texts_dir.mkdir(parents=True)
    config.plans_dir.mkdir(parents=True)
    _write_analysis(config, "harbor")
    out_json, out_md = entity_consistency_paths(config, "day1")
    out_json.write_text("{}", encoding="utf-8")
    out_md.write_text("# consistency", encoding="utf-8")

    skipped = build_run_preview(config, steps=["consistency"], force=False, use_transcripts=True)
    assert skipped["steps"][0]["will_run"] == 0
    assert skipped["steps"][0]["will_skip"] == 1

    out_json.write_text("{invalid", encoding="utf-8")
    invalid_json = build_run_preview(config, steps=["consistency"], force=False, use_transcripts=True)
    assert invalid_json["steps"][0]["will_run"] == 1

    out_json.write_text("{}", encoding="utf-8")
    out_md.unlink()
    missing_markdown = build_run_preview(config, steps=["consistency"], force=False, use_transcripts=True)
    assert missing_markdown["steps"][0]["will_run"] == 1

    forced = build_run_preview(config, steps=["consistency"], force=True, use_transcripts=True)
    assert forced["steps"][0]["will_run"] == 1
    assert forced["steps"][0]["will_skip"] == 0


def test_preview_plan_voiceover_warns_when_plan_is_missing(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _prepare_plan_voiceover_preview(config, with_plan=False)

    preview = build_run_preview(config, steps=["plan_voiceover"], force=False, use_transcripts=True)

    step = preview["steps"][0]
    assert step["name"] == "plan_voiceover"
    assert step["label"] == "生成计划口播"
    assert step["total"] == 0
    assert step["will_run"] == 0
    assert step["will_skip"] == 0
    assert step["warnings"] == ["未找到日度计划，请先生成规划再生成计划口播。"]


def test_preview_plan_voiceover_warns_when_analysis_is_missing(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _prepare_plan_voiceover_preview(config, with_analysis=False)

    preview = build_run_preview(config, steps=["plan_voiceover"], force=False, use_transcripts=True)

    assert preview["steps"][0]["warnings"] == ["未找到分析 JSON，计划口播步骤可能没有输入。"]


def test_preview_plan_voiceover_skips_only_complete_valid_outputs(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _prepare_plan_voiceover_preview(config)
    out_json = config.scripts_dir / "day1_plan_voiceover.json"
    out_md = config.scripts_dir / "day1_plan_voiceover.md"
    out_json.write_text("{}", encoding="utf-8")
    out_md.write_text("# voiceover", encoding="utf-8")

    skipped = build_run_preview(config, steps=["plan_voiceover"], force=False, use_transcripts=True)

    assert skipped["steps"][0]["will_run"] == 0
    assert skipped["steps"][0]["will_skip"] == 1

    out_json.write_text("{invalid", encoding="utf-8")
    invalid_json = build_run_preview(config, steps=["plan_voiceover"], force=False, use_transcripts=True)
    assert invalid_json["steps"][0]["will_run"] == 1

    out_json.write_text("{}", encoding="utf-8")
    out_md.unlink()
    missing_markdown = build_run_preview(config, steps=["plan_voiceover"], force=False, use_transcripts=True)
    assert missing_markdown["steps"][0]["will_run"] == 1


def test_preview_reports_user_edited_plan_voiceover_after_context_change(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _prepare_plan_voiceover_preview(config)
    out_json = config.scripts_dir / "day1_plan_voiceover.json"
    out_md = config.scripts_dir / "day1_plan_voiceover.md"
    out_json.write_text(
        json.dumps(
            {
                "segments": [],
                "_lineage": "old-lineage",
                "_user_edited": True,
                "_user_edited_at": "2026-11-10T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    out_md.write_text("# edited voiceover", encoding="utf-8")

    preview = build_run_preview(
        config,
        steps=["plan_voiceover"],
        force=False,
        use_transcripts=True,
        context_override="关注码头声音",
    )

    assert preview["artifact_overwrite"] == {
        "required": True,
        "items": ["day1_plan_voiceover.json", "day1_plan_voiceover.md"],
    }


def _write_day_analysis(config: AppConfig, index: str, source: str, day_label: str) -> None:
    config.texts_dir.mkdir(parents=True, exist_ok=True)
    compressed_stem = f"{index}_{source}"
    data = {
        "index": int(index),
        "title": source,
        "summary": day_label,
        "source_file": f"{source}.mp4",
        "compressed_file": f"{compressed_stem}.mp4",
        "day_label": day_label,
        "media_identity": {
            "original_stem": source,
            "original_path": f"{source}.mp4",
            "compressed_stem": compressed_stem,
            "compressed_path": f"{compressed_stem}.mp4",
            "index": index,
            "segment_index": None,
            "segment_offset_sec": 0.0,
        },
    }
    (config.texts_dir / f"{compressed_stem}.json").write_text(json.dumps(data), encoding="utf-8")


def test_preview_all_days_counts_each_discovered_plan(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _write_day_analysis(config, "001", "A", "day1")
    _write_day_analysis(config, "002", "B", "day2")
    config.plans_dir.mkdir(parents=True)
    (config.plans_dir / "day1_plan.json").write_text("{}", encoding="utf-8")
    (config.plans_dir / "day1_plan.md").write_text("# plan", encoding="utf-8")

    preview = build_run_preview(
        config,
        steps=["plan"],
        force=False,
        use_transcripts=True,
        all_days=True,
    )

    step = preview["steps"][0]
    assert step["total"] == 2
    assert step["will_run"] == 1
    assert step["will_skip"] == 1


def test_preview_all_days_counts_consistency_per_day(tmp_path: Path) -> None:
    from clio.tasks.consistency import entity_consistency_paths

    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _write_day_analysis(config, "001", "A", "day2")
    _write_day_analysis(config, "002", "B", "day1")
    out_json, out_md = entity_consistency_paths(config, "day1")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text("{}", encoding="utf-8")
    out_md.write_text("# consistency", encoding="utf-8")

    preview = build_run_preview(
        config,
        steps=["consistency"],
        force=False,
        use_transcripts=True,
        all_days=True,
    )

    step = preview["steps"][0]
    assert step["total"] == 2
    assert step["will_run"] == 1
    assert step["will_skip"] == 1


def test_preview_all_days_plan_voiceover_uses_only_planned_days(tmp_path: Path) -> None:
    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _write_day_analysis(config, "001", "A", "day1")
    _write_day_analysis(config, "002", "B", "day2")
    config.plans_dir.mkdir(parents=True)
    (config.plans_dir / "day1_plan.json").write_text("{}", encoding="utf-8")
    config.scripts_dir.mkdir(parents=True)
    (config.scripts_dir / "day1_plan_voiceover.json").write_text("{}", encoding="utf-8")
    (config.scripts_dir / "day1_plan_voiceover.md").write_text("# voiceover", encoding="utf-8")

    preview = build_run_preview(
        config,
        steps=["plan_voiceover"],
        force=False,
        use_transcripts=True,
        all_days=True,
    )

    step = preview["steps"][0]
    assert step["total"] == 1
    assert step["will_run"] == 0
    assert step["will_skip"] == 1


def test_preview_all_days_reports_protected_artifact_on_later_day(tmp_path: Path) -> None:
    from clio.artifact_guard import mark_user_edited
    from clio.tasks.plan import run_plan_vlog

    input_dir = tmp_path / "videos"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    config = _config(input_dir, output_dir)
    _write_day_analysis(config, "001", "A", "day1")
    _write_day_analysis(config, "002", "B", "day2")
    generated = {
        "day_title": "Day",
        "theme": "trip",
        "total_estimated_sec": 60,
        "sequence": [{"index": "001", "title": "A", "use_timeline": "00:00-00:10"}],
    }
    with patch("clio.tasks.plan.plan_daily_vlog", return_value=generated):
        run_plan_vlog(config, day_label="day1", overwrite=True)
        run_plan_vlog(config, day_label="day2", overwrite=True)

    plan_path = config.plans_dir / "day2_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    mark_user_edited(plan, edited_at="2026-11-10T00:00:00Z")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    preview = build_run_preview(
        config,
        steps=["plan"],
        force=False,
        use_transcripts=True,
        all_days=True,
        context_override="changed planning context",
    )

    assert preview["artifact_overwrite"] == {
        "required": True,
        "items": ["day2_plan.json", "day2_plan.md"],
    }

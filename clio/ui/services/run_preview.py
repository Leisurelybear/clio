from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from clio._constants import VIDEO_EXTENSIONS
from clio.config.models import AppConfig
from clio.i18n import t
from clio.identity import SEGMENT_SUFFIX_RE
from clio.index import ArtifactIndex
from clio.tasks._helpers import _matches_selected_artifact, _matches_selected_stem, _selected_stems


def _step_label(name: str) -> str:
    """Step label for the run preview, resolved from the active locale."""
    return t(f"run.step.{name}.label", fallback=name)


def build_run_preview(
    config: AppConfig,
    steps: Iterable[str],
    *,
    force: bool,
    use_transcripts: bool,
    files: list[str] | None = None,
    day_label: str = "day1",
) -> dict[str, Any]:
    selected_steps = list(steps)
    selected = _selected_stems(files) if files is not None else None

    source_input_info, all_source_videos = _source_input_info(config)
    from clio.tasks._video_loader import source_videos as _load_source_videos

    all_recursive_source_videos = list(_load_source_videos(config))
    compressed_input_info, all_compressed_videos = _compressed_input_info(config)
    all_analysis_jsons = _analysis_jsons(config)
    index = ArtifactIndex(
        output_dir=config.paths.output_dir,
        project_dir=config.project_dir or Path(),
        compressed_dir=config.compressed_dir,
        texts_dir=config.texts_dir,
        scripts_dir=config.scripts_dir,
        transcripts_dir=config.transcripts_dir,
    )
    index.build()

    filtered_source_videos = _filter_stem_paths(all_source_videos, selected)
    compressed_videos = _filter_stem_paths(all_compressed_videos, selected)
    analysis_jsons = _filter_artifact_paths(all_analysis_jsons, selected)

    input_info = compressed_input_info if _starts_from_compressed(selected_steps) else source_input_info
    input_info = {
        **input_info,
        "count": len(compressed_videos) if input_info["mode"] == "compressed" else len(filtered_source_videos),
    }

    preview_steps = [
        _step_preview(
            config,
            step,
            source_videos=filtered_source_videos,
            all_recursive_source_videos=all_recursive_source_videos,
            compressed_videos=compressed_videos,
            analysis_jsons=analysis_jsons,
            all_analysis_jsons=all_analysis_jsons,
            index=index,
            force=force,
            use_transcripts=use_transcripts,
            day_label=day_label,
        )
        for step in selected_steps
    ]

    # Surface empty selection so UI/CLI can prompt migrate / add-videos.
    # Attach once on input, not per-step, so totals.warnings stays step-scoped.
    empty_selection_note: str | None = None
    if input_info.get("mode") == "videos" and input_info.get("count", 0) == 0 and config.project_dir is not None:
        vjson = config.project_dir / "videos.json"
        empty_selection_note = (
            t("api.preview_no_videos_json") if not vjson.is_file() else t("api.preview_empty_videos_json")
        )
        input_info = {**input_info, "hint": empty_selection_note}

    return {
        "input": input_info,
        "steps": preview_steps,
        "totals": {
            "selected_steps": len(selected_steps),
            "will_run": sum(step["will_run"] for step in preview_steps),
            "will_skip": sum(step["will_skip"] for step in preview_steps),
            "warnings": sum(len(step["warnings"]) for step in preview_steps),
        },
    }


def _starts_from_compressed(steps: list[str]) -> bool:
    return bool(steps) and steps[0] in {"analyze", "transcribe"}


def _source_input_info(config: AppConfig) -> tuple[dict[str, Any], list[Path]]:
    project_input = Path(config.input) if getattr(config, "input", None) else None
    if project_input and project_input.is_file():
        videos = [project_input] if _is_video(project_input) else []
        return {"mode": "file", "path": str(project_input), "count": len(videos)}, videos

    from clio.tasks._video_loader import source_videos

    videos = source_videos(config)
    project_dir = config.project_dir
    return {
        "mode": "videos",
        "path": str(project_dir) if project_dir else "",
        "count": len(videos),
    }, videos


def _compressed_input_info(config: AppConfig) -> tuple[dict[str, Any], list[Path]]:
    compressed_dir = config.compressed_dir
    videos = _video_files(compressed_dir, recursive=False)
    return {"mode": "compressed", "path": str(compressed_dir), "count": len(videos)}, videos


def _step_preview(
    config: AppConfig,
    step: str,
    *,
    source_videos: list[Path],
    all_recursive_source_videos: list[Path],
    compressed_videos: list[Path],
    analysis_jsons: list[Path],
    all_analysis_jsons: list[Path],
    index: ArtifactIndex,
    force: bool,
    use_transcripts: bool,
    day_label: str,
) -> dict[str, Any]:
    if step == "compress":
        return _compress_step(config, source_videos, force=force)
    if step == "analyze":
        return _analyze_step(config, compressed_videos, all_recursive_source_videos, force=force, index=index)
    if step == "transcribe":
        if not use_transcripts:
            return _warning_step(step, t("api.preview_transcribe_disabled"))
        return _transcribe_step(config, compressed_videos, all_recursive_source_videos, force=force, index=index)
    if step == "voiceover":
        return _voiceover_step(config, analysis_jsons, force=force)
    if step == "plan":
        return _plan_step(config, all_analysis_jsons, day_label=day_label, force=force)
    if step == "label":
        return _label_step(config, analysis_jsons, force=force)
    return _unknown_step(step)


def _compress_step(config: AppConfig, source_videos: list[Path], *, force: bool) -> dict[str, Any]:
    total = len(source_videos)
    if force:
        return _step("compress", total=total, will_run=total, will_skip=0)

    compressed_original_stems = _compressed_original_stems(config.compressed_dir)
    skipped = sum(1 for source in source_videos if source.stem.lower() in compressed_original_stems)
    return _step("compress", total=total, will_run=total - skipped, will_skip=skipped)


def _analyze_step(
    config: AppConfig,
    compressed_videos: list[Path],
    source_videos: list[Path],
    *,
    force: bool,
    index: ArtifactIndex,
) -> dict[str, Any]:
    source_names = {source.stem.lower(): source.name for source in source_videos}
    inputs = _resolvable_analysis_inputs(compressed_videos, source_names)
    total = len(inputs)
    if force:
        return _step("analyze", total=total, will_run=total, will_skip=0)

    skipped = sum(1 for compressed in inputs if (g := index.lookup(compressed_stem=compressed.stem)) and g.texts)
    return _step("analyze", total=total, will_run=total - skipped, will_skip=skipped)


def _transcribe_step(
    config: AppConfig,
    compressed_videos: list[Path],
    source_videos: list[Path],
    *,
    force: bool,
    index: ArtifactIndex,
) -> dict[str, Any]:
    source_names = {source.stem.lower(): source.name for source in source_videos}
    inputs = _resolvable_analysis_inputs(compressed_videos, source_names)
    total = len(inputs)
    if force:
        return _step("transcribe", total=total, will_run=total, will_skip=0)

    skipped = sum(1 for compressed in inputs if (g := index.lookup(compressed_stem=compressed.stem)) and g.transcript)
    return _step("transcribe", total=total, will_run=total - skipped, will_skip=skipped)


def _voiceover_step(
    config: AppConfig,
    analysis_jsons: list[Path],
    *,
    force: bool,
) -> dict[str, Any]:
    total = len(analysis_jsons)
    if total == 0:
        return _warning_step("voiceover", t("api.preview_missing_analyze_voiceover"))
    skipped = (
        0
        if force
        else sum(
            1
            for analysis_json in analysis_jsons
            if (config.scripts_dir / f"{analysis_json.stem}_voiceover.json").exists()
        )
    )
    return _step("voiceover", total=total, will_run=total - skipped, will_skip=skipped)


def _plan_step(config: AppConfig, analysis_jsons: list[Path], *, day_label: str, force: bool) -> dict[str, Any]:
    if not analysis_jsons:
        return _warning_step("plan", t("api.preview_missing_analyze_plan"))
    total = 1
    plan_json = config.plans_dir / f"{day_label}_plan.json"
    plan_md = config.plans_dir / f"{day_label}_plan.md"
    skipped = 0 if force else int(plan_md.is_file() and _valid_json_file(plan_json))
    return _step("plan", total=total, will_run=total - skipped, will_skip=skipped)


def _label_step(config: AppConfig, analysis_jsons: list[Path], *, force: bool) -> dict[str, Any]:
    total = len(analysis_jsons)
    if total == 0:
        return _warning_step("label", t("api.preview_missing_analyze_label"))
    labeled_dir = config.paths.output_dir / "labeled"
    skipped = (
        0
        if force
        else sum(1 for analysis_json in analysis_jsons if (labeled_dir / f"{analysis_json.stem}_labeled.mp4").exists())
    )
    return _step("label", total=total, will_run=total - skipped, will_skip=skipped)


def _warning_step(step: str, warning: str) -> dict[str, Any]:
    return _step(step, total=0, will_run=0, will_skip=0, warnings=[warning])


def _unknown_step(step: str) -> dict[str, Any]:
    return _step(step, total=0, will_run=0, will_skip=0, warnings=[t("api.preview_unknown_step", {"step": step})])


def _step(
    name: str,
    *,
    total: int,
    will_run: int,
    will_skip: int,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "label": _step_label(name),
        "total": total,
        "will_run": will_run,
        "will_skip": will_skip,
        "warnings": warnings or [],
    }


def _analysis_jsons(config: AppConfig) -> list[Path]:
    texts_dirs = _find_texts_dirs(config.texts_dir)
    return sorted(path for texts_dir in texts_dirs for path in texts_dir.iterdir() if path.suffix.lower() == ".json")


def _find_texts_dirs(texts_dir: Path) -> list[Path]:
    if texts_dir.is_dir():
        return [texts_dir]
    output_dir = texts_dir.parent
    if not output_dir.is_dir():
        return []
    return sorted(path for path in output_dir.rglob(texts_dir.name) if path.is_dir())


def _filter_stem_paths(paths: list[Path], selected: set[str] | None) -> list[Path]:
    if selected is None:
        return paths
    return [path for path in paths if _matches_selected_stem(path, selected)]


def _filter_artifact_paths(paths: list[Path], selected: set[str] | None) -> list[Path]:
    if selected is None:
        return paths
    return [path for path in paths if _matches_selected_artifact(path, selected)]


def _video_files(path: Path, *, recursive: bool) -> list[Path]:
    if not path.is_dir():
        return []
    candidates = path.rglob("*") if recursive else path.iterdir()
    return sorted(item for item in candidates if item.is_file() and _is_video(item))


def _compressed_original_stems(compressed_dir: Path) -> set[str]:
    if not compressed_dir.is_dir():
        return set()
    stems = set()
    for path in sorted(compressed_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".mp4":
            continue
        prefix, stem_part = _indexed_stem_parts(path.stem)
        if prefix:
            stems.add(_original_stem_for_compressed_part(stem_part).lower())
    return stems


def _resolvable_analysis_inputs(compressed_videos: list[Path], source_names: dict[str, str]) -> list[Path]:
    inputs = []
    for compressed in compressed_videos:
        prefix, stem_part = _indexed_stem_parts(compressed.stem)
        if not prefix:
            continue
        original_stem = _original_stem_for_compressed_part(stem_part)
        if original_stem.lower() in source_names:
            inputs.append(compressed)
    return inputs


def _valid_json_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return True


def _indexed_stem_parts(stem: str) -> tuple[str, str]:
    if "_" not in stem:
        return "", stem
    prefix, stem_part = stem.split("_", 1)
    if not prefix.isdigit():
        return "", stem
    return prefix, stem_part


def _original_stem_for_compressed_part(stem_part: str) -> str:
    return SEGMENT_SUFFIX_RE.sub("", stem_part)


def _is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS

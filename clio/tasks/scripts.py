"""Script generation task — generate voiceover scripts from analysis."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import generate_plan_voiceover, generate_voiceover
from clio.artifact_guard import (
    ArtifactOverwriteConfirmationRequired,
    backup_artifacts,
    clear_user_edited,
    is_user_edited,
)
from clio.config import AppConfig
from clio.i18n import t
from clio.log import timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressReporter
from clio.prompts import PLAN_VOICEOVER_PROMPT, SCRIPT_PROMPT
from clio.resources import find_resource
from clio.schema import add_schema_version
from clio.tasks._helpers import _matches_selected_artifact, _selection_from_files, _task_lineage_settings
from clio.utils import plan_day_stem, write_json_atomic, write_text_atomic


def _load_script_template(config: AppConfig) -> str:
    path = config.script.template_file
    if path.is_file():
        return path.read_text(encoding="utf-8")
    if path.name == "vlog_template.md":
        default = find_resource("templates/vlog_template.md")
        if default is not None:
            return default.read_text(encoding="utf-8")
    return ""


def _voiceover_lineage_fingerprint(
    config: AppConfig,
    data: dict,
    template: str,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> str:
    """Fingerprint of everything that can change a cached voiceover script.

    Changing the voiceover prompt (builtin template, override, or runtime task
    prompt) or provider/model invalidates the skip_existing cache so users
    never silently keep a script generated under an older lineage.
    """
    from clio.analyze import _prompt_context_parts
    from clio.prompt_overrides import resolve_prompt_template

    try:
        prompt = resolve_prompt_template("voiceover", SCRIPT_PROMPT, config, task_prompts=task_prompts)
    except (OSError, UnicodeError):
        prompt = SCRIPT_PROMPT
    payload = json.dumps(
        {
            "task": _task_lineage_settings(config, "voiceover"),
            "prompt": prompt,
            "template": template,
            "target_words": getattr(config.script, "target_words", 80),
            "context": _prompt_context_parts(config, context_override),
            "analysis": data,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _voiceover_output_paths(json_file: Path, config: AppConfig) -> tuple[Path, Path]:
    return (
        config.scripts_dir / f"{json_file.stem}_voiceover.json",
        config.scripts_dir / f"{json_file.stem}_voiceover.md",
    )


def _voiceover_needs_overwrite(
    json_file: Path,
    config: AppConfig,
    template: str,
    *,
    overwrite: bool,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> bool:
    """Return whether this input would regenerate a protected voiceover."""
    out, _ = _voiceover_output_paths(json_file, config)
    if not out.is_file():
        return False
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
        existing = json.loads(out.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return True
    if not isinstance(data, dict) or not isinstance(existing, dict):
        return True
    if overwrite or not config.analyze.skip_existing:
        return is_user_edited(existing)
    data["index"] = data.get("index", json_file.stem[:3])
    current = _voiceover_lineage_fingerprint(config, data, template, task_prompts, context_override)
    if existing.get("_lineage") is None:
        return False
    return is_user_edited(existing) and existing.get("_lineage") != current


def voiceover_artifact_overwrite_required(
    config: AppConfig,
    files: list[str] | None = None,
    *,
    single_file: Path | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> list[Path]:
    """Return existing voiceover artifacts that this run would replace."""
    input_files = [Path(single_file)] if single_file is not None else sorted(config.texts_dir.glob("*.json"))
    if files is not None:
        selected = _selection_from_files(files)
        input_files = [path for path in input_files if _matches_selected_artifact(path, selected)]
    template = _load_script_template(config)
    risky: list[Path] = []
    for json_file in input_files:
        json_file = Path(json_file)
        if not _voiceover_needs_overwrite(
            json_file,
            config,
            template,
            overwrite=overwrite,
            context_override=context_override,
            task_prompts=task_prompts,
        ):
            continue
        out, md_out = _voiceover_output_paths(json_file, config)
        risky.extend(path for path in (out, md_out) if path.is_file())
    return risky


def _process_one_script(
    json_file: Path,
    config: AppConfig,
    template: str,
    token_store: FileTokenUsageStore,
    cancel_event: threading.Event | None,
    overwrite: bool,
    state: ProcessingState,
    tracker: ProgressReporter | None,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    confirm_artifact_overwrite: bool = False,
) -> bool | str:
    """Process a single voiceover script. Returns True (ok), False (skipped), or error string."""
    if cancel_event and cancel_event.is_set():
        return "cancelled"

    data = json.loads(json_file.read_text(encoding="utf-8"))
    data["index"] = data.get("index", json_file.stem[:3])
    orig_stem = Path(data.get("source_file") or json_file.stem).stem
    out, md_out = _voiceover_output_paths(json_file, config)
    if not overwrite and config.analyze.skip_existing and out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = None
            current = (
                _voiceover_lineage_fingerprint(config, data, template, task_prompts, context_override)
                if existing is not None
                else None
            )
        except (json.JSONDecodeError, OSError, UnicodeError):
            existing, current = None, None
        if isinstance(existing, dict):
            if existing.get("_lineage") == current:
                state.mark(orig_stem, "voiceover", "skipped")
                if tracker:
                    tracker.next(message=t("task.scripts.skip_next", {"name": json_file.stem}))
                    tracker.log(t("task.scripts.skip_exists_log", {"name": json_file.stem}))
                return False
            if existing.get("_lineage") is None:
                # Legacy cache without lineage: accept it (best effort) by
                # stamping a fresh marker so only input/prompt changes skip.
                existing["_lineage"] = current
                write_json_atomic(out, existing)
                state.mark(orig_stem, "voiceover", "skipped")
                if tracker:
                    tracker.next(message=t("task.scripts.skip_next", {"name": json_file.stem}))
                    tracker.log(t("task.scripts.skip_exists_log", {"name": json_file.stem}))
                return False
            # Mismatched provider/prompt: regenerate below.

    protected_overwrite = _voiceover_needs_overwrite(
        json_file,
        config,
        template,
        overwrite=overwrite,
        context_override=context_override,
        task_prompts=task_prompts,
    )
    if protected_overwrite and not confirm_artifact_overwrite:
        raise ArtifactOverwriteConfirmationRequired(
            t("task.artifact.overwrite_confirmation_required", {"items": out.name}),
            [out, md_out],
        )

    if tracker:
        tracker.next(message=t("task.scripts.next", {"name": json_file.stem}))
    script = generate_voiceover(
        data,
        template,
        config,
        token_store=token_store,
        cancel_event=cancel_event,
        context_override=context_override,
        task_prompts=task_prompts,
    )
    if cancel_event and cancel_event.is_set():
        return "cancelled"
    add_schema_version(script)
    clear_user_edited(script)
    script["_lineage"] = _voiceover_lineage_fingerprint(config, data, template, task_prompts, context_override)
    if protected_overwrite:
        backup_artifacts([out, md_out])
    write_json_atomic(out, script)
    state.mark(orig_stem, "voiceover", "done")
    if tracker:
        tracker.log(t("task.scripts.done_log", {"name": orig_stem}))

    md_content = (
        t("md.script_header", {"title": script.get("title", json_file.stem)})
        + "\n\n"
        + f"{script.get('voiceover', '')}\n\n"
        + t("md.edit_tip", {"tip": script.get("edit_tip", "")})
        + "\n"
    )
    write_text_atomic(md_out, md_content)
    print(f"  -> {md_out.name}")
    return True


def run_generate_scripts(
    config: AppConfig,
    tracker: ProgressReporter | None = None,
    single_file: Path | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    confirm_artifact_overwrite: bool = False,
    **kwargs: Any,
) -> None:
    config.scripts_dir.mkdir(parents=True, exist_ok=True)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    state = ProcessingState(config.paths.output_dir)
    template = _load_script_template(config)

    input_files = [single_file] if single_file else sorted(config.texts_dir.glob("*.json"))
    if files is not None:
        selected = _selection_from_files(files)
        input_files = [f for f in input_files if _matches_selected_artifact(f, selected)]
    if not input_files:
        return
    risky_artifacts = voiceover_artifact_overwrite_required(
        config,
        files,
        single_file=single_file,
        overwrite=overwrite,
        context_override=context_override,
        task_prompts=task_prompts,
    )
    if risky_artifacts and not confirm_artifact_overwrite:
        names = ", ".join(path.name for path in risky_artifacts)
        raise ArtifactOverwriteConfirmationRequired(
            t("task.artifact.overwrite_confirmation_required", {"items": names}),
            risky_artifacts,
        )
    if tracker:
        tracker.update(
            phase="voiceover",
            total=len(input_files),
            current=0,
            message=t("task.scripts.phase_title", {"count": len(input_files)}),
        )

    max_workers = config.analyze.max_workers
    failures = 0
    with timed(t("task.scripts.timed_label", {"count": len(input_files), "workers": max_workers})):
        if max_workers <= 1:
            sequential_error_count = 0
            for json_file in input_files:
                if cancel_event and cancel_event.is_set():
                    print(t("task.scripts.cancelled"))
                    break
                json_file = Path(json_file)
                print(t("task.scripts.start", {"name": json_file.stem}))
                t0 = time.monotonic()
                try:
                    result = _process_one_script(
                        json_file,
                        config,
                        template,
                        token_store,
                        cancel_event,
                        overwrite,
                        state,
                        tracker,
                        context_override,
                        task_prompts,
                        confirm_artifact_overwrite,
                    )
                except Exception as e:  # noqa: BLE001 - isolate one failed script without aborting the batch
                    print(t("task.scripts.error", {"name": json_file.stem, "detail": e}))
                    sequential_error_count += 1
                    continue
                elapsed = time.monotonic() - t0
                if isinstance(result, str) and result == "cancelled":
                    break
                if isinstance(result, str):
                    print(t("task.scripts.error", {"name": json_file.stem, "detail": result}))
                    sequential_error_count += 1
                elif result is True:
                    print(f"  ✓ {elapsed:.1f}s")
            if sequential_error_count:
                print(t("task.scripts.error_summary", {"count": sequential_error_count}))
            failures = sequential_error_count
        else:
            parallel_error_count: list[int] = [0]
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {}
                for json_file in input_files:
                    if cancel_event and cancel_event.is_set():
                        print(t("task.scripts.cancel_submit"))
                        break
                    json_file = Path(json_file)
                    f = pool.submit(
                        _process_one_script,
                        json_file,
                        config,
                        template,
                        token_store,
                        cancel_event,
                        overwrite,
                        state,
                        tracker,
                        context_override,
                        task_prompts,
                        confirm_artifact_overwrite,
                    )
                    futures[f] = json_file

                for future in as_completed(futures):
                    json_file = futures[future]
                    try:
                        result = future.result()
                        if isinstance(result, str) and result == "cancelled":
                            print(t("task.scripts.cancel_parallel"))
                            break
                        if isinstance(result, str):
                            print(t("task.scripts.error", {"name": json_file.stem, "detail": result}))
                            parallel_error_count[0] += 1
                    except Exception as e:  # noqa: BLE001 - isolate one failed script without aborting the batch
                        print(t("task.scripts.error", {"name": json_file.stem, "detail": e}))
                        parallel_error_count[0] += 1

                if cancel_event and cancel_event.is_set():
                    print(t("task.scripts.cancel_pending"))
                    for f in futures:
                        if not f.done():
                            f.cancel()

            if parallel_error_count[0]:
                print(t("task.scripts.error_summary", {"count": parallel_error_count[0]}))
            failures = parallel_error_count[0]
    if failures:
        raise RuntimeError(t("task.scripts.incomplete", {"count": failures}))


def _plan_voiceover_output_paths(config: AppConfig, day_label: str) -> tuple[Path, Path]:
    safe_day = plan_day_stem(day_label)
    return (
        config.scripts_dir / f"{safe_day}_plan_voiceover.json",
        config.scripts_dir / f"{safe_day}_plan_voiceover.md",
    )


def _index_key(value: Any) -> str:
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return str(value or "").strip().lower()


def _load_plan_voiceover_inputs(
    config: AppConfig,
    plan: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    source_inputs = plan.get("source_inputs", [])
    source_by_index: dict[str, dict[str, Any]] = {}
    if isinstance(source_inputs, list):
        for source in source_inputs:
            if isinstance(source, dict):
                source_by_index[_index_key(source.get("index"))] = source

    analyses: dict[str, dict[str, Any]] = {}
    if config.texts_dir.is_dir():
        for analysis_file in sorted(config.texts_dir.glob("*.json")):
            try:
                data = json.loads(analysis_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError):
                continue
            if not isinstance(data, dict):
                continue
            raw_index = data.get("index")
            if raw_index is None:
                prefix = analysis_file.stem.split("_", 1)[0]
                raw_index = prefix if prefix.isdigit() else analysis_file.stem
            analyses[_index_key(raw_index)] = data

    transcripts: dict[str, dict[str, Any]] = {}
    if config.transcripts_dir.is_dir():
        for transcript_file in sorted(config.transcripts_dir.glob("*_transcript.json")):
            try:
                data = json.loads(transcript_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeError):
                continue
            if not isinstance(data, dict):
                continue
            file_stem = transcript_file.stem.removesuffix("_transcript")
            transcripts[file_stem.lower()] = data
            source_stem = str(data.get("source_stem") or "").strip()
            if source_stem:
                transcripts[source_stem.lower()] = data
                if "_" in source_stem:
                    transcripts[source_stem.split("_", 1)[1].lower()] = data

    lineage_inputs: list[dict[str, Any]] = []
    sequence = plan.get("sequence", [])
    if isinstance(sequence, list):
        for segment in sequence:
            if not isinstance(segment, dict):
                continue
            key = _index_key(segment.get("index"))
            lineage_inputs.append(
                {
                    "source": source_by_index.get(key, {}),
                    "analysis": analyses.get(key, {}),
                }
            )
    return analyses, transcripts, lineage_inputs


def _plan_voiceover_lineage_fingerprint(
    config: AppConfig,
    plan: dict[str, Any],
    lineage_inputs: list[dict[str, Any]],
    template: str,
    task_prompts: dict[str, str] | None = None,
    context_override: str | None = None,
) -> str:
    """Fingerprint all inputs that can change a daily plan voiceover."""
    from clio.analyze import _prompt_context_parts
    from clio.prompt_overrides import resolve_prompt_template

    try:
        prompt = resolve_prompt_template("plan_voiceover", PLAN_VOICEOVER_PROMPT, config, task_prompts=task_prompts)
    except (OSError, UnicodeError):
        prompt = PLAN_VOICEOVER_PROMPT
    payload = json.dumps(
        {
            "task": _task_lineage_settings(config, "voiceover"),
            "prompt": prompt,
            "plan": plan,
            "analysis_inputs": lineage_inputs,
            "template": template,
            "target_words": getattr(config.script, "target_words", 80),
            "context": _prompt_context_parts(config, context_override),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _plan_voiceover_needs_overwrite(
    config: AppConfig,
    plan: dict[str, Any],
    day_label: str,
    template: str,
    lineage_inputs: list[dict[str, Any]],
    *,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> bool:
    out_json, out_md = _plan_voiceover_output_paths(config, day_label)
    if not out_json.is_file():
        return False
    try:
        existing = json.loads(out_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return True
    if not isinstance(existing, dict):
        return True
    edited = is_user_edited(existing)
    if overwrite or not config.analyze.skip_existing:
        return edited
    if not out_md.is_file():
        return edited
    current = _plan_voiceover_lineage_fingerprint(
        config, plan, lineage_inputs, template, task_prompts, context_override
    )
    return edited and existing.get("_lineage") != current


def plan_voiceover_artifact_overwrite_required(
    config: AppConfig,
    day_label: str,
    *,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> list[Path]:
    """Return daily voiceover artifacts that would replace user edits."""
    safe_day = plan_day_stem(day_label)
    plan_json = config.plans_dir / f"{safe_day}_plan.json"
    if not plan_json.is_file():
        return []
    try:
        plan = json.loads(plan_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return []
    if not isinstance(plan, dict):
        return []
    _, _, lineage_inputs = _load_plan_voiceover_inputs(config, plan)
    template = _load_script_template(config)
    if not _plan_voiceover_needs_overwrite(
        config,
        plan,
        day_label,
        template,
        lineage_inputs,
        overwrite=overwrite,
        context_override=context_override,
        task_prompts=task_prompts,
    ):
        return []
    out_json, out_md = _plan_voiceover_output_paths(config, day_label)
    return [path for path in (out_json, out_md) if path.is_file()]


def run_generate_plan_voiceovers(
    config: AppConfig,
    day_label: str = "day1",
    tracker: ProgressReporter | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    confirm_artifact_overwrite: bool = False,
    **kwargs: Any,
) -> dict[str, Any] | None:
    """Generate one voiceover artifact for every segment in a daily plan."""
    del files, kwargs
    config.scripts_dir.mkdir(parents=True, exist_ok=True)
    safe_day = plan_day_stem(day_label)
    plan_json = config.plans_dir / f"{safe_day}_plan.json"
    out_json, out_md = _plan_voiceover_output_paths(config, day_label)
    if not plan_json.is_file():
        print(t("task.plan_voiceover.no_plan", {"day": day_label, "path": plan_json.name}))
        return None
    try:
        plan = json.loads(plan_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise RuntimeError(t("task.plan_voiceover.plan_unreadable", {"day": day_label, "detail": exc})) from exc
    if not isinstance(plan, dict):
        raise RuntimeError(t("task.plan_voiceover.plan_unreadable", {"day": day_label, "detail": "not an object"}))
    sequence = plan.get("sequence", [])
    if not isinstance(sequence, list) or not sequence:
        print(t("task.plan_voiceover.no_segments", {"day": day_label}))
        return None

    analyses, transcripts, lineage_inputs = _load_plan_voiceover_inputs(config, plan)
    template = _load_script_template(config)
    current_lineage = _plan_voiceover_lineage_fingerprint(
        config, plan, lineage_inputs, template, task_prompts, context_override
    )
    source_inputs = plan.get("source_inputs", [])
    if not isinstance(source_inputs, list):
        source_inputs = []
    state_stems = {
        str(source.get("source_stem") or "").strip()
        for source in source_inputs
        if isinstance(source, dict) and str(source.get("source_stem") or "").strip()
    }

    if not overwrite and config.analyze.skip_existing and out_json.is_file() and out_md.is_file():
        try:
            existing = json.loads(out_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeError):
            existing = None
        if isinstance(existing, dict) and existing.get("_lineage") == current_lineage:
            state = ProcessingState(config.paths.output_dir)
            for stem in state_stems:
                state.mark(stem, "voiceover", "skipped")
            print(t("task.plan_voiceover.skip_existing", {"day": day_label}))
            return existing

    protected_overwrite = _plan_voiceover_needs_overwrite(
        config,
        plan,
        day_label,
        template,
        lineage_inputs,
        overwrite=overwrite,
        context_override=context_override,
        task_prompts=task_prompts,
    )
    if protected_overwrite and not confirm_artifact_overwrite:
        raise ArtifactOverwriteConfirmationRequired(
            t("task.artifact.overwrite_confirmation_required", {"items": out_json.name}),
            [path for path in (out_json, out_md) if path.is_file()],
        )

    source_by_index = {_index_key(source.get("index")): source for source in source_inputs if isinstance(source, dict)}
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    result_segments: list[dict[str, Any]] = []
    if tracker:
        tracker.update(
            phase="voiceover",
            total=len(sequence),
            current=0,
            message=t("task.plan_voiceover.progress", {"day": day_label, "current": 0, "total": len(sequence)}),
        )
    with timed(t("task.plan_voiceover.timed_label", {"day": day_label, "count": len(sequence)})):
        for position, planned_segment in enumerate(sequence):
            if cancel_event and cancel_event.is_set():
                print(t("task.plan_voiceover.cancelled", {"day": day_label}))
                return None
            if not isinstance(planned_segment, dict):
                raise RuntimeError(t("task.plan_voiceover.invalid_segment", {"position": position}))
            key = _index_key(planned_segment.get("index"))
            clip_data = analyses.get(key)
            if clip_data is None:
                raise RuntimeError(
                    t(
                        "task.plan_voiceover.missing_analysis",
                        {"position": position, "index": planned_segment.get("index", "")},
                    )
                )
            source = source_by_index.get(key, {})
            match_stem = str(source.get("match_stem") or "").strip()
            source_stem = str(source.get("source_stem") or "").strip()
            transcript = transcripts.get(match_stem.lower()) if match_stem else None
            if transcript is None and source_stem:
                transcript = transcripts.get(source_stem.lower())
            working_clip = dict(clip_data)
            try:
                working_clip["segment_offset_sec"] = float(source.get("segment_offset_sec", 0.0) or 0.0)
            except (TypeError, ValueError):
                working_clip["segment_offset_sec"] = 0.0
            if tracker:
                tracker.update(
                    phase="voiceover",
                    total=len(sequence),
                    current=position,
                    message=t(
                        "task.plan_voiceover.progress",
                        {"day": day_label, "current": position + 1, "total": len(sequence)},
                    ),
                )
            print(
                t(
                    "task.plan_voiceover.start_segment",
                    {
                        "day": day_label,
                        "position": position + 1,
                        "total": len(sequence),
                        "title": planned_segment.get("title", ""),
                    },
                )
            )
            result_segments.append(
                generate_plan_voiceover(
                    plan,
                    working_clip,
                    position,
                    template,
                    config,
                    transcript=transcript,
                    token_store=token_store,
                    cancel_event=cancel_event,
                    context_override=context_override,
                    task_prompts=task_prompts,
                    day_label=day_label,
                )
            )
            if tracker:
                tracker.next(
                    message=t(
                        "task.plan_voiceover.segment_done",
                        {"position": position + 1, "total": len(sequence)},
                    )
                )

    result: dict[str, Any] = {
        "_schema_version": 2,
        "_lineage": current_lineage,
        "day_label": day_label,
        "segments": result_segments,
    }
    if protected_overwrite:
        backup_artifacts([out_json, out_md])
    clear_user_edited(result)
    write_json_atomic(out_json, result)
    md_lines = [
        f"# {plan.get('day_title', day_label)}",
        "",
        t("md.plan_voiceover_theme", {"theme": plan.get("theme", "")}),
        "",
    ]
    for segment in result_segments:
        md_lines.extend(
            [
                f"## {segment.get('position', 0) + 1}. {segment.get('title', '')}",
                t("md.plan_voiceover_timeline", {"timeline": segment.get("use_timeline", "")}),
                t("md.plan_voiceover_hint", {"hint": segment.get("voiceover_hint", "")}),
                "",
                segment.get("voiceover", ""),
                "",
            ]
        )
    write_text_atomic(out_md, "\n".join(md_lines).rstrip() + "\n")
    state = ProcessingState(config.paths.output_dir)
    for stem in state_stems:
        state.mark(stem, "voiceover", "done")
    if tracker:
        tracker.log(t("task.plan_voiceover.done", {"day": day_label, "count": len(result_segments)}))
    print(f"  -> {out_json.name}")
    print(f"  -> {out_md.name}")
    return result

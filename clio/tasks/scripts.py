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
from clio.analyze import generate_voiceover
from clio.config import AppConfig
from clio.log import timed
from clio.processing_state import ProcessingState
from clio.progress import ProgressTracker
from clio.prompts import SCRIPT_PROMPT
from clio.schema import add_schema_version
from clio.tasks._helpers import _matches_selected_artifact, _selected_stems
from clio.utils import write_json_atomic, write_text_atomic


def _voiceover_lineage_fingerprint(
    config: AppConfig, data: dict, template: str, task_prompts: dict[str, str] | None = None
) -> str:
    """Fingerprint of everything that can change a cached voiceover script.

    Changing the voiceover prompt (builtin template, override, or runtime task
    prompt) or provider/model invalidates the skip_existing cache so users
    never silently keep a script generated under an older lineage.
    """
    try:
        task = config.ai.tasks.get("voiceover")
        provider = getattr(task, "provider", "") if task else ""
        model = getattr(task, "model", "") if task else ""
    except Exception:
        provider, model = "", ""
    from clio.prompt_overrides import resolve_prompt_template

    try:
        prompt = resolve_prompt_template("voiceover", SCRIPT_PROMPT, config, task_prompts=task_prompts)
    except Exception:
        prompt = SCRIPT_PROMPT
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "max_tokens": getattr(config.ai, "max_tokens", None),
            "analysis": data,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _process_one_script(
    json_file: Path,
    config: AppConfig,
    template: str,
    token_store: FileTokenUsageStore,
    cancel_event: threading.Event | None,
    overwrite: bool,
    state: ProcessingState,
    tracker: ProgressTracker | None,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
) -> bool | str:
    """Process a single voiceover script. Returns True (ok), False (skipped), or error string."""
    if cancel_event and cancel_event.is_set():
        return "cancelled"

    data = json.loads(json_file.read_text(encoding="utf-8"))
    data["index"] = data.get("index", json_file.stem[:3])
    orig_stem = Path(data.get("source_file") or json_file.stem).stem
    out = config.scripts_dir / f"{json_file.stem}_voiceover.json"
    if not overwrite and config.analyze.skip_existing and out.exists():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
            current = _voiceover_lineage_fingerprint(config, data, template, task_prompts)
        except (json.JSONDecodeError, OSError):
            existing, current = None, None
        else:
            if existing.get("_lineage") == current:
                state.mark(orig_stem, "voiceover", "skipped")
                if tracker:
                    tracker.next(message=f"跳过 {json_file.stem}")
                    tracker.log(f"跳过 {json_file.stem}（已存在）")
                return False
            if existing.get("_lineage") is None:
                # Legacy cache without lineage: accept it (best effort) by
                # stamping a fresh marker so only input/prompt changes skip.
                existing["_lineage"] = current
                write_json_atomic(out, existing)
                state.mark(orig_stem, "voiceover", "skipped")
                if tracker:
                    tracker.next(message=f"跳过 {json_file.stem}")
                    tracker.log(f"跳过 {json_file.stem}（已存在）")
                return False
            # Mismatched provider/prompt: regenerate below.

    if tracker:
        tracker.next(message=f"生成口播 {json_file.stem}")
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
    script["_lineage"] = _voiceover_lineage_fingerprint(config, data, template, task_prompts)
    write_json_atomic(out, script)
    state.mark(orig_stem, "voiceover", "done")
    if tracker:
        tracker.log(f"口播 {orig_stem} ✓")

    md_out = config.scripts_dir / f"{json_file.stem}_voiceover.md"
    md_content = (
        f"# {script.get('title', json_file.stem)} 口播\n\n"
        f"{script.get('voiceover', '')}\n\n"
        f"**剪辑建议**: {script.get('edit_tip', '')}\n"
    )
    write_text_atomic(md_out, md_content)
    print(f"  -> {md_out.name}")
    return True


def run_generate_scripts(
    config: AppConfig,
    tracker: ProgressTracker | None = None,
    single_file: Path | None = None,
    cancel_event: threading.Event | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    context_override: str | None = None,
    task_prompts: dict[str, str] | None = None,
    **kwargs: Any,
) -> None:
    config.scripts_dir.mkdir(parents=True, exist_ok=True)
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    state = ProcessingState(config.paths.output_dir)
    template = config.script.template_file.read_text(encoding="utf-8") if config.script.template_file.exists() else ""

    if single_file:
        input_files = [single_file]
    else:
        input_files = sorted(config.texts_dir.glob("*.json"))
    if files is not None:
        selected = _selected_stems(files)
        input_files = [f for f in input_files if _matches_selected_artifact(f, selected)]
    if not input_files:
        return
    if tracker:
        tracker.update(
            phase="voiceover", total=len(input_files), current=0, message=f"生成口播文案（{len(input_files)} 条）..."
        )

    max_workers = config.analyze.max_workers
    failures = 0
    with timed(f"run_generate_scripts（{len(input_files)} 个，workers={max_workers}）"):
        if max_workers <= 1:
            sequential_error_count = 0
            for json_file in input_files:
                if cancel_event and cancel_event.is_set():
                    print("[取消] voiceover 步骤被用户终止")
                    break
                json_file = Path(json_file)
                print(f"  [口播] {json_file.stem}")
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
                    )
                except Exception as e:
                    print(f"  [错误] {json_file.stem}: {e}")
                    sequential_error_count += 1
                    continue
                elapsed = time.monotonic() - t0
                if isinstance(result, str) and result == "cancelled":
                    break
                if isinstance(result, str):
                    print(f"  [错误] {json_file.stem}: {result}")
                    sequential_error_count += 1
                elif result is True:
                    print(f"  ✓ {elapsed:.1f}s")
            if sequential_error_count:
                print(f"  [警告] {sequential_error_count} 个 voiceover 生成失败")
            failures = sequential_error_count
        else:
            parallel_error_count: list[int] = [0]
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {}
                for json_file in input_files:
                    if cancel_event and cancel_event.is_set():
                        print("[取消] 取消剩余 voiceover 任务提交")
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
                    )
                    futures[f] = json_file

                for future in as_completed(futures):
                    json_file = futures[future]
                    try:
                        result = future.result()
                        if isinstance(result, str) and result == "cancelled":
                            print("[取消] voiceover 被用户取消")
                            break
                        if isinstance(result, str):
                            print(f"  [错误] {json_file.stem}: {result}")
                            parallel_error_count[0] += 1
                    except Exception as e:
                        print(f"  [错误] {json_file.stem}: {e}")
                        parallel_error_count[0] += 1

                if cancel_event and cancel_event.is_set():
                    print("[取消] 取消未完成 voiceover 任务")
                    for f in futures:
                        if not f.done():
                            f.cancel()

            if parallel_error_count[0]:
                print(f"  [警告] {parallel_error_count[0]} 个 voiceover 生成失败")
            failures = parallel_error_count[0]
    if failures:
        raise RuntimeError(f"口播文案未完整生成（{failures} 个失败）")

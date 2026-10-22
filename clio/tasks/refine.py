"""Refinement tasks — review and fix analysis texts / voiceover scripts."""

from __future__ import annotations

import json
import time
from pathlib import Path

from clio.ai.token_usage import FileTokenUsageStore
from clio.analyze import refine_script, refine_text
from clio.config import AppConfig
from clio.i18n import t
from clio.log import timed
from clio.tasks._helpers import (
    _eta_line,
    _matches_selected_artifact,
    _rewrite_script_md,
    _rewrite_text_file,
    _selected_stems,
)
from clio.utils import write_json_atomic


def _collect_target_files(path: Path | None, default_dir: Path, pattern: str = "*.json") -> list[Path]:
    """把 -i 参数或默认目录解析为要处理的文件列表。"""
    target = path or default_dir
    if not target.exists():
        raise FileNotFoundError(t("task.refine.path_missing", {"path": target}))
    if target.is_file():
        if target.suffix.lower() != ".json":
            raise ValueError(t("task.refine.json_only", {"path": target}))
        return [target]
    return sorted(target.glob(pattern))


def _load_analysis_for_script(script_path: Path, texts_dir: Path) -> dict | None:
    """根据口播文件名反查对应的素材分析（001_xxx_voiceover.json → 001_xxx.json）。"""
    stem = script_path.stem
    if stem.endswith("_voiceover"):
        analysis_stem = stem[: -len("_voiceover")]
    else:
        analysis_stem = stem
    candidate = texts_dir / f"{analysis_stem}.json"
    if candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def run_refine_texts(
    config: AppConfig,
    path: Path | None = None,
    fix: str | None = None,
    context_override: str | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    task_prompts: dict[str, str] | None = None,
) -> int:
    """审阅并修正 texts/*.json；同步重写同名 .txt。返回处理条数。

    fix 非空时：仅处理 -i 指定的单文件，prompt 切换为「按用户意见定向修正」。
    """
    if fix and (path is None or path.is_dir()):
        raise ValueError(t("task.refine.fix_requires_file"))
    target_files = _collect_target_files(path, config.texts_dir)
    if files is not None:
        selected = _selected_stems(files)
        target_files = [f for f in target_files if _matches_selected_artifact(f, selected)]
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    if not target_files:
        print(t("task.refine.no_json_files", {"path": path or config.texts_dir}))
        return 0
    label = "refine:fix:texts" if fix else "refine:texts"
    print(t("task.refine.target_files", {"label": label, "count": len(target_files)}))
    if fix:
        print(t("task.refine.fix_note", {"fix": fix}))
    completed = 0
    elapsed_total = 0.0
    with timed(t("task.refine.timed_label", {"label": label, "count": len(target_files)})):
        for i, json_file in enumerate(target_files, start=1):
            print(_eta_line("refine", i, len(target_files), json_file.name, completed, elapsed_total))
            t0 = time.monotonic()
            try:
                analysis = json.loads(json_file.read_text(encoding="utf-8"))
                refined = refine_text(
                    analysis,
                    config,
                    fix=fix,
                    context_override=context_override,
                    token_store=token_store,
                    task_prompts=task_prompts,
                )
            except Exception as e:
                print(t("task.refine.failed", {"detail": e}))
                continue
            finally:
                elapsed_total += time.monotonic() - t0
            completed += 1
            write_json_atomic(json_file, refined)
            txt_path = json_file.with_suffix(".txt")
            _rewrite_text_file(txt_path, refined)
            changelog = refined.get("_changelog") or []
            if changelog:
                print(t("task.refine.changes", {"count": len(changelog), "detail": "; ".join(changelog)[:120]}))
            else:
                print(t("task.refine.no_changes"))
    return completed


def run_refine_scripts(
    config: AppConfig,
    path: Path | None = None,
    fix: str | None = None,
    context_override: str | None = None,
    files: list[str] | None = None,
    overwrite: bool = False,
    task_prompts: dict[str, str] | None = None,
) -> int:
    """审阅并修正 scripts/*_voiceover.json；同步重写同名 .md。

    fix 非空时：仅处理 -i 指定的单文件。
    """
    if fix and (path is None or path.is_dir()):
        raise ValueError(t("task.refine.fix_requires_file"))
    target_files = _collect_target_files(path, config.scripts_dir, pattern="*_voiceover.json")
    if files is not None:
        selected = _selected_stems(files)
        target_files = [f for f in target_files if _matches_selected_artifact(f, selected)]
    token_store = FileTokenUsageStore(str(config.paths.output_dir))
    if not target_files:
        print(t("task.refine.no_voiceover_files", {"path": path or config.scripts_dir}))
        return 0
    label = "refine:fix:scripts" if fix else "refine:scripts"
    print(t("task.refine.target_files", {"label": label, "count": len(target_files)}))
    if fix:
        print(t("task.refine.fix_note", {"fix": fix}))
    completed = 0
    elapsed_total = 0.0
    with timed(t("task.refine.timed_label", {"label": label, "count": len(target_files)})):
        for i, json_file in enumerate(target_files, start=1):
            print(_eta_line("refine", i, len(target_files), json_file.name, completed, elapsed_total))
            t0 = time.monotonic()
            try:
                script = json.loads(json_file.read_text(encoding="utf-8"))
                analysis = _load_analysis_for_script(json_file, config.texts_dir)
                refined = refine_script(
                    script,
                    analysis,
                    config,
                    fix=fix,
                    context_override=context_override,
                    token_store=token_store,
                    task_prompts=task_prompts,
                )
            except Exception as e:
                print(t("task.refine.failed", {"detail": e}))
                continue
            finally:
                elapsed_total += time.monotonic() - t0
            completed += 1
            write_json_atomic(json_file, refined)
            md_path = json_file.with_name(json_file.stem + ".md")
            _rewrite_script_md(md_path, refined)
            changelog = refined.get("_changelog") or []
            if changelog:
                print(t("task.refine.changes", {"count": len(changelog), "detail": "; ".join(changelog)[:120]}))
            else:
                print(t("task.refine.no_changes"))
    return completed

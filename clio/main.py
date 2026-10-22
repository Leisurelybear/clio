#!/usr/bin/env python3
"""Vlog 剪辑辅助工具 CLI"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from clio.config import apply_run_paths, load_config
from clio.doctor import is_virtualenv_python, run_doctor
from clio.i18n import set_active_locale, t
from clio.log import setup_logging
from clio.shutdown import before_stop, install_hooks
from clio.utils import discover_ffmpeg_bin, resolve_binary, safe_basename

PLACEHOLDER_KEYS = {"your_api_key_here", "YOUR_API_KEY", ""}


def _add_io_args(
    parser: argparse.ArgumentParser,
    *,
    force_help: str | None = None,
) -> None:
    parser.add_argument(
        "-p",
        "--project",
        type=Path,
        help=t("cli.help.io.project"),
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=t("cli.help.io.output"),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=argparse.SUPPRESS,
        help=force_help or t("cli.help.io.force"),
    )


def _prepare_config(config_path: Path, args: argparse.Namespace):
    raw_project = getattr(args, "project", None)
    raw_input = getattr(args, "input", None)
    project_dir = raw_project or raw_input
    if project_dir is not None and not project_dir.is_dir():
        project_dir = None
    config = load_config(config_path, project_dir=project_dir)
    output_override = getattr(args, "output", None)
    if output_override:
        config = apply_run_paths(config, output_override)
    return config


def _explicit_path_error(args: argparse.Namespace) -> str | None:
    """Validate explicitly supplied project/input paths before config loading."""
    project_path = getattr(args, "project", None)
    if project_path is not None and not project_path.is_dir():
        if not project_path.exists():
            return t("cli.path.project_missing", {"path": project_path})
        return t("cli.path.project_not_dir", {"path": project_path})

    input_path = getattr(args, "input", None)
    if input_path is None:
        return None
    if not input_path.exists():
        return t("cli.path.input_missing", {"path": input_path})
    if input_path.is_dir():
        return None
    if args.command in {"compress", "analyze", "scripts", "refine", "compare-models"}:
        return None
    return t("cli.path.input_needs_dir", {"path": input_path})


def run_check(config_path: Path, project_dir: Path | None = None) -> int:
    config = load_config(config_path, project_dir=project_dir)

    ok = True

    def status(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "OK" if passed else "FAIL"
        suffix = f" - {detail}" if detail else ""
        print(f"  [{mark}] {name}{suffix}")
        if not passed:
            ok = False

    print(t("cli.check.title"))
    venv_ok = is_virtualenv_python()
    status(
        t("cli.check.venv"),
        venv_ok,
        sys.executable + (t("cli.check.venv_hint") if not venv_ok else ""),
    )

    setup_script = "setup.ps1" if os.name == "nt" else "setup.sh"
    ffmpeg = config.paths.ffmpeg or discover_ffmpeg_bin("ffmpeg")
    ffprobe = config.paths.ffprobe or discover_ffmpeg_bin("ffprobe")
    status("ffmpeg", bool(ffmpeg), ffmpeg or t("cli.check.binary_missing_setup", {"setup": setup_script}))
    status("ffprobe", bool(ffprobe), ffprobe or t("cli.check.binary_missing"))

    from clio.tasks._video_loader import source_videos

    project_dir = config.project_dir
    videos = source_videos(config)
    if project_dir and project_dir.is_dir():
        existing = sum(1 for v in videos if v.is_file())
        offline = len(videos) - existing
        if not videos:
            detail = t("cli.check.video_empty", {"path": project_dir})
        elif offline:
            detail = t(
                "cli.check.video_offline",
                {"path": project_dir, "count": len(videos), "offline": offline},
            )
        else:
            detail = t("cli.check.video_ready", {"path": project_dir, "count": len(videos)})
        mark = "OK" if videos and offline == 0 else ("WARN" if videos else "WARN")
        print(f"  [{mark}] {t('cli.check.project_row', {'name': project_dir.name, 'detail': detail})}")
        if not videos:
            print(t("cli.check.video_hint"))
    else:
        print(t("cli.check.no_project"))

    print(t("cli.check.ai_tasks"))
    for task_name, task_cfg in config.ai.tasks.items():
        provider = config.ai.providers.get(task_cfg.provider)
        key_ok = provider is not None and provider.api_key not in PLACEHOLDER_KEYS
        detail = f"{task_cfg.provider}/{task_cfg.model}"
        if provider and not key_ok:
            env_name = provider.api_key_env or t("cli.check.no_env_name")
            detail += t("cli.check.key_env", {"env": env_name})
        status(f"  {task_name}", bool(provider) and key_ok, detail)

    status(
        t("cli.check.proxy"),
        not config.proxy.enabled or bool(config.proxy.url),
        config.proxy.url if config.proxy.enabled else t("cli.check.proxy_disabled"),
    )

    if not project_dir or not project_dir.is_dir():
        print()
        print(t("cli.check.usage"))
        example_path = t("cli.check.example_path") if os.name == "nt" else "/path/to/project"
        print(t("cli.check.example", {"path": example_path}))
    print(t("cli.done_exit_code", {"code": 0 if ok else 1}))
    return 0 if ok else 1


def cmd_tokens(args):
    config = _prepare_config(Path(args.config), args)
    import json

    from clio.ai.token_usage import FileTokenUsageStore

    store = FileTokenUsageStore(str(config.paths.output_dir))
    stats = store.get_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=t("cli.description"),
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help=t("cli.help.config"),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=t("cli.help.force"),
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_compress = sub.add_parser("compress", help=t("cli.help.compress"))
    _add_io_args(p_compress)

    p_analyze = sub.add_parser("analyze", help=t("cli.help.analyze"))
    _add_io_args(p_analyze)

    p_scripts = sub.add_parser("scripts", help=t("cli.help.scripts"))
    _add_io_args(p_scripts)

    p_label = sub.add_parser("label", help=t("cli.help.label"))
    _add_io_args(p_label)

    p_check = sub.add_parser("check", help=t("cli.help.check"))
    p_check.add_argument("-p", "--project", type=Path, help=t("cli.help.check.project"))
    p_check.add_argument("-i", "--input", type=Path, help=argparse.SUPPRESS)

    p_doctor = sub.add_parser("doctor", help=t("cli.help.doctor"))
    p_doctor.add_argument("-p", "--project", type=Path, help=t("cli.help.doctor.project"))
    p_doctor.add_argument("-i", "--input", type=Path, help=argparse.SUPPRESS)

    p_plan = sub.add_parser("plan", help=t("cli.help.plan"))
    _add_io_args(p_plan)
    p_plan.add_argument("--day", default="day1", help=t("cli.help.plan.day"))
    p_plan.add_argument("--all-days", action="store_true", help=t("cli.help.plan.all_days"))
    p_plan.add_argument("--no-transcripts", action="store_true", help=t("cli.help.plan.no_transcripts"))

    p_run = sub.add_parser("run", help=t("cli.help.run"))
    _add_io_args(p_run)
    p_run.add_argument("--day", default="day1", help=t("cli.help.plan.day"))

    p_cut = sub.add_parser("cut", help=t("cli.help.cut"))
    _add_io_args(p_cut, force_help=t("cli.help.cut.force"))
    p_cut.add_argument("--day", default="day1", help=t("cli.help.cut.day"))
    p_cut.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=t("cli.help.cut.out_dir"),
    )
    p_cut.add_argument(
        "--reencode",
        action="store_true",
        help=t("cli.help.cut.reencode"),
    )
    p_cut.add_argument(
        "--source",
        choices=["compressed", "original"],
        default="compressed",
        help=t("cli.help.cut.source"),
    )
    p_refine = sub.add_parser("refine", help=t("cli.help.refine"))
    p_refine.add_argument("-p", "--project", type=Path, help=t("cli.help.io.project"))
    p_refine.add_argument("-o", "--output", type=Path, help=t("cli.help.io.output"))
    p_refine.add_argument(
        "--target",
        "-t",
        choices=["texts", "scripts", "all"],
        default="all",
        help=t("cli.help.refine.target"),
    )
    p_refine.add_argument(
        "-i",
        "--input",
        type=Path,
        help=t("cli.help.refine.input"),
    )
    p_refine.add_argument(
        "--fix",
        "-f",
        type=str,
        default="",
        help=t("cli.help.refine.fix"),
    )
    p_refine.add_argument(
        "--context",
        "-C",
        type=str,
        default="",
        help=t("cli.help.refine.context"),
    )

    p_compare = sub.add_parser("compare-models", help=t("cli.help.compare"))
    p_compare.add_argument("-i", "--input", type=Path, required=True, help=t("cli.help.compare.input"))
    p_compare.add_argument(
        "--models",
        nargs="+",
        required=True,
        help=t("cli.help.compare.models"),
    )
    p_compare.add_argument("--out-dir", type=Path, default=None, help=t("cli.help.compare.out_dir"))
    p_compare.add_argument(
        "--context",
        "-C",
        type=str,
        default="",
        help=t("cli.help.compare.context"),
    )

    p_migrate = sub.add_parser("migrate-config", help=t("cli.help.migrate_config"))
    p_migrate.add_argument("--projects-root", type=Path, default=None, help=t("cli.help.migrate_config.projects_root"))

    p_migrate_projects = sub.add_parser(
        "migrate",
        help=t("cli.help.migrate"),
    )
    p_migrate_projects.add_argument(
        "--from",
        type=Path,
        default=None,
        dest="from_path",
        help=t("cli.help.migrate.from"),
    )

    p_serve = sub.add_parser("serve", help=t("cli.help.serve"))
    p_serve.add_argument("--host", default="127.0.0.1", help=t("cli.help.serve.host"))
    p_serve.add_argument("--port", type=int, default=8765, help=t("cli.help.serve.port"))
    p_serve.add_argument("--no-browser", action="store_true", help=t("cli.help.serve.no_browser"))
    p_serve.add_argument("--token", type=str, default=None, help=t("cli.help.serve.token"))

    sub.add_parser(
        "desktop",
        help=t("cli.help.desktop"),
    )

    p_tokens = sub.add_parser("tokens", help=t("cli.help.tokens"))
    _add_io_args(p_tokens)
    p_tokens.set_defaults(func=cmd_tokens)

    p_export = sub.add_parser("export", help=t("cli.help.export"))
    _add_io_args(p_export, force_help=t("cli.help.export.force"))
    p_export.add_argument("--format", default="jianying", choices=["jianying"], help=t("cli.help.export.format"))
    p_export.add_argument("--day", default="day1", help=t("cli.help.export.day"))
    # -o/--output already from _add_io_args (output_dir override for config).
    # Export destination uses --out-dir to avoid argparse conflict.
    p_export.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        dest="export_out_dir",
        help=t("cli.help.export.out_dir"),
    )
    _p_verify = sub.add_parser("verify", help=t("cli.help.verify"))
    _add_io_args(_p_verify)

    p_reindex = sub.add_parser("reindex", help=t("cli.help.reindex"))
    _add_io_args(p_reindex)
    p_reindex.add_argument(
        "--name",
        type=str,
        default="",
        dest="project_name",
        help=t("cli.help.reindex.name"),
    )

    p_transcribe = sub.add_parser("transcribe", help=t("cli.help.transcribe"))
    _add_io_args(p_transcribe, force_help=t("cli.help.transcribe.force"))

    p_whisper = sub.add_parser("whisper", help=t("cli.help.whisper"))
    whisper_sub = p_whisper.add_subparsers(dest="whisper_command", required=True)
    whisper_sub.add_parser("install", help=t("cli.help.whisper.install"))
    whisper_sub.add_parser("check", help=t("cli.help.whisper.check"))

    args = parser.parse_args(argv)
    config_path = Path(args.config)

    path_error = _explicit_path_error(args)
    if path_error:
        print(t("cli.error", {"detail": path_error}), file=sys.stderr)
        return 2

    if args.command == "doctor":
        project = getattr(args, "project", None) or getattr(args, "input", None)
        return run_doctor(config_path, project)

    if args.command == "desktop":
        # Self-contained: app.main loads config + starts server + window.
        # Early return avoids _prepare_config / before_stop wrapping a long-lived GUI.
        from clio.desktop.app import main as run_desktop

        return run_desktop(config_path=config_path)

    # serve: timed startup breadcrumbs (cold import cost is before main())
    _serve_t0: float | None = None
    if args.command == "serve":
        import time as _time

        _serve_t0 = _time.perf_counter()
        print("[startup] python main.py serve — loading config…")

    base_config = load_config(config_path)
    set_active_locale(base_config.ui.language)
    if _serve_t0 is not None:
        import time as _time

        print(f"[startup] config loaded ({(_time.perf_counter() - _serve_t0) * 1000:.0f}ms)")
        t_log = _time.perf_counter()
        setup_logging(base_config.paths.logs_dir)
        install_hooks()
        print(f"[startup] logging + hooks ({(_time.perf_counter() - t_log) * 1000:.0f}ms)")
    else:
        setup_logging(base_config.paths.logs_dir)
        install_hooks()

    if args.command == "check":
        project = getattr(args, "project", None) or getattr(args, "input", None)
        return run_check(config_path, project)

    elif args.command == "transcribe":
        from clio.tasks.transcribe import run_transcribe_all

        config = _prepare_config(config_path, args)
        config.analyze.skip_existing = not getattr(args, "force", False)
        return run_transcribe_all(config)

    elif args.command == "whisper":
        from clio.whisper_cli import run_whisper_check, run_whisper_install

        if args.whisper_command == "install":
            return run_whisper_install(config_path)
        elif args.whisper_command == "check":
            return run_whisper_check(config_path)

    elif args.command == "compare-models":
        from clio.tasks.compare_models import run_compare_models

        config = load_config(config_path)
        context_override = (getattr(args, "context", "") or "").strip() or None
        return run_compare_models(
            config,
            args.input,
            args.models,
            output_dir=args.out_dir,
            context_override=context_override,
        )

    # ── Single-file detection ────────────────────────────────────────
    # If -i points to a single file for compress/analyze/scripts,
    # don't let _prepare_config set it as input_dir (refine already handles -i correctly)
    single_file: Path | None = None
    if args.command in ("compress", "analyze", "scripts"):
        raw_input = getattr(args, "input", None)
        if raw_input is not None and raw_input.is_file():
            single_file = raw_input
            args.input = None  # null out so _prepare_config doesn't use it

    config = _prepare_config(config_path, args)
    if _serve_t0 is not None:
        import time as _time

        print(f"[startup] project config prepared ({(_time.perf_counter() - _serve_t0) * 1000:.0f}ms from serve start)")
    if args.force:
        config.analyze.skip_existing = False

    context_override = (getattr(args, "context", "") or "").strip() or None

    try:
        if args.command == "verify":
            from clio.tasks.verify import run_verify

            return run_verify(config)
        elif args.command == "reindex":
            from clio.tasks.reindex import run_reindex

            return run_reindex(config)
        elif args.command == "compress":
            from clio.pipeline import run_compress_all
            from clio.tasks.reindex import auto_reindex_if_needed

            auto_reindex_if_needed(config)
            run_compress_all(config, single_file=single_file)
        elif args.command == "analyze":
            from clio.pipeline import run_analyze_all
            from clio.tasks.reindex import auto_reindex_if_needed

            auto_reindex_if_needed(config)
            run_analyze_all(config, single_file=single_file)
        elif args.command == "scripts":
            from clio.pipeline import run_generate_scripts

            run_generate_scripts(config, single_file=single_file)
        elif args.command == "label":
            from clio.pipeline import run_label_videos
            from clio.tasks.reindex import auto_reindex_if_needed

            auto_reindex_if_needed(config)
            run_label_videos(config)
        elif args.command == "plan":
            from clio.pipeline import run_plan_all_days, run_plan_vlog
            from clio.tasks.reindex import auto_reindex_if_needed

            auto_reindex_if_needed(config)
            config.plan.use_transcripts = not getattr(args, "no_transcripts", False)
            if getattr(args, "all_days", False):
                run_plan_all_days(config)
            else:
                run_plan_vlog(config, args.day)
        elif args.command == "run":
            from clio.pipeline import run_full_pipeline

            run_full_pipeline(config, args.day)
        elif args.command == "refine":
            from clio.pipeline import run_refine_scripts, run_refine_texts

            if not config.ai.context and not context_override:
                print(t("cli.refine.no_context"), file=sys.stderr)
            fix = (getattr(args, "fix", "") or "").strip() or None
            target_path = getattr(args, "input", None)
            if fix and (target_path is None or target_path.is_dir()):
                print(t("cli.refine.fix_requires_file"), file=sys.stderr)
                return 1
            try:
                if args.target in ("texts", "all"):
                    run_refine_texts(config, target_path, fix=fix, context_override=context_override)
                if args.target in ("scripts", "all"):
                    if args.target == "all" and target_path is not None:
                        scripts_path = None
                    else:
                        scripts_path = target_path
                    run_refine_scripts(config, scripts_path, fix=fix, context_override=context_override)
            except ValueError as e:
                print(t("cli.error", {"detail": e}), file=sys.stderr)
                return 1
        elif args.command == "cut":
            from clio.pipeline import run_cut_all
            from clio.plan_model import Plan
            from clio.plan_readiness import (
                check_plan_export_readiness,
                collect_project_indices,
                readiness_block_payload,
            )
            from clio.tasks.reindex import auto_reindex_if_needed

            auto_reindex_if_needed(config)
            plan_path = config.plans_dir / f"{safe_basename(args.day, max_len=60)}_plan.json"
            if not plan_path.is_file():
                print(t("cli.error", {"detail": t("cli.plan.not_found", {"path": plan_path})}), file=sys.stderr)
                return 1
            plan = Plan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
            known, offline = collect_project_indices(config)
            ready = check_plan_export_readiness(plan, known_indices=known, offline_indices=offline, source=args.source)
            blocked = readiness_block_payload(ready, force=bool(getattr(args, "force", False)))
            if blocked is not None:
                print(t("cli.error", {"detail": blocked["error"]}), file=sys.stderr)
                for issue in ready.errors + (ready.warnings if not getattr(args, "force", False) else []):
                    print(f"  [{issue.level}] {issue.message}", file=sys.stderr)
                return 1
            run_cut_all(
                config,
                day_label=args.day,
                output_dir=args.out_dir,
                reencode=args.reencode,
                source=args.source,
            )
        elif args.command == "migrate-config":
            from clio.ui.services.file_service import _migrate_project_configs

            root = args.projects_root or (config.project_dir or config_path.parent)
            updated, errors = _migrate_project_configs(root)
            print(t("cli.migrate_config.updated", {"count": updated}))
            if errors:
                print(t("cli.errors_header"))
                for err in errors:
                    print(t("cli.list_error", {"error": err}))
            return 1 if errors else 0
        elif args.command == "migrate":
            from clio.tasks.migrate import run_migrate

            updated, errors = run_migrate(config_path, getattr(args, "from_path", None))
            print(t("cli.migrate.updated", {"count": updated}))
            for err in errors:
                print(t("cli.indented_error", {"error": err}))
            return 1 if errors else 0
        elif args.command == "serve":
            import time as _time

            t_ui = _time.perf_counter()
            print("[startup] importing UI modules…")
            from clio.ui import run as run_ui

            print(f"[startup] UI modules ready ({(_time.perf_counter() - t_ui) * 1000:.0f}ms)")
            return run_ui(
                config,
                config_path=config_path,
                host=args.host,
                port=args.port,
                open_browser=not args.no_browser,
                api_token=args.token or config.server.api_token,
            )
        elif args.command == "tokens":
            cmd_tokens(args)
            return 0
        elif args.command == "export":
            from clio.export import export_plan
            from clio.plan_model import Plan
            from clio.plan_readiness import (
                check_plan_export_readiness,
                collect_project_indices,
                readiness_block_payload,
            )

            plan_path = config.plans_dir / f"{safe_basename(args.day, max_len=60)}_plan.json"
            if not plan_path.is_file():
                print(t("cli.error", {"detail": t("cli.plan.not_found", {"path": plan_path})}), file=sys.stderr)
                return 1
            plan = Plan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
            known, offline = collect_project_indices(config)
            ready = check_plan_export_readiness(plan, known_indices=known, offline_indices=offline, source="original")
            blocked = readiness_block_payload(ready, force=bool(getattr(args, "force", False)))
            if blocked is not None:
                print(t("cli.error", {"detail": blocked["error"]}), file=sys.stderr)
                for issue in ready.errors + (ready.warnings if not getattr(args, "force", False) else []):
                    print(f"  [{issue.level}] {issue.message}", file=sys.stderr)
                return 1
            out_dir = (
                getattr(args, "export_out_dir", None)
                or getattr(args, "output", None)
                or config.paths.output_dir / "export" / f"{args.day}_{args.format}"
            )
            export_plan(
                args.format,
                plan_path,
                out_dir,
                day_label=args.day,
                project_dir=config.project_dir,
                ffprobe=resolve_binary(config.paths.ffprobe, "ffprobe"),
                texts_dir=config.texts_dir,
                canvas_ratio=config.export.canvas_ratio,
                index_width=config.naming.index_width,
            )
            return 0
    except FileNotFoundError as e:
        print(t("cli.error", {"detail": e}), file=sys.stderr)
        return 1
    except Exception as e:
        print(t("cli.error", {"detail": e}), file=sys.stderr)
        return 1
    finally:
        before_stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
from clio.log import setup_logging
from clio.shutdown import before_stop, install_hooks
from clio.utils import discover_ffmpeg_bin, safe_basename

PLACEHOLDER_KEYS = {"your_api_key_here", "YOUR_API_KEY", ""}


def _add_io_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-p",
        "--project",
        type=Path,
        help="项目目录（包含 project.yaml）",
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
        help="输出文件夹覆盖",
    )


def _prepare_config(config_path: Path, args: argparse.Namespace):
    raw_project = getattr(args, "project", None)
    raw_input = getattr(args, "input", None)
    project_dir = raw_project or raw_input
    if not project_dir or not project_dir.is_dir():
        project_dir = Path.cwd()
    config = load_config(config_path, project_dir=project_dir)
    output_override = getattr(args, "output", None)
    if output_override:
        config = apply_run_paths(config, output_override)
    return config


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

    print("环境检查:")
    venv_ok = is_virtualenv_python()
    status(
        "虚拟环境",
        venv_ok,
        sys.executable + (" (建议使用 .venv 虚拟环境)" if not venv_ok else ""),
    )

    setup_script = "setup.ps1" if os.name == "nt" else "setup.sh"
    ffmpeg = config.paths.ffmpeg or discover_ffmpeg_bin("ffmpeg")
    ffprobe = config.paths.ffprobe or discover_ffmpeg_bin("ffprobe")
    status("ffmpeg", bool(ffmpeg), ffmpeg or f"未找到，运行 {setup_script}")
    status("ffprobe", bool(ffprobe), ffprobe or "未找到")

    from clio.tasks._video_loader import source_videos

    project_dir = config.project_dir
    videos = source_videos(config)
    if project_dir and project_dir.is_dir():
        existing = sum(1 for v in videos if v.is_file())
        offline = len(videos) - existing
        detail = f"{project_dir} ({len(videos)} 个视频"
        if offline:
            detail += f", 其中 {offline} 个离线"
        detail += ")"
        mark = "OK" if videos and offline == 0 else ("WARN" if videos else "WARN")
        print(f"  [{mark}] 项目 '{project_dir.name}' - {detail}")
        if not videos:
            print("       提示: 在 Web UI 视频管理中勾选素材，或运行 python main.py migrate")
    else:
        print("  [WARN] 未指定项目，使用 -p/--project 选择项目目录")

    print("\nAI 任务配置:")
    for task_name, task_cfg in config.ai.tasks.items():
        provider = config.ai.providers.get(task_cfg.provider)
        key_ok = provider is not None and provider.api_key not in PLACEHOLDER_KEYS
        detail = f"{task_cfg.provider}/{task_cfg.model}"
        if provider and not key_ok:
            env_name = provider.api_key_env or "<未设置 api_key_env>"
            detail += f" (需设置 {env_name} 于 .env / 环境变量)"
        status(f"  {task_name}", bool(provider) and key_ok, detail)

    status(
        "代理",
        not config.proxy.enabled or bool(config.proxy.url),
        config.proxy.url if config.proxy.enabled else "未启用",
    )

    if not project_dir or not project_dir.is_dir():
        print()
        print("用法: python main.py <命令> -p <项目目录>")
        example_path = "G:\\vlog-projects\\巴黎行" if os.name == "nt" else "/path/to/project"
        print(f"      例如: python main.py run -p {example_path}")
    print(f"\ndone (exit code: {0 if ok else 1})")
    return 0 if ok else 1


def cmd_tokens(args):
    config = load_config(args.config)
    import json

    from clio.ai.token_usage import FileTokenUsageStore

    store = FileTokenUsageStore(str(config.paths.output_dir))
    stats = store.get_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Vlog 剪辑辅助工具：文件夹输入 → 压缩 → AI 分析 → 口播 → 规划",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="config.yaml",
        help="配置文件路径（默认 config.yaml）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略已存在的输出，强制重新生成（覆盖 config.yaml 的 skip_existing）",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_compress = sub.add_parser("compress", help="仅压缩素材文件夹中的视频")
    _add_io_args(p_compress)

    p_analyze = sub.add_parser("analyze", help="压缩 + AI 分析，输出文本和 CSV")
    _add_io_args(p_analyze)

    p_scripts = sub.add_parser("scripts", help="根据分析结果生成口播文案")
    _add_io_args(p_scripts)

    p_label = sub.add_parser("label", help="在压缩视频上烧录序号标注")
    _add_io_args(p_label)

    p_check = sub.add_parser("check", help="检查环境配置是否就绪")
    p_check.add_argument("-p", "--project", type=Path, help="检查指定项目目录")
    p_check.add_argument("-i", "--input", type=Path, help=argparse.SUPPRESS)

    p_doctor = sub.add_parser("doctor", help="诊断本机环境、依赖、配置和常见运行风险")
    p_doctor.add_argument("-p", "--project", type=Path, help="诊断指定项目目录")
    p_doctor.add_argument("-i", "--input", type=Path, help=argparse.SUPPRESS)

    p_plan = sub.add_parser("plan", help="生成单日 vlog 剪辑规划")
    _add_io_args(p_plan)
    p_plan.add_argument("--day", default="day1", help="日 vlog 标签")
    p_plan.add_argument("--all-days", action="store_true", help="按分析结果中的 day/day_label 批量生成多日规划")
    p_plan.add_argument("--no-transcripts", action="store_true", help="不注入语音转录信息到 prompt")

    p_run = sub.add_parser("run", help="一键执行完整流程")
    _add_io_args(p_run)
    p_run.add_argument("--day", default="day1", help="日 vlog 标签")

    p_cut = sub.add_parser("cut", help="根据规划从视频中裁剪片段")
    _add_io_args(p_cut)
    p_cut.add_argument("--day", default="day1", help="日 vlog 标签（默认 day1）")
    p_cut.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="输出目录（默认 output/cuts/<day>/）",
    )
    p_cut.add_argument(
        "--reencode",
        action="store_true",
        help="重新编码（默认 -c copy 快剪；启用则用 libx264 精确剪）",
    )
    p_cut.add_argument(
        "--source",
        choices=["compressed", "original"],
        default="compressed",
        help="视频来源（compressed: 从压缩版裁剪 / original: 从原片裁剪）",
    )
    p_cut.add_argument(
        "--force",
        action="store_true",
        help="忽略规划就绪警告（error 仍会阻止）",
    )

    p_refine = sub.add_parser("refine", help="用 AI + trip 上下文 审阅并修正已有分析/口播")
    p_refine.add_argument(
        "--target",
        "-t",
        choices=["texts", "scripts", "all"],
        default="all",
        help="要 refine 的目标（默认 all）",
    )
    p_refine.add_argument(
        "-i",
        "--input",
        type=Path,
        help="指定单个 .json 文件或目录；省略则处理整个 texts/ 或 scripts/",
    )
    p_refine.add_argument(
        "--fix",
        "-f",
        type=str,
        default="",
        help=(
            "指定具体修改意见（必须配合 -i 单文件使用）。例: --fix '把 location 从曼谷素万那普机场改成巴黎戴高乐机场'"
        ),
    )
    p_refine.add_argument(
        "--context",
        "-C",
        type=str,
        default="",
        help="临时上下文说明，附加到 ai.context 之后（仅本次 refine 生效）",
    )

    p_compare = sub.add_parser("compare-models", help="用多个视频模型分析同一视频并生成对比报告")
    p_compare.add_argument("-i", "--input", type=Path, required=True, help="要分析的视频文件")
    p_compare.add_argument(
        "--models",
        nargs="+",
        required=True,
        help="模型列表，格式 provider:model；也支持逗号分隔",
    )
    p_compare.add_argument("--out-dir", type=Path, default=None, help="报告输出目录（默认 output/model_compare/）")
    p_compare.add_argument(
        "--context",
        "-C",
        type=str,
        default="",
        help="临时上下文说明，附加到 ai.context 之后（仅本次对比生效）",
    )

    p_migrate = sub.add_parser("migrate-config", help="扫描已有项目的 project.yaml，补充缺失的 provider 配置字段")
    p_migrate.add_argument("--projects-root", type=Path, default=None, help="项目根目录（扫描子目录中的 project.yaml）")

    p_migrate_projects = sub.add_parser(
        "migrate",
        help="将旧项目迁移到新结构（独立 project_dir + videos.json）",
    )
    p_migrate_projects.add_argument(
        "--from",
        type=Path,
        default=None,
        dest="from_path",
        help="指定要迁移的项目路径（默认扫描注册表）",
    )

    p_serve = sub.add_parser("serve", help="启动本地 web UI（浏览器里可视化编辑 AI 输出）")
    p_serve.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1，不暴露到局域网）")
    p_serve.add_argument("--port", type=int, default=8765, help="端口（默认 8765）")
    p_serve.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p_serve.add_argument("--token", type=str, default=None, help="API Token（留空则自动生成）")

    sub.add_parser(
        "desktop",
        help="启动桌面窗口（pywebview + 本地 UI，系统文件对话框）",
    )

    p_tokens = sub.add_parser("tokens", help="查看 token 使用统计")
    p_tokens.set_defaults(func=cmd_tokens)

    p_export = sub.add_parser("export", help="导出 plan 到剪辑软件草稿")
    _add_io_args(p_export)
    p_export.add_argument("--format", default="jianying", choices=["jianying"], help="导出格式")
    p_export.add_argument("--day", default="day1", help="日 vlog 标签（默认 day1）")
    # -o/--output already from _add_io_args (output_dir override for config).
    # Export destination uses --out-dir to avoid argparse conflict.
    p_export.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        dest="export_out_dir",
        help="导出草稿输出目录（默认 output/export/<day>_<format>/）",
    )
    p_export.add_argument(
        "--force",
        action="store_true",
        help="忽略规划就绪警告（error 仍会阻止）",
    )

    _p_verify = sub.add_parser("verify", help="校验压缩文件与原始视频的完整性")
    _add_io_args(_p_verify)

    p_reindex = sub.add_parser("reindex", help="重建 .vmeta / .vindex sidecar 文件")
    _add_io_args(p_reindex)
    p_reindex.add_argument(
        "--name",
        type=str,
        default="",
        dest="project_name",
        help="按项目名匹配（可选；优先使用 -p/--project 目录）",
    )

    p_transcribe = sub.add_parser("transcribe", help="Whisper ASR 语音转录（需先安装 faster-whisper）")
    _add_io_args(p_transcribe)
    p_transcribe.add_argument("--force", action="store_true", help="忽略已有转录，重新生成")

    p_whisper = sub.add_parser("whisper", help="Whisper 环境管理（安装/检测）")
    whisper_sub = p_whisper.add_subparsers(dest="whisper_command", required=True)
    whisper_sub.add_parser("install", help="安装 faster-whisper 依赖并预下载模型")
    whisper_sub.add_parser("check", help="检测 faster-whisper / CUDA 状态")

    args = parser.parse_args(argv)
    config_path = Path(args.config)

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
        run_transcribe_all(config)
        return 0

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
                print(
                    "警告: config.yaml 里没有配置 ai.context 或 ai.context_file，"
                    "refine 效果有限（AI 不知道你的行程背景和规范）。",
                    file=sys.stderr,
                )
            fix = (getattr(args, "fix", "") or "").strip() or None
            target_path = getattr(args, "input", None)
            if fix and (target_path is None or target_path.is_dir()):
                print("错误: --fix 必须配合 -i 指定单个 .json 文件", file=sys.stderr)
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
                print(f"错误: {e}", file=sys.stderr)
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
                print(f"错误: 规划文件不存在: {plan_path}", file=sys.stderr)
                return 1
            plan = Plan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
            known, offline = collect_project_indices(config)
            ready = check_plan_export_readiness(plan, known_indices=known, offline_indices=offline, source=args.source)
            blocked = readiness_block_payload(ready, force=bool(getattr(args, "force", False)))
            if blocked is not None:
                print(f"错误: {blocked['error']}", file=sys.stderr)
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
            print(f"已更新 {updated} 个 project.yaml")
            if errors:
                print("错误:")
                for err in errors:
                    print(f"  - {err}")
            return 0
        elif args.command == "migrate":
            from clio.tasks.migrate import run_migrate

            updated, errors = run_migrate(config_path, getattr(args, "from_path", None))
            print(f"已迁移 {updated} 个项目")
            for err in errors:
                print(f"  错误: {err}")
            return 0 if not errors or updated else 1
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
                print(f"错误: 规划文件不存在: {plan_path}", file=sys.stderr)
                return 1
            plan = Plan.from_dict(json.loads(plan_path.read_text(encoding="utf-8")))
            known, offline = collect_project_indices(config)
            ready = check_plan_export_readiness(plan, known_indices=known, offline_indices=offline, source="original")
            blocked = readiness_block_payload(ready, force=bool(getattr(args, "force", False)))
            if blocked is not None:
                print(f"错误: {blocked['error']}", file=sys.stderr)
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
                ffprobe=config.paths.ffprobe,
                texts_dir=config.texts_dir,
                canvas_ratio=config.export.canvas_ratio,
                index_width=config.naming.index_width,
            )
            return 0
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1
    finally:
        before_stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

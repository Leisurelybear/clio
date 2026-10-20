from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePath, PureWindowsPath
from typing import Literal

from clio.asr.readiness import check_asr_ready
from clio.config import AppConfig, load_config
from clio.utils import discover_ffmpeg_bin, no_console_kwargs

DoctorStatus = Literal["OK", "WARN", "FAIL"]
PLACEHOLDER_KEYS = {"your_api_key_here", "YOUR_API_KEY", ""}


@dataclass(frozen=True)
class DoctorItem:
    name: str
    status: DoctorStatus
    detail: str = ""


def doctor_exit_code(items: list[DoctorItem]) -> int:
    return 1 if any(item.status == "FAIL" for item in items) else 0


def parse_node_major(version_text: str) -> int | None:
    text = version_text.strip()
    if text.startswith("v"):
        text = text[1:]
    first = text.split(".", 1)[0]
    return int(first) if first.isdigit() else None


def is_virtualenv_python(
    executable: str | Path | None = None,
    *,
    prefix: str | Path | None = None,
    base_prefix: str | Path | None = None,
) -> bool:
    prefix = sys.prefix if prefix is None else str(prefix)
    base_prefix = sys.base_prefix if base_prefix is None else str(base_prefix)
    if prefix != base_prefix:
        return True

    raw_executable = str(executable or sys.executable)
    fs_exe = Path(raw_executable)
    if any((parent / "pyvenv.cfg").is_file() for parent in (fs_exe.parent, fs_exe.parent.parent)):
        return True

    exe: PurePath
    if "\\" in raw_executable or (len(raw_executable) > 1 and raw_executable[1] == ":"):
        exe = PureWindowsPath(raw_executable)
    else:
        exe = Path(raw_executable)

    bin_dir = exe.parent.name.lower()
    env_dir = exe.parent.parent.name.lower()
    return bin_dir in {"bin", "scripts"} and env_dir in {".venv", "venv"}


def _node_version() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        result = subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=False, timeout=5, **no_console_kwargs()
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (result.stdout or result.stderr).strip() or None


def _binary_check(name: str, configured: str, discover_binary: Callable[[str], str | None]) -> DoctorItem:
    found = configured or discover_binary(name)
    if found:
        return DoctorItem(name, "OK", str(found))
    return DoctorItem(name, "FAIL", "未找到，请运行 setup 脚本或在 config.yaml 中配置路径")


def _path_writable(path: Path) -> bool:
    target = path if path.exists() else path.parent
    return target.exists() and os.access(target, os.W_OK)


def _provider_has_key(provider, environ: Mapping[str, str]) -> bool:
    if provider.api_key not in PLACEHOLDER_KEYS:
        return True
    return bool(provider.api_key_env and environ.get(provider.api_key_env))


def _provider_key_detail(provider) -> str:
    if provider.api_key_env:
        return f"缺少环境变量 {provider.api_key_env}（在 .env 中设置，不要写入 yaml api_key）"
    return "缺少 api_key_env（请配置环境变量名，密钥放在 .env）"


def provider_key_missing(provider, environ: Mapping[str, str] | None = None) -> str | None:
    """Return a Chinese detail string when the provider's API key is missing, else None."""
    environ = environ if environ is not None else os.environ
    if _provider_has_key(provider, environ):
        return None
    return _provider_key_detail(provider)


def collect_doctor_checks(
    config: AppConfig,
    *,
    discover_binary: Callable[[str], str | None] = discover_ffmpeg_bin,
    environ: Mapping[str, str] | None = None,
    node_version_getter: Callable[[], str | None] = _node_version,
) -> list[DoctorItem]:
    environ = environ if environ is not None else os.environ
    items: list[DoctorItem] = []

    py_ok = sys.version_info >= (3, 11)
    items.append(
        DoctorItem(
            "Python",
            "OK" if py_ok else "FAIL",
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} (需要 3.11+)",
        )
    )

    venv_ok = is_virtualenv_python()
    items.append(
        DoctorItem(
            "虚拟环境",
            "OK" if venv_ok else "WARN",
            sys.executable if venv_ok else f"{sys.executable} (建议使用 .venv)",
        )
    )

    items.append(_binary_check("ffmpeg", config.paths.ffmpeg, discover_binary))
    items.append(_binary_check("ffprobe", config.paths.ffprobe, discover_binary))

    if config.whisper.enabled:
        readiness = check_asr_ready(config, environ=environ)
        items.append(
            DoctorItem(
                f"ASR: {readiness.display_name}",
                "OK" if readiness.ready else "FAIL",
                readiness.error or readiness.engine,
            )
        )
    else:
        items.append(DoctorItem("ASR", "OK", "whisper.enabled=false，已禁用"))

    from clio.tasks._video_loader import source_videos

    project_dir = config.project_dir
    videos = source_videos(config)
    if project_dir and project_dir.is_dir():
        existing = sum(1 for v in videos if v.is_file())
        offline = len(videos) - existing
        if not videos:
            status: DoctorStatus = "WARN"
            detail = f"{project_dir} (0 个视频 — 请在 UI 添加或 migrate)"
        elif offline:
            status = "WARN"
            detail = f"{project_dir} ({len(videos)} 个视频, {offline} 个离线)"
        else:
            status = "OK"
            detail = f"{project_dir} ({len(videos)} 个视频)"
        items.append(DoctorItem("项目视频", status, detail))
    else:
        items.append(
            DoctorItem(
                "项目视频",
                "WARN",
                "未指定项目，使用 -p/--project 选择项目",
            )
        )

    output_dir = config.paths.output_dir
    items.append(
        DoctorItem(
            "输出目录可写",
            "OK" if _path_writable(output_dir) else "FAIL",
            str(output_dir),
        )
    )

    referenced_providers = {task.provider for task in config.ai.tasks.values()}
    for provider_name in sorted(referenced_providers):
        provider = config.ai.providers.get(provider_name)
        if provider is None:
            items.append(DoctorItem(f"AI provider: {provider_name}", "FAIL", "任务引用了未配置的 provider"))
            continue
        if _provider_has_key(provider, environ):
            detail = provider.api_key_env or "api_key"
            items.append(DoctorItem(f"AI provider: {provider_name}", "OK", detail))
        else:
            items.append(DoctorItem(f"AI provider: {provider_name}", "FAIL", _provider_key_detail(provider)))

    node_text = node_version_getter()
    if not node_text:
        items.append(DoctorItem("Node.js", "WARN", "未找到；仅运行 UI 单元测试需要 Node.js 18+"))
    else:
        major = parse_node_major(node_text)
        ok = major is not None and major >= 18
        status: DoctorStatus = "OK" if ok else "WARN"
        items.append(DoctorItem("Node.js", status, f"{node_text} (UI 单元测试需要 18+)"))

    return items


def run_doctor(config_path: Path, project_dir: Path | None = None) -> int:
    items: list[DoctorItem] = []
    try:
        config = load_config(config_path)
        if project_dir is not None:
            config = load_config(config_path, project_dir=project_dir)
        items.append(DoctorItem("配置文件", "OK", str(config_path)))
    except Exception as exc:
        items.append(DoctorItem("配置文件", "FAIL", str(exc)))
        _print_doctor_items(items)
        return doctor_exit_code(items)

    items.extend(collect_doctor_checks(config))
    _print_doctor_items(items)
    return doctor_exit_code(items)


def _print_doctor_items(items: list[DoctorItem]) -> None:
    print("环境诊断:")
    for item in items:
        suffix = f" - {item.detail}" if item.detail else ""
        print(f"  [{item.status}] {item.name}{suffix}")
    print(f"\ndone (exit code: {doctor_exit_code(items)})")

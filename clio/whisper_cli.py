from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from clio.config import load_config
from clio.transcribe import (
    PROJECT_ROOT,
    _clear_model_cache,
    _get_model,
    _reload_whisper_import,
    _resolve_cache_dir,
    check_cublas,
    pip_mirror_for_config,
)
from clio.utils import run_subprocess
from clio.whisper_cache import is_model_cache_complete, largest_model_file_size

_snapshot_download: Any
try:
    from huggingface_hub import snapshot_download as _snapshot_download
except ImportError:
    _snapshot_download = None


def run_whisper_install(config_path: str | Path = "config.yaml") -> int:
    print("正在安装 faster-whisper...")

    if getattr(sys, "frozen", False):
        print(
            "  [错误] 打包版（clio.exe）不内置 Whisper 依赖，无法在此安装。\n"
            "         请使用源码版：进入项目目录运行 `python main.py whisper install`，\n"
            "         或将 faster-whisper 安装到外部 Python 后手动下载模型缓存。"
        )
        return 1

    cfg = load_config(config_path)
    import os

    if cfg.whisper.hf_endpoint:
        os.environ["HF_ENDPOINT"] = cfg.whisper.hf_endpoint
        print(f"HF_ENDPOINT 已设置为: {cfg.whisper.hf_endpoint}")
    else:
        print("HF_ENDPOINT: 使用 HuggingFace 官方默认地址")
        if cfg.proxy.enabled and isinstance(cfg.proxy.url, str) and cfg.proxy.url.strip():
            os.environ["HTTP_PROXY"] = cfg.proxy.url
            os.environ["HTTPS_PROXY"] = cfg.proxy.url

    req = PROJECT_ROOT / "requirements-whisper.txt"
    if not req.is_file():
        print(f"未找到依赖文件: {req}")
        return 1
    pip_mirror = pip_mirror_for_config(cfg)
    if pip_mirror:
        print(f"检测到国内 HF 镜像，pip 使用国内源: {pip_mirror}")
    install_base = [sys.executable, "-m", "pip", "install"]
    if pip_mirror:
        install_base += ["-i", pip_mirror]
    result = run_subprocess(
        [*install_base, "-r", str(req)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("安装失败:", result.stderr)
        return 1
    print("faster-whisper 安装完成")
    # Reload the module binding so this same process can import the new install.
    _reload_whisper_import()

    import platform

    is_windows = platform.system() == "Windows"
    try:
        from ctranslate2 import get_cuda_device_count

        cuda_avail = get_cuda_device_count() > 0
    except (ImportError, OSError):
        cuda_avail = False

    # cuBLAS DLL (nvidia-cublas-cu12) is required by CTranslate2
    # even for CPU-only inference on Windows. Install unconditionally.
    cublas_pkgs = ["nvidia-cublas-cu12"]
    if cuda_avail:
        print("检测到 NVIDIA GPU，安装 CUDA 运行时加速...")
        cublas_pkgs.append("nvidia-cudnn-cu12")
    elif is_windows:
        print("Windows 上 CTranslate2 需要 cuBLAS DLL（即使 CPU 模式），正在安装...")
    else:
        print("CUDA: 不可用（使用 CPU）")
    if cublas_pkgs:
        import shutil

        cuda_size_mb = 2800
        tmp_free = shutil.disk_usage(tempfile.gettempdir()).free // (1024 * 1024)
        if tmp_free < cuda_size_mb:
            print(f"  [跳过] 磁盘空间不足（临时目录剩余 {tmp_free} MB，需要 ~{cuda_size_mb} MB）")
            if cuda_avail:
                print("  [提示] 如需 CUDA 加速，请手动执行: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
        else:
            r = run_subprocess(
                [*install_base, *cublas_pkgs, "-q"],
            )
            if r.returncode == 0:
                print(f"  {'/'.join(cublas_pkgs)} 安装完成")
            else:
                print(f"  [警告] {'/'.join(cublas_pkgs)} 安装失败（返回码 {r.returncode}）")

    model_name = cfg.whisper.model_size
    cache_dir = _resolve_cache_dir(cfg)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if _model_in_cache(cache_dir, model_name):
        print(f"模型 '{model_name}' 已在缓存中，跳过下载")
        return _verify_install(cfg)

    repo_id = f"Systran/faster-whisper-{model_name}"
    print(f"正在预下载模型 '{model_name}' 到 {cache_dir}...")
    print(f"  模型仓库: {repo_id}")
    print("  模型大小约 1~2 GB，下载时间取决于网络速度")
    if _snapshot_download is None:
        print("  [错误] huggingface_hub 未安装，无法下载模型")
        return 1

    try:
        _snapshot_download(
            repo_id=repo_id,
            cache_dir=str(cache_dir),
            resume_download=True,
            local_dir_use_symlinks=False,
            ignore_patterns=["*.h5", "*.ot", "*.pt"],
        )
    except Exception as e:
        print(f"  [错误] 下载失败: {e}")
        print("  [提示] 检查 hf_endpoint 配置或网络连接")
        return 1

    print(f"模型 '{model_name}' 已就绪")
    return _verify_install(cfg)


def _verify_install(cfg: Any) -> int:
    """Smoke-test the install: confirm cuBLAS is loadable and the model loads."""
    print("验证安装（加载模型以确认依赖完整）...")
    if not check_cublas():
        print("  [错误] cuBLAS 仍未就绪，转录将失败。请手动执行:")
        print("         pip install nvidia-cublas-cu12")
        return 1
    try:
        _get_model(cfg)
    except Exception as e:
        print(f"  [错误] 模型加载验证失败: {e}")
        print("  [提示] faster-whisper 或 cuBLAS 可能未正确安装，请重试 python main.py whisper install")
        return 1
    finally:
        _clear_model_cache()
    print("验证通过 ✔ 转录功能已就绪")
    return 0


def _model_in_cache(cache_dir: Path, model_name: str) -> bool:
    """Check if a model is completely cached and valid."""
    complete = is_model_cache_complete(cache_dir, model_name)
    if complete:
        return True
    model_file_size = _find_model_file_size(cache_dir)
    if model_file_size:
        print(f"  缓存不完整（最大模型文件 {model_file_size // 1024 // 1024} MB），重新下载")
    return False


def _find_model_file_size(dir_path: Path) -> int:
    """Find the largest file in a directory (likely the model binary)."""
    return largest_model_file_size(dir_path)


def run_whisper_check(config_path: str | Path = "config.yaml") -> int:
    print("=== Whisper 环境检测 ===")
    try:
        import faster_whisper

        print(f"faster-whisper: {faster_whisper.__version__}  ✔")
    except ImportError:
        print("faster-whisper: 未安装  ✘（请执行 python main.py whisper install）")
        return 1

    try:
        from ctranslate2 import get_cuda_device_count

        cuda_avail = get_cuda_device_count() > 0
    except (ImportError, OSError):
        cuda_avail = False
    print(f"CUDA: {'可用 ✔' if cuda_avail else '不可用（使用 CPU）'}")

    cfg = load_config(config_path)
    cache_dir = _resolve_cache_dir(cfg)
    if cache_dir.is_dir():
        models = [d.name for d in cache_dir.iterdir() if d.is_dir()]
        if models:
            print(f"已缓存模型: {', '.join(models)}")
        else:
            print("模型缓存: 空（尚无缓存模型）")
    ep = cfg.whisper.hf_endpoint
    print(f"HF_ENDPOINT: {ep or 'HuggingFace 官方默认地址'}")
    return 0

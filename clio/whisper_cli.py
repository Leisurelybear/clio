from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

from clio.config import load_config
from clio.i18n import t
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
    print(t("whisper.installing"))

    if getattr(sys, "frozen", False):
        print(t("whisper.frozen_error"))
        return 1

    cfg = load_config(config_path)
    import os

    if cfg.whisper.hf_endpoint:
        os.environ["HF_ENDPOINT"] = cfg.whisper.hf_endpoint
        print(t("whisper.hf_endpoint_set", {"endpoint": cfg.whisper.hf_endpoint}))
    else:
        print(t("whisper.hf_endpoint_default"))
        if cfg.proxy.enabled and isinstance(cfg.proxy.url, str) and cfg.proxy.url.strip():
            os.environ["HTTP_PROXY"] = cfg.proxy.url
            os.environ["HTTPS_PROXY"] = cfg.proxy.url

    req = PROJECT_ROOT / "requirements-whisper.txt"
    if not req.is_file():
        print(t("whisper.requirements_missing", {"path": req}))
        return 1
    pip_mirror = pip_mirror_for_config(cfg)
    if pip_mirror:
        print(t("whisper.pip_mirror", {"mirror": pip_mirror}))
    install_base = [sys.executable, "-m", "pip", "install"]
    if pip_mirror:
        install_base += ["-i", pip_mirror]
    result = run_subprocess(
        [*install_base, "-r", str(req)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(t("whisper.install_failed"), result.stderr)
        return 1
    print(t("whisper.install_done"))
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
        print(t("whisper.cuda_detected"))
        cublas_pkgs.append("nvidia-cudnn-cu12")
    elif is_windows:
        print(t("whisper.cublas_windows"))
    else:
        print(t("whisper.cuda_unavailable"))
    if cublas_pkgs:
        import shutil

        cuda_size_mb = 2800
        tmp_free = shutil.disk_usage(tempfile.gettempdir()).free // (1024 * 1024)
        if tmp_free < cuda_size_mb:
            print(t("whisper.skip_low_disk", {"free": tmp_free, "need": cuda_size_mb}))
            if cuda_avail:
                print(t("whisper.hint_manual_cuda"))
        else:
            r = run_subprocess(
                [*install_base, *cublas_pkgs, "-q"],
            )
            if r.returncode == 0:
                print(t("whisper.cublas_installed", {"packages": "/".join(cublas_pkgs)}))
            else:
                print(t("whisper.cublas_install_failed", {"packages": "/".join(cublas_pkgs), "code": r.returncode}))

    model_name = cfg.whisper.model_size
    cache_dir = _resolve_cache_dir(cfg)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if _model_in_cache(cache_dir, model_name):
        print(t("whisper.model_cached", {"model": model_name}))
        return _verify_install(cfg)

    repo_id = f"Systran/faster-whisper-{model_name}"
    print(t("whisper.predownloading", {"model": model_name, "cache_dir": cache_dir}))
    print(t("whisper.repo", {"repo": repo_id}))
    print(t("whisper.model_size_hint"))
    if _snapshot_download is None:
        print(t("whisper.hf_hub_missing"))
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
        print(t("whisper.download_failed", {"error": e}))
        print(t("whisper.hint_check_network"))
        return 1

    print(t("whisper.model_ready", {"model": model_name}))
    return _verify_install(cfg)


def _verify_install(cfg: Any) -> int:
    """Smoke-test the install: confirm cuBLAS is loadable and the model loads."""
    print(t("whisper.verify_install"))
    if not check_cublas():
        print(t("whisper.cublas_not_ready"))
        print("         pip install nvidia-cublas-cu12")
        return 1
    try:
        _get_model(cfg)
    except Exception as e:
        print(t("whisper.model_verify_failed", {"error": e}))
        print(t("whisper.hint_retry_install"))
        return 1
    finally:
        _clear_model_cache()
    print(t("whisper.verify_ok"))
    return 0


def _model_in_cache(cache_dir: Path, model_name: str) -> bool:
    """Check if a model is completely cached and valid."""
    complete = is_model_cache_complete(cache_dir, model_name)
    if complete:
        return True
    model_file_size = _find_model_file_size(cache_dir)
    if model_file_size:
        print(t("whisper.cache_incomplete", {"mb": model_file_size // 1024 // 1024}))
    return False


def _find_model_file_size(dir_path: Path) -> int:
    """Find the largest file in a directory (likely the model binary)."""
    return largest_model_file_size(dir_path)


def run_whisper_check(config_path: str | Path = "config.yaml") -> int:
    print(t("whisper.check_title"))
    try:
        import faster_whisper

        print(t("whisper.version_ok", {"version": faster_whisper.__version__}))
    except ImportError:
        print(t("whisper.not_installed"))
        return 1

    try:
        from ctranslate2 import get_cuda_device_count

        cuda_avail = get_cuda_device_count() > 0
    except (ImportError, OSError):
        cuda_avail = False
    print(t("whisper.cuda_available") if cuda_avail else t("whisper.cuda_unavailable"))

    cfg = load_config(config_path)
    cache_dir = _resolve_cache_dir(cfg)
    if cache_dir.is_dir():
        models = [d.name for d in cache_dir.iterdir() if d.is_dir()]
        if models:
            print(t("whisper.cached_models", {"models": ", ".join(models)}))
        else:
            print(t("whisper.no_cached_models"))
    ep = cfg.whisper.hf_endpoint
    print(f"HF_ENDPOINT: {ep or t('whisper.hf_endpoint_official')}")
    return 0

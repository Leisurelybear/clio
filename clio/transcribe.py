from __future__ import annotations

import ctypes
import os
import platform
import threading
from collections.abc import Callable
from pathlib import Path

from clio.config import AppConfig


def check_whisper() -> bool:
    try:
        import faster_whisper  # noqa: F401

    except ImportError:
        return False
    if not check_cublas():
        return False
    return True


def check_cublas() -> bool:
    """Check whether the cuBLAS runtime can be loaded.

    CTranslate2 (faster-whisper's engine) requires cublas64_12.dll on Windows
    even for CPU-only inference, so its absence means transcription will fail
    with "Library cublas64_12.dll is not found".
    """
    if platform.system() != "Windows":
        return True
    for dll in ("cublas64_12.dll", "cublas64_11.dll"):
        try:
            ctypes.CDLL(dll)
            return True
        except OSError:
            continue
    # Fallback: locate DLL in nvidia site-packages (installed via pip)
    for dll in ("cublas64_12.dll", "cublas64_11.dll"):
        try:
            import importlib.util

            _spec = importlib.util.find_spec("nvidia.cublas")
            if _spec and _spec.submodule_search_locations:
                for _loc in _spec.submodule_search_locations:
                    for subdir in ("lib", "bin", ""):
                        dll_path = Path(_loc) / subdir / dll
                        if dll_path.is_file():
                            os.add_dll_directory(str(dll_path.parent))
                            ctypes.CDLL(dll)
                            return True
        except (ImportError, AttributeError, OSError):
            pass
        # Broader fallback: glob search in site-packages
        try:
            import site as _site

            for _sp in _site.getsitepackages():
                for _nvidia_dir in Path(_sp).glob("nvidia/*/"):
                    for subdir in ("lib", "bin", ""):
                        dll_path = _nvidia_dir / subdir / dll
                        if dll_path.is_file():
                            os.add_dll_directory(str(dll_path.parent))
                            ctypes.CDLL(dll)
                            return True
        except Exception:
            pass
    return False


_CHINA_HF_HINTS = ("hf-mirror.com", "modelscope", "csdn.net", "tuweizhong", "bilibili", "aliyun")


def pip_mirror_for_config(cfg: AppConfig) -> str | None:
    """Pick a China pip mirror URL when the HF endpoint suggests a China mirror.

    nvidia-cublas-cu12 is a large PyPI package; users already on a China HF
    mirror benefit from a China pip mirror so the install actually completes.
    """
    ep = (getattr(cfg.whisper, "hf_endpoint", "") or "").lower()
    if any(hint in ep for hint in _CHINA_HF_HINTS):
        return "https://pypi.tuna.tsinghua.edu.cn/simple"
    return None


PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None  # type: ignore[assignment]

_whisper_model = None
_whisper_cache_key: str | None = None
_env_lock = threading.Lock()
_model_lock = threading.RLock()


def _reload_whisper_import() -> bool:
    """Re-import faster_whisper after an in-process install.

    The module-level import binding stays None until faster-whisper is actually
    available; a same-process install needs a fresh import to pick it up.
    """
    global WhisperModel
    import importlib

    try:
        faster_whisper = importlib.import_module("faster_whisper")
        importlib.reload(faster_whisper)
        WhisperModel = faster_whisper.WhisperModel
        return True
    except ImportError:
        WhisperModel = None
        return False


def _clear_model_cache() -> None:
    global _whisper_model, _whisper_cache_key
    with _model_lock:
        _whisper_model = None
        _whisper_cache_key = None


def model_usage_lock() -> threading.Lock:
    """Lock that serializes model load/clear against concurrent cache deletion."""
    return _model_lock


def is_model_loaded(model_name: str) -> bool:
    """True when ``model_name`` is the currently cached in-memory model."""
    with _model_lock:
        if _whisper_cache_key is None:
            return False
        return _whisper_cache_key.split("@", 1)[0] == model_name


def _resolve_cache_dir(config: AppConfig) -> Path:
    if config.whisper.cache_dir:
        return Path(config.whisper.cache_dir).resolve()
    return PROJECT_ROOT / "models"


def _resolve_device(config: AppConfig) -> str:
    if config.whisper.device == "auto":
        try:
            from ctranslate2 import get_cuda_device_count

            return "cuda" if get_cuda_device_count() > 0 else "cpu"
        except (ImportError, OSError, RuntimeError):
            return "cpu"
    return config.whisper.device


def _resolve_compute_types(device: str) -> list[str]:
    if device == "cuda":
        return ["int8_float16", "float16", "default"]
    return ["int8", "default"]


def _get_model(config: AppConfig):
    global _whisper_model, _whisper_cache_key
    if WhisperModel is None:
        _reload_whisper_import()
    if WhisperModel is None:
        raise ImportError("faster-whisper is not installed. Run: pip install faster-whisper")

    _ENV_KEYS = {"HF_ENDPOINT", "HTTP_PROXY", "HTTPS_PROXY", "OMP_NUM_THREADS", "MKL_NUM_THREADS"}
    with _env_lock:
        saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        try:
            os.environ.setdefault("OMP_NUM_THREADS", "4")
            os.environ.setdefault("MKL_NUM_THREADS", "4")
            if config.whisper.hf_endpoint:
                os.environ["HF_ENDPOINT"] = config.whisper.hf_endpoint
            else:
                if config.proxy.enabled and isinstance(config.proxy.url, str) and config.proxy.url.strip():
                    os.environ["HTTP_PROXY"] = config.proxy.url
                    os.environ["HTTPS_PROXY"] = config.proxy.url

            cache_dir = _resolve_cache_dir(config)
            device = _resolve_device(config)
            attempt = 0
            while True:
                with _model_lock:
                    compute_types = _resolve_compute_types(device)
                    for ct in compute_types:
                        attempt += 1
                        key = f"{config.whisper.model_size}@{device}@{ct}@{cache_dir}"
                        if _whisper_model is not None and _whisper_cache_key == key:
                            return _whisper_model
                        try:
                            _whisper_model = WhisperModel(
                                config.whisper.model_size,
                                device=device,
                                compute_type=ct,
                                download_root=str(cache_dir),
                            )
                            _whisper_cache_key = key
                            return _whisper_model
                        except (ValueError, RuntimeError, OSError) as e:
                            is_last = ct == compute_types[-1]
                            err_str = str(e)
                            if device == "cuda" and is_last:
                                print(f"  [警告] CUDA 加载失败 ({err_str})，回退到 CPU")
                                device = "cpu"
                                break
                            if device != "cuda" and is_last:
                                print(f"  [错误] 模型加载失败: {err_str}")
                                print("  [提示] 请执行 `python main.py whisper install` 预下载模型到本地缓存")
                                ep = config.whisper.hf_endpoint or "未设置（使用官方地址）"
                                print(f"  [提示] 国内用户需在设置中配置 hf_endpoint（当前: {ep}）")
                                if (
                                    "tls" in err_str.lower()
                                    or "handshake" in err_str.lower()
                                    or "eof" in err_str.lower()
                                    or "connect" in err_str.lower()
                                ):
                                    print("  [提示] 可能是网络/代理问题导致模型下载失败，建议：")
                                    print("         1. 在配置中设置 hf_endpoint: https://hf-mirror.com")
                                    print("         2. 或执行 `python main.py whisper install` 手动下载")
                                if "cublas" in err_str.lower() or "library" in err_str.lower():
                                    print("  [提示] 可能是 CUDA 库缺失，建议：")
                                    print("         1. 在配置中设置 whisper.device: cpu（跳过 CUDA）")
                                    print(
                                        "         2. 或安装 CUDA 运行时: pip install nvidia-cublas-cu12"
                                        " nvidia-cudnn-cu12"
                                    )
                                raise
                            print(f"  [警告] {device} {ct} 加载失败 ({e})，尝试下一个 compute type")
                            continue
                        except Exception as e:
                            if device == "cuda":
                                print(f"  [警告] CUDA 加载异常 ({e})，回退到 CPU")
                                device = "cpu"
                                break
                            raise
            return _whisper_model
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def transcribe_audio(
    audio_path: Path,
    config: AppConfig,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict]:
    from clio.asr.factory import build_provider

    engine_id = getattr(config.whisper, "engine", "local")
    provider = build_provider(engine_id, config)
    lang = config.whisper.language
    supported = getattr(provider.capabilities, "supported_languages", ["*"])
    if lang not in supported and "*" not in supported:
        raise RuntimeError(f"ASR 引擎 {engine_id} 不支持语言 {lang}，当前支持: {', '.join(supported)}")
    if progress_callback:
        progress_callback(0)
    segments = provider.transcribe(audio_path, lang, progress_callback, cancel_event)
    return [seg.to_dict() for seg in segments]


def _transcribe_local_whisper(
    config: AppConfig,
    audio_path: Path,
    progress_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> list[dict]:
    lang = config.whisper.language

    def _transcribe_once(device_override: str | None = None) -> list[dict]:
        model = _get_model(config)
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=None if lang == "auto" else lang,
            word_timestamps=False,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300),
            beam_size=5,
            best_of=5,
            temperature=0.0,
        )
        # faster-whisper returns a lazy stream: real engine errors (e.g. broken
        # cuBLAS) surface here while iterating, not during the setup call. Keep
        # the whole iteration inside the fallback boundary.
        total_duration = info.duration
        last_pct = 0
        result = []
        for seg in segments_iter:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("本地 ASR 转录已取消")
            pct = int(seg.end / total_duration * 100) if total_duration > 0 else 0
            if pct >= last_pct + 5:
                print(f"  [whisper] 转录进度: {seg.end:.1f}s / {total_duration:.0f}s ({pct}%)")
                if progress_callback:
                    progress_callback(pct)
                last_pct = pct
            is_low = seg.avg_logprob < -0.8 or seg.no_speech_prob > 0.1
            entry = {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
                "avg_logprob": round(seg.avg_logprob, 3),
            }
            if is_low:
                entry["low_confidence"] = True
            result.append(entry)
        if progress_callback:
            progress_callback(100)
        return result

    try:
        return _transcribe_once()
    except (RuntimeError, OSError) as e:
        err = str(e)
        if not ("cublas" in err.lower() or "cuda" in err.lower() or "library" in err.lower()):
            raise
        print(f"  [警告] CUDA 模型加载/推理失败 ({e})，回退到 CPU 重试")
        _clear_model_cache()
        _orig_device = config.whisper.device
        try:
            config.whisper.device = "cpu"
        except AttributeError:
            if config.whisper._project is not None:
                config.whisper._project.device = "cpu"
        try:
            return _transcribe_once()
        except (RuntimeError, OSError):
            print("  [错误] CPU 回退也失败，可能是 cuBLAS 库缺失")
            print("  [提示] 请在配置中设置 whisper.device: cpu，或安装 CUDA 运行时:")
            print("         pip install nvidia-cublas-cu12 nvidia-cudnn-cu12")
            raise
        finally:
            try:
                config.whisper.device = _orig_device
            except AttributeError:
                if config.whisper._project is not None:
                    config.whisper._project.device = _orig_device

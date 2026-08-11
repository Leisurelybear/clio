from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from clio._constants import VIDEO_EXTENSIONS
from clio.shutdown import register_process, unregister_process

T = TypeVar("T")
JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
MAX_EXTRACT_JSON_SCAN_CHARS = 2_000_000
_PROBE_INFO_CACHE_MAX = 512
_PROBE_INFO_CACHE_LOCK = threading.Lock()
_PROBE_INFO_CACHE: dict[tuple[str, str, int, int], dict[str, float]] = {}

# ---- subprocess wrappers for cross-platform encoding safety ----
# On Chinese Windows text=True defaults to GBK, which crashes on UTF-8
# filenames in ffmpeg stderr.  These wrappers force utf-8 + errors=replace.


def no_console_kwargs() -> dict:
    """Windows kwargs to avoid a visible console window per subprocess.

    GUI hosts (desktop UI) have no console; without CREATE_NO_WINDOW every
    ffmpeg/ffprobe/pip child flashes a black console window. No-op elsewhere.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def run_subprocess(
    cmd: list[str],
    **kwargs,
) -> subprocess.CompletedProcess:
    """subprocess.run() wrapper — forces UTF-8 encoding in text mode."""
    in_text = kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding")
    if in_text:
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    if not kwargs.get("creationflags"):
        kwargs.update(no_console_kwargs())
    return subprocess.run(cmd, **kwargs)


def popen_subprocess(
    cmd: list[str],
    **kwargs,
) -> subprocess.Popen:
    """subprocess.Popen() wrapper — forces UTF-8 encoding in text mode."""
    if kwargs.get("text") or kwargs.get("universal_newlines"):
        kwargs.setdefault("encoding", "utf-8")
        kwargs.setdefault("errors", "replace")
    if not kwargs.get("creationflags"):
        kwargs.update(no_console_kwargs())
    return subprocess.Popen(cmd, **kwargs)


def with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (),
    what: str = "operation",
    should_retry: Callable[[BaseException], bool] | None = None,
) -> T:
    """调用 fn()，按指数退避（1s/2s/4s）重试最多 attempts 次。

    只对 retry_on 列出的异常类型重试；其它异常（如 ValueError）立即抛出。
    如果提供了 should_retry 函数，在异常类型匹配后额外调用该函数判断是否应重试。
    每次重试会打印一行（控制台 + 日志都看得到），最后一次失败时也打印。

    用法::

        return with_retry(
            lambda: self._client.post(...).json()["choices"][0]["message"]["content"],
            attempts=3, retry_on=(httpx.HTTPError,),
            what="OpenAI 兼容 API",
        )
    """
    last_exc: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except BaseException as e:
            if not isinstance(e, retry_on):
                raise
            if should_retry is not None and not should_retry(e):
                raise
            last_exc = e
            if i + 1 >= attempts:
                print(f"  [重试] {what} 失败 {attempts} 次，放弃: {type(e).__name__}: {e}")
                break
            delay = base_delay * (2**i)
            print(f"  [重试] {what} 失败 ({type(e).__name__}: {str(e)[:80]})，{delay:.1f}s 后重试 ({i + 2}/{attempts})")
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def _fix_trailing_commas(text: str) -> str:
    """移除 JSON 中数组/对象末尾多余的逗号，使 json.loads 能解析。"""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _repair_truncated_json(text: str) -> str | None:
    """Best-effort close of truncated JSON objects/arrays.

    Models often hit max_tokens mid-string; the partial payload still has the
    leading structure. Close any open string, drop a dangling comma, then close
    open braces/brackets so json.loads can recover partial results.
    """
    if not text or "{" not in text:
        return None
    # Start from first object brace (ignore prose prefix)
    start = text.find("{")
    s = text[start:].rstrip()
    if not s:
        return None

    stack: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
        elif ch == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif ch == "]" and stack and stack[-1] == "[":
            stack.pop()

    if not stack and not in_str:
        return None  # structurally closed already; not a truncation case

    repaired = s
    if in_str:
        repaired += '"'
    # Drop trailing comma before we close containers
    repaired = re.sub(r",\s*$", "", repaired)
    while stack:
        opener = stack.pop()
        repaired += "]" if opener == "[" else "}"
    return repaired


def _try_load_json_object(candidate: str) -> dict | None:
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def extract_json(text: str) -> dict:
    """从 AI 返回的文本中容错提取 JSON 对象。

    顺序：整段解析 → 贪婪 {...} 块 → 清尾逗号 → 截断补全（关字符串/括号）。
    都失败时抛出 ValueError，含原文前 500 字方便排查。
    """
    text = text.strip()
    loaded = _try_load_json_object(text)
    if loaded is not None:
        return loaded

    if len(text) > MAX_EXTRACT_JSON_SCAN_CHARS:
        raise ValueError(
            f"AI 返回内容过长，已跳过 JSON 正则提取：{len(text)} 字符 > {MAX_EXTRACT_JSON_SCAN_CHARS} 字符"
        )

    match = re.search(r"\{[\s\S]*\}", text)
    candidates: list[str] = []
    if match:
        candidates.append(match.group())
    # Also try from first `{` to end (truncated payloads often have no final `}`)
    first_brace = text.find("{")
    if first_brace >= 0:
        tail = text[first_brace:]
        if tail not in candidates:
            candidates.append(tail)

    last_err: Exception | None = None
    for candidate in candidates:
        loaded = _try_load_json_object(candidate)
        if loaded is not None:
            return loaded
        fixed = _fix_trailing_commas(candidate)
        loaded = _try_load_json_object(fixed)
        if loaded is not None:
            return loaded
        repaired = _repair_truncated_json(fixed)
        if repaired:
            loaded = _try_load_json_object(repaired)
            if loaded is not None:
                print("  [JSON] AI 输出被截断，已尽力补全括号/字符串后解析")
                return loaded
        try:
            json.loads(candidate)
        except json.JSONDecodeError as e:
            last_err = e

    hint = f" ({last_err})" if last_err else ""
    raise ValueError(f"AI 返回内容无法解析为 JSON{hint}:\n{text[:500]}")


def mask_if_looks_like_key(value: str) -> str:
    """如果字符串看起来像 API key 而非环境变量名，遮蔽中间部分。

    避免错误信息把误填的 key 原样回显到终端/日志。
    """
    if not value:
        return value
    if value.startswith(("sk-", "sk_", "AIza", "gho_", "ghp_", "xai-")) and len(value) > 12:
        return f"{value[:4]}***{value[-4:]}"
    if len(value) > 32 and " " not in value:
        return f"{value[:6]}***{value[-4:]}"
    return value


def discover_ffmpeg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found

    local_app = Path(os.environ.get("LOCALAPPDATA", ""))
    user_home = Path(os.environ.get("USERPROFILE", ""))
    search_roots = [
        local_app / "Microsoft/WinGet/Packages",
        Path("C:/ProgramData/chocolatey/lib/ffmpeg/tools/ffmpeg/bin"),
        user_home / "scoop/apps/ffmpeg/current/bin",
        Path("C:/Program Files/ffmpeg/bin"),
        Path("C:/Tools/ffmpeg/bin"),
    ]
    ffmpeg_home = os.environ.get("FFMPEG_HOME")
    if ffmpeg_home:
        search_roots.append(Path(ffmpeg_home))
    for root in search_roots:
        if not root.is_dir():
            continue
        matches = sorted(root.glob(f"**/{name}.exe"))
        if matches:
            return str(matches[0])
    return None


def resolve_binary(configured: str, fallback: str) -> str:
    if configured:
        path = Path(configured)
        if path.is_file():
            return str(path)
        raise FileNotFoundError(f"找不到可执行文件: {configured}")

    found = discover_ffmpeg_bin(fallback)
    if found:
        return found
    setup_script = "setup.ps1" if os.name == "nt" else "setup.sh"
    raise FileNotFoundError(f"找不到 {fallback}。请运行 {setup_script} 安装，或在 config.yaml 的 paths 中填写路径。")


def probe_ffmpeg_deps(
    ffmpeg_configured: str = "",
    ffprobe_configured: str = "",
) -> dict:
    """Report ffmpeg/ffprobe availability without raising.

    Returns:
        ok: True only if both resolve.
        ffmpeg / ffprobe: resolved path or None.
        missing: list of missing tool names.
        detail: Chinese message for UI (empty when ok).
    """
    found: dict[str, str | None] = {"ffmpeg": None, "ffprobe": None}
    missing: list[str] = []
    for name, configured in (
        ("ffmpeg", ffmpeg_configured or ""),
        ("ffprobe", ffprobe_configured or ""),
    ):
        try:
            found[name] = resolve_binary(configured, name)
        except FileNotFoundError:
            found[name] = None
            missing.append(name)

    if not missing:
        return {
            "ok": True,
            "ffmpeg": found["ffmpeg"],
            "ffprobe": found["ffprobe"],
            "missing": [],
            "detail": "",
        }

    setup = "setup.ps1" if os.name == "nt" else "setup.sh"
    labels = "、".join(missing)
    detail = (
        f"未找到 {labels}。请运行 {setup}，或在 config.yaml 的 paths.ffmpeg / paths.ffprobe 中填写路径。"
        " 压缩 / 裁剪 / 转录抽音 / 波形等功能不可用。"
    )
    return {
        "ok": False,
        "ffmpeg": found["ffmpeg"],
        "ffprobe": found["ffprobe"],
        "missing": missing,
        "detail": detail,
    }


def find_videos(directory: Path, recursive: bool = False) -> list[Path]:
    if not directory.is_dir():
        raise NotADirectoryError(f"素材目录不存在: {directory}")
    if recursive:
        files = [p for p in sorted(directory.rglob("*")) if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    else:
        files = [p for p in sorted(directory.iterdir()) if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
    return files


def _probe_cache_key(video_path: Path, ffprobe: str) -> tuple[str, str, int, int] | None:
    try:
        st = video_path.stat()
    except OSError:
        return None
    return (ffprobe, str(video_path.resolve()), st.st_size, st.st_mtime_ns)


def _get_cached_probe_info(key: tuple[str, str, int, int] | None) -> dict[str, float] | None:
    if key is None:
        return None
    with _PROBE_INFO_CACHE_LOCK:
        cached = _PROBE_INFO_CACHE.get(key)
        return dict(cached) if cached is not None else None


def _set_cached_probe_info(key: tuple[str, str, int, int] | None, info: dict[str, float]) -> None:
    if key is None:
        return
    with _PROBE_INFO_CACHE_LOCK:
        if len(_PROBE_INFO_CACHE) >= _PROBE_INFO_CACHE_MAX and key not in _PROBE_INFO_CACHE:
            _PROBE_INFO_CACHE.pop(next(iter(_PROBE_INFO_CACHE)))
        _PROBE_INFO_CACHE[key] = dict(info)


_DEFAULT_PROBE_TIMEOUT = 30.0


def run_probe(
    cmd: list[str],
    *,
    timeout: float = _DEFAULT_PROBE_TIMEOUT,
    cancel_event: threading.Event | None = None,
    max_output_bytes: int = 4096,
) -> subprocess.CompletedProcess:
    """Run ffprobe with timeout, cancel signal, and output limit."""
    proc = popen_subprocess(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    register_process(proc)
    try:
        if cancel_event and cancel_event.is_set():
            proc.terminate()
            raise InterruptedError("probe cancelled")
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise TimeoutError(f"probe timed out after {timeout}s: {' '.join(cmd)}")
        if len(stdout) > max_output_bytes:
            stdout = stdout[:max_output_bytes]
        result = subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
        if proc.returncode != 0:
            result.check_returncode()
        return result
    finally:
        unregister_process(proc)


def get_duration_sec(video_path: Path, ffprobe: str) -> float:
    key = _probe_cache_key(video_path, ffprobe)
    cached = _get_cached_probe_info(key)
    if cached is not None and "duration_sec" in cached:
        return cached["duration_sec"]
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = run_probe(cmd)
    raw = result.stdout.strip()
    if raw in ("N/A", "inf", "-inf", ""):
        raise ValueError(f"ffprobe 返回无效时长 '{raw}' for {video_path}")
    duration = float(raw)
    if key is not None:
        size_mb = key[2] / (1024 * 1024)
        _set_cached_probe_info(key, {"duration_sec": duration, "size_mb": size_mb})
    return duration


def write_json_atomic(path: Path, data: JsonValue, *, ensure_ascii: bool = False, indent: int = 2) -> None:
    """Write JSON to a file using tmp + rename for crash safety."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.urandom(4).hex()}")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=ensure_ascii, indent=indent), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_text_atomic(path: Path, text: str) -> None:
    """Write text to a file using tmp + rename for crash safety."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.urandom(4).hex()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def sanitize_name(text: str, max_len: int = 40) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:max_len] or "clip"


def safe_basename(text: str, max_len: int = 40) -> str:
    """Sanitize *text* into a single, traversal-free basename.

    Like sanitize_name but additionally rejects absolute/`..` segments so the
    result can never escape a parent directory when joined into a path.
    """
    name = sanitize_name(text, max_len=max_len)
    if not name or name in {".", ".."}:
        raise ValueError(f"非法的文件名: {text!r}")
    if ".." in name.split("_"):
        raise ValueError(f"非法的文件名: {text!r}")
    return name


def format_index(index: int, width: int) -> str:
    return str(index).zfill(width)


def validate_within_root(path: Path, root: Path) -> Path:
    """Resolve *path* and verify it is within *root* (no symlink escape).

    Returns the resolved path on success.
    Raises ValueError if the path escapes root or is a symlink.
    """
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        if not resolved.is_relative_to(root_resolved):
            raise ValueError(f"path escapes root: {path} not within {root}")
    except (ValueError, TypeError):
        raise ValueError(f"path escapes root: {path} not within {root}")
    try:
        if resolved.is_symlink() or path.is_symlink():
            raise ValueError(f"symlink not allowed: {path}")
    except (TypeError, OSError):
        pass
    return resolved


def probe_video_info(video_path: Path, ffprobe: str) -> dict:
    key = _probe_cache_key(video_path, ffprobe)
    cached = _get_cached_probe_info(key)
    if cached is not None and "duration_sec" in cached and "size_mb" in cached:
        return {"duration_sec": round(cached["duration_sec"], 2), "size_mb": round(cached["size_mb"], 2)}
    duration = get_duration_sec(video_path, ffprobe)
    size_mb = video_path.stat().st_size / (1024 * 1024)
    _set_cached_probe_info(key, {"duration_sec": duration, "size_mb": size_mb})
    return {"duration_sec": round(duration, 2), "size_mb": round(size_mb, 2)}


_MAX_STDERR_TAIL = 200


def run_ffmpeg(
    args: list[str],
    ffmpeg: str,
    progress_callback: Callable[[float], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    cmd = [ffmpeg, *args]
    process = popen_subprocess(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, bufsize=1)
    register_process(process)
    stderr_lines: list[str] = []
    time_pat = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
    cancel_flag = [False]

    def _reader() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            if "error" in line.lower() or "warning" in line.lower():
                print(line, end="")
            if len(stderr_lines) >= _MAX_STDERR_TAIL:
                stderr_lines.pop(0)
            stderr_lines.append(line)
            if progress_callback:
                m = time_pat.search(line)
                if m:
                    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    progress_callback(h * 3600 + mi * 60 + s)

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    try:
        while True:
            if cancel_event and cancel_event.is_set():
                cancel_flag[0] = True
                process.terminate()
                raise InterruptedError("ffmpeg 被用户取消")
            try:
                process.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                pass
        reader.join(timeout=5)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        raise
    finally:
        unregister_process(process)
    if not cancel_flag[0] and process.returncode != 0:
        raise RuntimeError(f"ffmpeg 执行失败:\n{' '.join(cmd)}\n{''.join(stderr_lines)}")

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import TextIO

from clio import session_log

_LOGGER_NAME = "clio"
_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _HourlyFileHandler(logging.Handler):
    """按当前小时自动切文件：logs/YYYY-MM-DD-HH.log。"""

    def __init__(self, logs_dir: Path) -> None:
        super().__init__()
        self._logs_dir = logs_dir
        self._current_hour: str | None = None
        self._current_file: TextIO | None = None
        self.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        self._rotate(datetime.now())

    def _rotate(self, ts: datetime) -> None:
        hour_key = ts.strftime("%Y-%m-%d-%H")
        if hour_key == self._current_hour:
            return
        if self._current_file is not None:
            try:
                self._current_file.close()
            except Exception:
                pass
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._logs_dir / f"{hour_key}.log"
        self._current_file = open(log_path, "a", encoding="utf-8")
        self._current_hour = hour_key

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._rotate(datetime.fromtimestamp(record.created))
            assert self._current_file is not None
            msg = self.format(record) + "\n"
            self._current_file.write(msg)
            self._current_file.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._current_file is not None:
            try:
                self._current_file.close()
            except Exception:
                pass
            self._current_file = None
        super().close()


class _TeeWriter:
    """把每一次 write 同时写到原始 stream 和 logger。"""

    def __init__(self, original: TextIO, logger: logging.Logger, level: int) -> None:
        self._original = original
        self._logger = logger
        self._level = level

    def write(self, message: str) -> int:
        if not message:
            return 0
        try:
            written = self._original.write(message)
        except Exception:
            written = 0
        if "Traceback (most recent call last):" in message:
            self._logger.log(logging.ERROR, message.rstrip())
        else:
            for line in message.splitlines():
                if line:
                    self._logger.log(self._level, line)
        # print() often splits content + trailing "\n" into two write()s;
        # blank ends must not create empty session_log rows in the UI.
        stripped = message.rstrip()
        if stripped:
            session_log.write(stripped)
        try:
            self._original.flush()
        except Exception:
            pass
        return written

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return bool(getattr(self._original, "isatty", lambda: False)())

    def __getattr__(self, name: str):
        if name in ("close", "writelines", "truncate", "detach", "__del__"):
            raise AttributeError(f"_TeeWriter does not support '{name}'")
        return getattr(self._original, name)


_initialized = False
_init_lock = threading.Lock()
_original_stdout: TextIO | None = None
_original_stderr: TextIO | None = None


def _install_excepthook(logger: logging.Logger) -> None:
    """把未捕获异常整成一条 ERROR 日志（避免 traceback 每行都污染）。"""

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        if sys.__stderr__ is not None:
            sys.__stderr__.write(tb_text)
            try:
                sys.__stderr__.flush()
            except Exception:
                pass
        logger.error(tb_text.rstrip())

    sys.excepthook = hook


def setup_logging(logs_dir: Path, level: int = logging.INFO) -> logging.Logger:
    """初始化日志：把 stdout/stderr 同时写到控制台和 logs/YYYY-MM-DD-HH.log。

    - 跨小时自动切到新文件，无需重启
    - 多次调用是幂等的
    - 文件创建失败时退化为只在控制台输出
    - 注册 sys.excepthook：未捕获异常整成一条 ERROR 日志
    """
    global _initialized, _original_stdout, _original_stderr
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)

    with _init_lock:
        if _initialized:
            return logger

        try:
            logger.addHandler(_HourlyFileHandler(logs_dir))
        except Exception as e:
            sys.stderr.write(f"[clio] 无法创建日志文件: {e}\n")

        _original_stdout = sys.stdout
        _original_stderr = sys.stderr
        sys.stdout = _TeeWriter(sys.stdout, logger, logging.INFO)
        sys.stderr = _TeeWriter(sys.stderr, logger, logging.WARNING)
        _install_excepthook(logger)
        _initialized = True

    return logger


def teardown_logging() -> None:
    """恢复 sys.stdout/stderr 到原始流，重置初始化标志。

    供 pytest 清理用（capsys 需要原始 stream）。
    """
    global _initialized, _original_stdout, _original_stderr
    with _init_lock:
        if not _initialized:
            return
        if _original_stdout is not None:
            sys.stdout = _original_stdout
            _original_stdout = None
        if _original_stderr is not None:
            sys.stderr = _original_stderr
            _original_stderr = None
        try:
            sys.excepthook = sys.__excepthook__
        except Exception:
            pass
        _initialized = False


def format_size(num_bytes: float) -> str:
    """人类可读体积：1.2 MB / 456 KB / 0 B。"""
    if num_bytes < 1024:
        return f"{int(num_bytes)} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    return f"{num_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_duration(seconds: float) -> str:
    """紧凑时长：45s / 1m23s / 1h02m03s。"""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m{s:02d}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s"


class timed:
    """上下文管理器：进入时打印起始时间戳，退出时打印耗时。

    用法::

        with timed("压缩 GL010683.mp4"):
            compress(...)

    输出::

        [压缩 GL010683.mp4] 起始 21:52:30
        [压缩 GL010683.mp4] 完成 用时 1m23s

    也可在 with 块内读 `t.elapsed` 获取当前已耗时。
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self._t0 = 0.0

    def __enter__(self) -> timed:
        self._t0 = time.monotonic()
        print(f"[{self._label}] 起始 {datetime.now().strftime('%H:%M:%S')}")
        return self

    def __exit__(self, *exc) -> None:
        elapsed = time.monotonic() - self._t0
        print(f"[{self._label}] 完成 用时 {format_duration(elapsed)}")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._t0

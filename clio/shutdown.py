from __future__ import annotations

import atexit
import os
import signal
import subprocess
import sys
import threading

_running_processes: list[subprocess.Popen] = []
_processes_lock = threading.Lock()

_hooks_installed = False
_called = False
_called_lock = threading.Lock()


def _sprint(msg: str) -> None:
    """Print that never raises UnicodeEncodeError (e.g. Windows cp1252 console)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        try:
            print(msg.encode(enc, errors="replace").decode(enc))
        except Exception:
            pass


def register_process(proc: subprocess.Popen) -> None:
    with _processes_lock:
        _running_processes.append(proc)


def unregister_process(proc: subprocess.Popen) -> None:
    with _processes_lock:
        try:
            _running_processes.remove(proc)
        except ValueError:
            pass


def before_stop() -> None:
    global _called
    with _called_lock:
        if _called:
            return
        _called = True

    _sprint("  [beforeStop] 开始清理资源...")
    had_error = False

    try:
        procs: list[subprocess.Popen] = []
        with _processes_lock:
            procs = list(_running_processes)
            _running_processes.clear()

        if procs:
            alive = [p for p in procs if p.poll() is None]
            if alive:
                _sprint(f"  [beforeStop] 终止 {len(alive)} 个运行中的 ffmpeg 子进程...")
            for proc in alive:
                pid = proc.pid
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                    _sprint(f"  [beforeStop]   ffmpeg (pid={pid}) 已终止")
                except Exception:
                    try:
                        proc.kill()
                        proc.wait()
                        _sprint(f"  [beforeStop]   ffmpeg (pid={pid}) 已强制终止")
                    except Exception:
                        _sprint(f"  [beforeStop]   ffmpeg (pid={pid}) 终止失败（可能已退出）")

        try:
            from clio.ai.factory import _clear_provider_cache

            _clear_provider_cache()
            _sprint("  [beforeStop] AI 连接池已关闭")
        except Exception:
            had_error = True
            pass

        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            sys.stderr.flush()
        except Exception:
            pass
    except Exception as e:
        had_error = True
        _sprint(f"  [beforeStop] 清理过程异常: {e}")

    _sprint("  [beforeStop] 清理完成" if not had_error else "  [beforeStop] 清理完成（有错误）")


def reset_stop_flag() -> None:
    """Reset the _called flag (for testing or after a failed shutdown)."""
    global _called
    with _called_lock:
        _called = False


def _signal_handler(signum, frame) -> None:
    before_stop()
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def install_hooks() -> None:
    global _hooks_installed
    if _hooks_installed:
        return
    _hooks_installed = True
    atexit.register(before_stop)
    try:
        signal.signal(signal.SIGTERM, _signal_handler)
    except (ValueError, OSError):
        pass

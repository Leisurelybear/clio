from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from clio.utils import (
    discover_ffmpeg_bin,
    get_duration_sec,
    popen_subprocess,
    run_ffmpeg,
    run_subprocess,
    write_json_atomic,
    write_text_atomic,
)


class TestRunSubprocess:
    def test_success(self):
        result = run_subprocess(["python", "--version"])
        assert result.returncode == 0

    def test_captures_stdout(self):
        result = run_subprocess(["python", "-c", "print('hello')"], capture_output=True, text=True)
        assert result.stdout.strip() == "hello"

    def test_errors_replace_encoding(self):
        result = run_subprocess(
            ["python", "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe')"], capture_output=True
        )
        assert result.returncode == 0

    @patch("clio.utils.subprocess.run")
    def test_passes_text_flag(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_subprocess(["echo", "hi"], capture_output=True, text=True)
        assert mock_run.call_args.kwargs.get("text") is True

    @patch("clio.utils.subprocess.run")
    def test_default_no_errors_when_not_text(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        run_subprocess(["echo"], capture_output=True)
        assert "errors" not in mock_run.call_args.kwargs


class TestPopenSubprocess:
    def test_returns_popen_object(self):
        proc = popen_subprocess(["python", "--version"])
        assert proc is not None
        proc.wait(timeout=5)

    @patch("clio.utils.subprocess.Popen")
    def test_forwards_kwargs(self, mock_popen):
        mock_popen.return_value = MagicMock()
        popen_subprocess(["ffmpeg", "-i", "input.mp4"], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        kwargs = mock_popen.call_args.kwargs
        assert kwargs.get("stdout") == subprocess.DEVNULL
        assert kwargs.get("stderr") == subprocess.PIPE

    @patch("clio.utils.subprocess.Popen")
    def test_adds_errors_in_text_mode(self, mock_popen):
        mock_popen.return_value = MagicMock()
        popen_subprocess(["ffmpeg"], text=True)
        assert mock_popen.call_args.kwargs.get("errors") == "replace"

    @patch("clio.utils.subprocess.Popen")
    def test_no_errors_without_text(self, mock_popen):
        mock_popen.return_value = MagicMock()
        popen_subprocess(["ffmpeg"])
        assert "errors" not in mock_popen.call_args.kwargs


class TestDiscoverFfmpegBin:
    @patch("clio.utils.shutil.which", return_value=None)
    @patch("clio.utils.Path.is_dir", return_value=False)
    def test_returns_none_when_not_found(self, mock_is_dir, mock_which):
        result = discover_ffmpeg_bin("ffmpeg")
        assert result is None

    @patch("clio.utils.shutil.which", return_value="/usr/bin/ffmpeg")
    def test_returns_which_result(self, mock_which):
        result = discover_ffmpeg_bin("ffmpeg")
        assert result == "/usr/bin/ffmpeg"

    @patch("clio.utils.shutil.which", return_value=None)
    @patch("clio.utils.Path.is_dir", return_value=True)
    @patch("clio.utils.Path.glob")
    def test_searches_winget_path(self, mock_glob, mock_is_dir, mock_which):
        mock_glob.return_value = [Path("C:/ffmpeg/bin/ffmpeg.exe")]
        result = discover_ffmpeg_bin("ffmpeg")
        assert result is not None
        assert "ffmpeg.exe" in result

    @patch("clio.utils.shutil.which", return_value=None)
    @patch("clio.utils.Path.is_dir", return_value=True)
    @patch("clio.utils.Path.glob", return_value=[])
    def test_ffmpeg_home_env(self, mock_glob, mock_is_dir, mock_which):
        with patch.dict(os.environ, {"FFMPEG_HOME": "C:/custom_ffmpeg"}, clear=False):
            result = discover_ffmpeg_bin("ffmpeg")
            assert result is None


class TestGetDurationSec:
    def test_returns_float(self):
        with patch("clio.utils.run_probe") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="5025.670000\n",
            )
            result = get_duration_sec(Path("/dummy.mp4"), "ffprobe")
            assert abs(result - 5025.67) < 0.01

    def test_raises_on_ffprobe_failure(self):
        with patch("clio.utils.run_probe") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "ffprobe")
            with pytest.raises(subprocess.CalledProcessError):
                get_duration_sec(Path("/dummy.mp4"), "ffprobe")

    def test_raises_on_na_duration(self):
        with patch("clio.utils.run_probe") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="N/A\n",
            )
            with pytest.raises(ValueError, match="无效时长"):
                get_duration_sec(Path("/dummy.mp4"), "ffprobe")

    def test_raises_on_inf_duration(self):
        with patch("clio.utils.run_probe") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="inf\n",
            )
            with pytest.raises(ValueError, match="无效时长"):
                get_duration_sec(Path("/dummy.mp4"), "ffprobe")


class TestWriteJsonAtomic:
    def test_writes_and_reads_back(self, tmp_path):
        data = {"key": "value"}
        f = tmp_path / "test.json"
        write_json_atomic(f, data)
        assert f.exists()
        assert json.loads(f.read_text(encoding="utf-8")) == data

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "sub" / "nested" / "test.json"
        data = {"a": 1}
        write_json_atomic(f, data)
        assert f.exists()

    def test_cleanup_on_crash(self, tmp_path):
        f = tmp_path / "test.json"
        with patch("clio.utils.os.replace", side_effect=OSError("rename failed")):
            with pytest.raises(OSError):
                write_json_atomic(f, {"a": 1})
        assert not f.exists()
        tmp_files = list(tmp_path.glob("*.tmp*"))
        assert len(tmp_files) == 0


class TestWriteTextAtomic:
    def test_writes_and_reads_back(self, tmp_path):
        content = "hello world"
        f = tmp_path / "test.txt"
        write_text_atomic(f, content)
        assert f.read_text(encoding="utf-8") == content

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "sub" / "test.txt"
        write_text_atomic(f, "content")
        assert f.exists()

    def test_empty_string(self, tmp_path):
        f = tmp_path / "empty.txt"
        write_text_atomic(f, "")
        assert f.read_text(encoding="utf-8") == ""

    def test_planted_file_never_followed(self, tmp_path):
        collision = (b"\x11" * 8).hex()
        first_name = tmp_path / f".out.txt.{collision}.tmp"
        first_name.write_text("keep me", encoding="utf-8")

        values = iter([b"\x11" * 8, b"\x22" * 8])

        def sequential_urandom(n: int) -> bytes:
            del n
            return next(values)

        with patch("clio.utils.os.urandom", side_effect=sequential_urandom):
            target = tmp_path / "out.txt"
            write_text_atomic(target, "new content")
        assert first_name.read_text(encoding="utf-8") == "keep me"
        assert target.read_text(encoding="utf-8") == "new content"

    def test_all_temp_names_collide_raises(self, tmp_path):
        f = tmp_path / "out.txt"
        with patch("clio.utils.os.open", side_effect=FileExistsError):
            with pytest.raises(OSError):
                write_text_atomic(f, "content")
        assert not f.exists()


class TestRunFfmpeg:
    @patch("clio.utils.popen_subprocess")
    def test_calls_popen(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr = iter([""])
        mock_popen.return_value = mock_proc

        run_ffmpeg(["-i", "in.mp4", "out.mp4"], "ffmpeg")
        mock_popen.assert_called_once()
        args = mock_popen.call_args.args[0]
        assert "ffmpeg" in args[0]

    @patch("clio.utils.popen_subprocess")
    def test_raises_on_nonzero_exit(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.poll.return_value = 1
        mock_proc.wait.return_value = 1
        mock_proc.stderr = iter(["error: something failed", ""])
        mock_popen.return_value = mock_proc

        with pytest.raises(RuntimeError, match="ffmpeg 执行失败"):
            run_ffmpeg(["-i", "in.mp4", "out.mp4"], "ffmpeg")

    @patch("clio.utils.popen_subprocess")
    def test_cancel_event_stops(self, mock_popen):
        import threading

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stderr = iter(["frame=  100 fps=30", ""])
        mock_popen.return_value = mock_proc
        cancel = threading.Event()
        cancel.set()

        with pytest.raises(InterruptedError, match="用户取消"):
            run_ffmpeg(["-i", "in.mp4", "out.mp4"], "ffmpeg", cancel_event=cancel)
        assert mock_proc.terminate.call_count >= 1

    @patch("clio.utils.popen_subprocess")
    def test_progress_callback_fired(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.poll.return_value = 0
        mock_proc.wait.return_value = 0
        mock_proc.stderr = iter(
            [
                "frame=  100 fps=30 size=   10kB time=00:00:01.00",
                "frame=  200 fps=30 size=   20kB time=00:00:02.00",
                "",
            ]
        )
        mock_popen.return_value = mock_proc
        callback = MagicMock()

        run_ffmpeg(["-i", "in.mp4", "out.mp4"], "ffmpeg", progress_callback=callback)
        assert callback.call_count >= 1

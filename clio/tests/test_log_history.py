from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from clio import log_history
from clio.log_history import list_log_files, read_log_file


def _write_log(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_list_log_files_returns_valid_hourly_files_newest_first(tmp_path):
    _write_log(tmp_path / "2026-07-20-21.log", "old")
    _write_log(tmp_path / "2026-07-20-22.log", "newer")
    _write_log(tmp_path / "notes.log", "ignored")
    _write_log(tmp_path / "2026-13-20-22.log", "invalid hour")
    (tmp_path / "2026-07-20-23.log").mkdir()

    files = list_log_files(tmp_path)

    assert [item["name"] for item in files] == [
        "2026-07-20-22.log",
        "2026-07-20-21.log",
    ]
    assert files[0]["size"] == len("newer")
    assert isinstance(files[0]["mtime"], float)


def test_list_log_files_missing_directory_is_empty(tmp_path):
    assert list_log_files(tmp_path / "missing") == []


def test_list_log_files_unreadable_directory_is_empty(tmp_path, monkeypatch):
    def _deny(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "iterdir", _deny)
    assert list_log_files(tmp_path) == []


def test_read_log_file_parses_timestamps_and_continuation_lines(tmp_path):
    name = "2026-07-20-22.log"
    _write_log(
        tmp_path / name,
        "2026-07-20 22:00:00 [INFO] started\n"
        "  traceback continuation\n"
        "plain line\n"
        "2026-07-20 22:00:01 [ERROR] failed\n",
    )

    result = read_log_file(tmp_path, name)

    expected_ts = datetime.strptime("2026-07-20 22:00:00", "%Y-%m-%d %H:%M:%S").timestamp()
    assert result["name"] == name
    assert result["truncated"] is False
    assert result["bytes_read"] > 0
    assert [entry["text"] for entry in result["logs"]] == [
        "[INFO] started",
        "  traceback continuation",
        "plain line",
        "[ERROR] failed",
    ]
    assert result["logs"][0]["ts"] == expected_ts
    assert result["logs"][1]["ts"] == expected_ts
    assert result["logs"][2]["ts"] == expected_ts


def test_read_log_file_without_timestamp_keeps_raw_text(tmp_path):
    name = "2026-07-20-22.log"
    _write_log(tmp_path / name, "plain output\n[INFO] next\n")

    result = read_log_file(tmp_path, name)

    assert result["logs"] == [
        {"ts": None, "text": "plain output"},
        {"ts": None, "text": "[INFO] next"},
    ]


def test_read_log_file_drops_partial_first_tail_line(tmp_path):
    name = "2026-07-20-22.log"
    first = "2026-07-20 22:00:00 [INFO] dropped\n"
    second = "2026-07-20 22:00:01 [INFO] kept\n"
    _write_log(tmp_path / name, first + second)
    max_bytes = len(second.encode("utf-8")) + 4

    result = read_log_file(tmp_path, name, max_bytes=max_bytes)

    assert result["truncated"] is True
    assert [entry["text"] for entry in result["logs"]] == ["[INFO] kept"]


def test_read_log_file_caps_requested_tail_size(tmp_path, monkeypatch):
    name = "2026-07-20-22.log"
    first = "2026-07-20 22:00:00 [INFO] old line\n"
    second = "2026-07-20 22:00:01 [ERROR] newest line\n"
    _write_log(tmp_path / name, first + second)
    monkeypatch.setattr(log_history, "MAX_READ_BYTES", len(second.encode("utf-8")) + 4)

    result = read_log_file(tmp_path, name, max_bytes=10_000)

    assert result["truncated"] is True
    assert [entry["text"] for entry in result["logs"]] == ["[ERROR] newest line"]


@pytest.mark.parametrize(
    "name",
    [
        "",
        "..",
        "../2026-07-20-22.log",
        r"..\2026-07-20-22.log",
        "/tmp/2026-07-20-22.log",
        r"C:\temp\2026-07-20-22.log",
        "2026-07-20-22.txt",
        "2026-13-20-22.log",
        "2026-07-20-99.log",
    ],
)
def test_read_log_file_rejects_non_hourly_basename(tmp_path, name):
    with pytest.raises(ValueError, match="invalid log file name"):
        read_log_file(tmp_path, name)


def test_read_log_file_rejects_bool_max_bytes(tmp_path):
    name = "2026-07-20-22.log"
    _write_log(tmp_path / name, "line\n")
    with pytest.raises(ValueError, match="positive integer"):
        read_log_file(tmp_path, name, max_bytes=True)


def test_read_log_file_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="log file not found"):
        read_log_file(tmp_path, "2026-07-20-22.log")


def test_read_log_file_rejects_symlink_escape(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    outside = _write_log(tmp_path / "outside" / "2026-07-20-22.log", "secret\n")
    link = logs_dir / "2026-07-20-22.log"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ValueError, match="invalid log file name"):
        read_log_file(logs_dir, link.name)

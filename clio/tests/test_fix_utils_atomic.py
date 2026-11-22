"""Regression tests for atomic-write fd ownership (A-01)."""

from __future__ import annotations

import os

import pytest

from clio import utils


def _fail_replace(*_args, **_kwargs):
    raise PermissionError("target locked")


def _tracking_close(monkeypatch):
    closed: list[int] = []
    real_close = os.close

    def close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(utils.os, "close", close)
    return closed


def test_write_bytes_atomic_does_not_close_fd_twice_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "out.json"
    closed = _tracking_close(monkeypatch)
    monkeypatch.setattr(utils.os, "replace", _fail_replace)

    with pytest.raises(PermissionError):
        utils.write_bytes_atomic(target, b"{}")

    assert closed == []
    assert not target.exists()
    assert not list(tmp_path.glob(".out.json.*.tmp"))


def test_write_secret_atomic_does_not_close_fd_twice_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    closed = _tracking_close(monkeypatch)
    monkeypatch.setattr(utils.os, "replace", _fail_replace)

    with pytest.raises(PermissionError):
        utils.write_secret_atomic(target, "KEY=value\n")

    assert closed == []
    assert not list(tmp_path.glob("..env.*.tmp"))

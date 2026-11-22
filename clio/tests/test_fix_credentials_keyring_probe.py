"""Regression tests: a missing OS keyring is logged and reported, not silently hidden."""

from __future__ import annotations

import builtins
import logging
import sys
import types

from clio.credentials.stores import KeyringStore


def test_missing_keyring_package_is_logged_and_described(monkeypatch, caplog):
    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "keyring":
            raise ModuleNotFoundError("No module named 'keyring'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import)
    with caplog.at_level(logging.INFO, logger="clio.credentials"):
        store = KeyringStore()

    assert store.backend is None
    assert store.describe("") == {"available": False, "writable": False, "reason": "keyring package is not installed"}
    assert "keyring package is not installed" in caplog.text


def test_backend_discovery_error_logs_type_only(monkeypatch, caplog):
    fake = types.ModuleType("keyring")

    def _boom():
        raise RuntimeError("secret-looking detail")

    fake.get_keyring = _boom
    monkeypatch.setitem(sys.modules, "keyring", fake)
    with caplog.at_level(logging.INFO, logger="clio.credentials"):
        store = KeyringStore()

    assert store.describe("")["reason"] == "backend discovery failed (RuntimeError)"
    assert "secret-looking detail" not in caplog.text


def test_plaintext_backend_is_rejected_with_reason(monkeypatch):
    fake = types.ModuleType("keyring")

    class PlaintextKeyring:
        priority = 1

    fake.get_keyring = PlaintextKeyring
    monkeypatch.setitem(sys.modules, "keyring", fake)

    store = KeyringStore()

    assert store.backend is None
    assert "unsupported backend" in store.describe("")["reason"]

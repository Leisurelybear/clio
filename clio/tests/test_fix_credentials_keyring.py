"""Regression tests: a transient keyring failure must not downgrade storage to .env."""

from __future__ import annotations

import pytest

from clio.credentials import CredentialService
from clio.credentials.stores import KeyringStore


class FlakyKeyring:
    """In-memory keyring whose next ``get_password`` calls can be made to fail."""

    def __init__(self, failures: int = 1):
        self.values: dict[tuple[str, str], str] = {}
        self.failures = failures

    def get_password(self, service, account):
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("keychain is locked")
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


def test_transient_read_failure_keeps_keyring_backend():
    store = KeyringStore(FlakyKeyring())

    assert store.get("gemini_api_key") is None
    assert store.describe("") == {"available": True, "writable": True}

    store.set("gemini_api_key", "secret-1")
    assert store.get("gemini_api_key") == "secret-1"


def test_default_source_stays_keyring_after_read_failure(tmp_path):
    service = CredentialService({}, tmp_path, environ={}, keyring=KeyringStore(FlakyKeyring()))

    service.status("gemini_api_key")

    assert service.capabilities()["default_source"] == "keyring"


def test_delete_reports_failing_keyring_instead_of_ignoring_it():
    store = KeyringStore(FlakyKeyring())

    with pytest.raises(ValueError, match="keyring operation failed"):
        store.delete("gemini_api_key")


def test_delete_without_backend_still_reports_unavailable():
    store = KeyringStore(FlakyKeyring())
    store.backend = None

    with pytest.raises(ValueError, match="unavailable"):
        store.delete("gemini_api_key")

"""Regression tests: shared credential refs do not depend on provider order."""

from __future__ import annotations

import pytest

from clio.credentials import CredentialService


def _raw(first: dict, second: dict) -> dict:
    return {
        "credentials": {"shared": {"label": "Shared", "env_name": "CLIO_FIX_SHARED_KEY"}},
        "ai": {"providers": {"a": first, "b": second}},
    }


WITH_KEY = {"type": "openai", "credential_ref": "shared", "api_key": "abc"}
WITHOUT_KEY = {"type": "openai", "credential_ref": "shared"}


@pytest.mark.parametrize(("first", "second"), [(WITH_KEY, WITHOUT_KEY), (WITHOUT_KEY, WITH_KEY)])
def test_empty_inline_value_is_ignored_in_either_order(tmp_path, first, second):
    service = CredentialService(_raw(first, second), tmp_path, environ={})

    assert service.resolve("shared") == "abc"


def test_two_different_inline_values_still_conflict(tmp_path):
    other = {**WITH_KEY, "api_key": "xyz"}

    with pytest.raises(ValueError, match="conflicting inline values"):
        CredentialService(_raw(WITH_KEY, other), tmp_path, environ={})

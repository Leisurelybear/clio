"""Regression test: masked placeholders come from one shared definition."""

from __future__ import annotations

import pytest

from clio.ai import provider_security
from clio.credentials.models import MASKED_PLACEHOLDERS, validate_value


def test_provider_security_reuses_shared_placeholders():
    assert provider_security._MASKED_SECRET_VALUES is MASKED_PLACEHOLDERS


@pytest.mark.parametrize("placeholder", sorted(MASKED_PLACEHOLDERS))
def test_every_placeholder_is_rejected_as_a_value(placeholder):
    with pytest.raises(ValueError, match="masked placeholder"):
        validate_value(placeholder)

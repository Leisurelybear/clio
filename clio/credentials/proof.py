"""Short-lived possession receipts keep secrets out of provider draft objects."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

_SIGNING_KEY = secrets.token_bytes(32)


def issue_proof(value: str) -> str:
    expires = str(int(time.time()) + 300)
    digest = hmac.new(_SIGNING_KEY, f"{expires}\0{value}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{digest}"


def verify_proof(proof: object, value: str) -> bool:
    if not isinstance(proof, str) or not value:
        return False
    expires, _, digest = proof.partition(".")
    if not expires.isdigit() or not int(time.time()) <= int(expires) <= int(time.time()) + 300:
        return False
    expected = hmac.new(_SIGNING_KEY, f"{expires}\0{value}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)

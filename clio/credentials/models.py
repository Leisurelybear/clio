from __future__ import annotations

import re
from dataclasses import asdict, dataclass

ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,127}$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
KINDS = {"api_key", "access_key", "secret", "password"}
SOURCES = {"auto", "environment", "keyring", "dotenv", "inline"}
# Placeholders the UI shows instead of a secret; never a real credential value.
MASKED_PLACEHOLDERS = frozenset({"***", "********", "••••••••••"})


@dataclass(frozen=True)
class CredentialMetadata:
    label: str = ""
    kind: str = "api_key"
    source: str = "auto"
    env_name: str = ""
    backend_account: str = ""

    @classmethod
    def parse(cls, credential_id: str, raw: dict) -> CredentialMetadata:
        if not isinstance(credential_id, str) or not ID_RE.fullmatch(credential_id):
            raise ValueError("invalid credential ID")
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise ValueError("credential metadata accepts no secret values or unknown fields")
        if any(not isinstance(value, str) for value in raw.values()):
            raise ValueError("credential metadata fields must be strings")
        meta = cls(**raw)
        if meta.kind not in KINDS or meta.source not in SOURCES or len(meta.label) > 256:
            raise ValueError("invalid credential metadata")
        if meta.backend_account and not ID_RE.fullmatch(meta.backend_account):
            raise ValueError("invalid credential backend account")
        if meta.env_name:
            from clio.credentials.env_policy import _is_blocked_env_name

            if not ENV_RE.fullmatch(meta.env_name) or _is_blocked_env_name(meta.env_name):
                raise ValueError("invalid credential environment name")
        return cls(**{**asdict(meta), "backend_account": meta.backend_account or credential_id})


def validate_value(value: str) -> None:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4096 or any(c in value for c in "\r\n\0"):
        raise ValueError("credential must be a single-line string of at most 4096 bytes")
    if value in MASKED_PLACEHOLDERS:
        raise ValueError("a masked placeholder is not a credential")

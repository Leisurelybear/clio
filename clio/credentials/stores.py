from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Mapping
from functools import wraps
from pathlib import Path
from typing import Any, Protocol

from clio.credentials.environment import inherited_value, sync_dotenv
from clio.credentials.models import ENV_RE, validate_value

logger = logging.getLogger("clio.credentials")

STORE_LOCK = threading.RLock()


def serialized_store_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with STORE_LOCK:
            return function(*args, **kwargs)

    return wrapped


class CredentialStore(Protocol):
    def get(self, account: str) -> str | None: ...
    def set(self, account: str, value: str) -> None: ...
    def delete(self, account: str) -> None: ...
    def contains(self, account: str) -> bool: ...
    def describe(self, account: str) -> dict: ...


class EnvironmentStore:
    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = environ

    def get(self, account: str) -> str | None:
        return inherited_value(account, self.environ) or None

    def set(self, account: str, value: str) -> None:
        raise ValueError("process environment is read-only")

    def delete(self, account: str) -> None:
        raise ValueError("process environment is read-only")

    def contains(self, account: str) -> bool:
        return bool(self.get(account))

    def describe(self, account: str) -> dict:
        return {"available": True, "writable": False}


class DotenvStore:
    def __init__(self, path: Path):
        self.path = path

    def _path(self) -> Path:
        from clio.utils import validate_within_root

        return validate_within_root(self.path, self.path.parent)

    def get(self, account: str) -> str | None:
        from clio.config.loader import parse_dotenv

        path = self._path()
        content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        return parse_dotenv(content).get(account) or None

    def _update(self, account: str, value: str | None) -> None:
        from clio.config.loader import parse_dotenv
        from clio.credentials.env_policy import _is_blocked_env_name
        from clio.utils import write_secret_atomic

        assignment = re.compile(r"^(\s*(?:export\s+)?)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")

        if not ENV_RE.fullmatch(account) or _is_blocked_env_name(account):
            raise ValueError("invalid credential environment name")
        if value is not None:
            validate_value(value)
        with STORE_LOCK:
            path = self._path()
            # utf-8-sig drops a Notepad BOM so the first key matches and is not rewritten with it.
            content = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
            lines = []
            for line in content.splitlines(keepends=True):
                match = assignment.match(line.rstrip("\r\n"))
                if match is None or match.group(2) != account:
                    lines.append(line)
            if value is not None:
                if lines and not lines[-1].endswith("\n"):
                    lines[-1] += "\n"
                lines.append(f"{account}={json.dumps(value, ensure_ascii=False)}\n")
            updated = "".join(lines)
            write_secret_atomic(path, updated)
            sync_dotenv(parse_dotenv(updated), override=True, previous=parse_dotenv(content))

    def set(self, account: str, value: str) -> None:
        self._update(account, value)

    def delete(self, account: str) -> None:
        self._update(account, None)

    def contains(self, account: str) -> bool:
        return bool(self.get(account))

    def describe(self, account: str) -> dict:
        return {"available": True, "writable": True}


class InlineLegacyStore(EnvironmentStore):
    def get(self, account: str) -> str | None:
        return (self.environ or {}).get(account) or None


class KeyringStore:
    """Only native OS backends are accepted; third-party plaintext stores are excluded."""

    namespace = "clio.credentials"

    def __init__(self, backend: Any = None):
        self.backend = backend
        if backend is None:
            try:
                import keyring

                candidate = keyring.get_keyring()
                if (
                    type(candidate).__module__
                    in {
                        "keyring.backends.Windows",
                        "keyring.backends.macOS",
                        "keyring.backends.SecretService",
                        "keyring.backends.kwallet",
                    }
                    and candidate.priority > 0
                ):
                    self.backend = candidate
            except Exception:  # noqa: BLE001 - optional platform backend discovery
                pass

    def _call(self, method: str, account: str, *args: str) -> Any:
        if self.backend is None:
            raise ValueError("OS keyring is unavailable")
        try:
            return getattr(self.backend, method)(self.namespace, account, *args)
        except Exception:  # noqa: BLE001 - platform errors must never echo a credential
            raise ValueError("OS keyring operation failed") from None

    def get(self, account: str) -> str | None:
        try:
            return self._call("get_password", account)
        except ValueError:
            # A locked keychain, DBus timeout or denied prompt is transient. Keep the
            # backend so later saves never silently fall back to a plaintext .env.
            if self.backend is not None and not getattr(self, "_read_failure_logged", False):
                self._read_failure_logged = True
                logger.warning("OS keyring read failed; treating the credential as missing")
            return None

    def set(self, account: str, value: str) -> None:
        validate_value(value)
        self._call("set_password", account, value)

    def delete(self, account: str) -> None:
        # Read through _call so an unavailable or failing keyring is reported, not ignored.
        if self._call("get_password", account) is not None:
            self._call("delete_password", account)

    def contains(self, account: str) -> bool:
        return bool(self.get(account))

    def describe(self, account: str) -> dict:
        return {"available": self.backend is not None, "writable": self.backend is not None}

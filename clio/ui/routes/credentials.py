"""Field-level credential operations. Responses contain metadata unless explicitly revealed."""

from __future__ import annotations

import hmac
from dataclasses import asdict, replace
from typing import Any
from urllib.parse import unquote

import yaml

from clio.ai.factory import _clear_provider_cache
from clio.credentials import CredentialService
from clio.credentials.models import CredentialMetadata, validate_value
from clio.credentials.proof import issue_proof
from clio.credentials.stores import STORE_LOCK
from clio.ui.handler_protocol import HandlerProtocol
from clio.utils import validate_within_root, write_text_atomic


def _service(handler: HandlerProtocol) -> CredentialService:
    path = handler.config_path
    if path is None or not path.is_file():
        raise ValueError("global config is unavailable")
    validate_within_root(path, path.parent)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("global config must be a mapping")
    return CredentialService(raw, path.parent)


def _invalidate(handler: HandlerProtocol, service: CredentialService, ref: str) -> None:
    handler.__class__._config_cache.invalidate_all()
    names = {name for name, credential in service.provider_refs.items() if credential == ref}
    _clear_provider_cache(names)


def _save_metadata(handler: HandlerProtocol, service: CredentialService, ref: str, *, migrate: bool = False) -> None:
    raw = service.raw
    raw.setdefault("credentials", {})[ref] = asdict(service.metadata(ref))
    if migrate:
        for name, credential in service.provider_refs.items():
            if credential == ref:
                provider = raw["ai"]["providers"][name]
                provider["credential_ref"] = ref
                provider.pop("api_key", None)
    assert handler.config_path is not None
    write_text_atomic(handler.config_path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))


def _error(handler: HandlerProtocol, exc: Exception) -> None:
    status = 404 if isinstance(exc, KeyError) else 403 if isinstance(exc, PermissionError) else 400
    # Backend exceptions may include user input; never return their message.
    handler._send_json(
        {"ok": False, "error": "Credential operation failed; check source, confirmation and metadata."}, status
    )


def handle_get_credentials(handler: HandlerProtocol, qs: dict[str, Any]) -> None:
    try:
        service = _service(handler)
        entries = [service.status(ref) for ref in sorted(service.registry)]
        handler._send_json({"ok": True, "credentials": entries, "backends": service.capabilities()})
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        _error(handler, exc)


def handle_put_credential(handler: HandlerProtocol, qs: dict[str, Any], obj: dict, credential_id: str) -> None:
    try:
        with STORE_LOCK:
            service = _service(handler)
            ref = unquote(credential_id)
            new = ref not in service.registry
            if new:
                fields = obj.get("metadata", {})
                meta = CredentialMetadata.parse(ref, fields)
                if not meta.env_name or meta.env_name.lower() != ref or meta.backend_account != ref:
                    raise ValueError("new credential must own its canonical environment slot")
                service.registry[ref] = meta
            elif "metadata" in obj and CredentialMetadata.parse(ref, obj["metadata"]) != service.metadata(ref):
                raise ValueError("credential metadata cannot be retargeted through a field write")
            if "value" not in obj:
                if new:
                    raise ValueError("new credentials require a value")
                return handler._send_json({"ok": True, "credential": service.status(ref)})
            value = obj["value"]
            validate_value(value)
            if obj.get("confirm_binding") is True:
                previous_value = service.resolve(ref)
                if previous_value and not hmac.compare_digest(value.encode(), previous_value.encode()):
                    raise ValueError("re-enter the current credential to confirm a changed provider target")
            if value == "" and obj.get("confirmed") is not True:
                raise ValueError("deletion requires confirmation")
            source = obj.get("source", "")
            service.write(ref, value or None, source)
            _save_metadata(handler, service, ref)
            _invalidate(handler, service, ref)
            response = {"ok": True, "credential": service.status(ref)}
            if value:
                response["credential_proof"] = issue_proof(value)
            handler._send_json(response)
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        _error(handler, exc)


def handle_post_credential_reveal(handler: HandlerProtocol, qs: dict[str, Any], obj: dict, credential_id: str) -> None:
    try:
        if obj.get("confirmed") is not True:
            raise ValueError("reveal requires confirmation")
        service = _service(handler)
        value = service.resolve(unquote(credential_id))
        handler._send_json({"ok": True, "value": value}, extra_headers={"Pragma": "no-cache"})
    except (OSError, UnicodeError, KeyError, ValueError, yaml.YAMLError) as exc:
        _error(handler, exc)


def handle_delete_credential(handler: HandlerProtocol, qs: dict[str, Any], credential_id: str) -> None:
    try:
        if qs.get("confirmed") != ["true"]:
            raise ValueError("deletion requires confirmation")
        with STORE_LOCK:
            service = _service(handler)
            ref = unquote(credential_id)
            source = (qs.get("source") or [""])[0]
            service.write(ref, None, source)
            _invalidate(handler, service, ref)
            handler._send_json({"ok": True, "credential": service.status(ref)})
    except (OSError, UnicodeError, KeyError, ValueError, yaml.YAMLError) as exc:
        _error(handler, exc)


def handle_post_credential_migrate(handler: HandlerProtocol, qs: dict[str, Any], obj: dict, credential_id: str) -> None:
    try:
        if obj.get("confirmed") is not True:
            raise ValueError("migration requires confirmation")
        with STORE_LOCK:
            service = _service(handler)
            ref = unquote(credential_id)
            value = service.resolve(ref)
            if not value:
                raise ValueError("credential is empty")
            requested = obj.get("source", service.capabilities()["default_source"])
            target = requested
            if requested == "keyring" and not service.capabilities()["keyring"]["available"]:
                target = "dotenv"
            service.write(ref, value, target)
            # Verify the destination before updating references or offering cleanup.
            meta = service.metadata(ref)
            assert service.dotenv is not None
            copied = (
                service.keyring.get(meta.backend_account) if target == "keyring" else service.dotenv.get(meta.env_name)
            )
            if copied != value:
                raise ValueError("migration verification failed")
            service.registry[ref] = replace(meta, source="auto")
            _save_metadata(handler, service, ref, migrate=True)
            service.legacy.pop(ref, None)
            _invalidate(handler, service, ref)
            handler._send_json(
                {"ok": True, "credential": service.status(ref), "source": target, "fallback": target != requested}
            )
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        _error(handler, exc)

"""Behavioral coverage of credential storage, runtime, API, and retirement."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml

from clio.config import load_global_config
from clio.credentials import CredentialService
from clio.credentials.models import CredentialMetadata
from clio.credentials.stores import DotenvStore, KeyringStore
from clio.ui.routes import credentials as routes


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture
def backend(monkeypatch):
    keyring = KeyringStore(MemoryKeyring())
    monkeypatch.setattr("clio.credentials.service.KeyringStore", lambda: keyring)
    return keyring


@pytest.fixture
def handler(tmp_path, monkeypatch, backend):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "config_version": "V2",
                "ai": {
                    "providers": {
                        "gemini": {"type": "gemini", "api_key_env": "GEMINI_API_KEY", "models": ["test"]},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    instance = MagicMock()
    instance.config_path = path
    instance.__class__._config_cache = MagicMock()
    return instance


def payload(handler):
    return handler._send_json.call_args.args[0]


def test_precedence_shadowing_and_source_policy(tmp_path, backend):
    DotenvStore(tmp_path / ".env").set("GEMINI_API_KEY", "dotenv-secret")
    backend.set("gemini_api_key", "keyring-secret")
    raw: dict[str, Any] = {"ai": {"providers": {"gemini": {"api_key": "inline-secret"}}}}
    service = CredentialService(raw, tmp_path, environ={"GEMINI_API_KEY": "shell-secret"})
    assert service.resolve("gemini_api_key") == "shell-secret"
    status = service.status("gemini_api_key")
    assert status["effective_source"] == "environment"
    assert status["shadowed_sources"] == ["keyring", "dotenv", "inline"]
    assert "shell-secret" not in json.dumps(status)
    assert "hash" not in status
    assert CredentialService(raw, tmp_path, environ={}).resolve("gemini_api_key") == "keyring-secret"
    raw["credentials"] = {"gemini_api_key": {"source": "dotenv", "env_name": "GEMINI_API_KEY"}}
    assert CredentialService(raw, tmp_path, environ={}).resolve("gemini_api_key") == "dotenv-secret"


def test_legacy_custom_environment_name_is_respected(tmp_path, backend):
    raw = {"ai": {"providers": {"gemini": {"api_key_env": "CUSTOM_KEY"}}}}
    service = CredentialService(raw, tmp_path, environ={"CUSTOM_KEY": "legacy-secret"})
    assert service.resolve_provider(raw["ai"]["providers"]["gemini"], "gemini") == "legacy-secret"


def test_dotenv_atomic_preservation_roundtrip_and_delete(tmp_path, monkeypatch):
    monkeypatch.delenv("ROUNDTRIP_KEY", raising=False)
    store = DotenvStore(tmp_path / ".env")
    store.set("ROUNDTRIP_KEY", "first")
    with store.path.open("a", encoding="utf-8") as file:
        file.write("# keep comment\nSAFE_SETTING=value\nexport ROUNDTRIP_KEY=duplicate\n")
    secret = "  a'\"\\$#=b  "
    store.set("ROUNDTRIP_KEY", secret)
    assert store.get("ROUNDTRIP_KEY") == secret
    assert store.path.read_text(encoding="utf-8").count("ROUNDTRIP_KEY=") == 1
    store.delete("ROUNDTRIP_KEY")
    assert store.path.read_text(encoding="utf-8") == "# keep comment\nSAFE_SETTING=value\n"
    assert "ROUNDTRIP_KEY" not in os.environ


def test_concurrent_field_writes_keep_both_entries(tmp_path):
    store = DotenvStore(tmp_path / ".env")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda name: store.set(name, name + "-secret"), ["TEST_ALPHA_KEY", "TEST_BETA_KEY"]))
    assert store.get("TEST_ALPHA_KEY") == "TEST_ALPHA_KEY-secret"
    assert store.get("TEST_BETA_KEY") == "TEST_BETA_KEY-secret"


def test_save_preserves_inherited_environment(handler, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "shell-secret")
    routes.handle_put_credential(handler, {}, {"value": "new-secret", "source": "dotenv"}, "gemini_api_key")
    assert payload(handler)["ok"]
    assert payload(handler)["credential"]["effective_source"] == "environment"
    assert "dotenv" in payload(handler)["credential"]["shadowed_sources"]
    assert os.environ["GEMINI_API_KEY"] == "shell-secret"


def test_reveal_is_explicit_and_inventory_is_redacted(handler):
    routes.handle_put_credential(handler, {}, {"value": "new-secret", "source": "keyring"}, "gemini_api_key")
    routes.handle_get_credentials(handler, {})
    assert "new-secret" not in json.dumps(payload(handler))
    routes.handle_post_credential_reveal(handler, {}, {}, "gemini_api_key")
    assert payload(handler)["ok"] is False
    routes.handle_post_credential_reveal(handler, {}, {"confirmed": True}, "gemini_api_key")
    assert payload(handler)["value"] == "new-secret"
    assert handler._send_json.call_args.kwargs["extra_headers"]["Pragma"] == "no-cache"


def test_omitted_value_is_noop_and_empty_requires_confirmation(handler, backend):
    backend.set("gemini_api_key", "keep-secret")
    routes.handle_put_credential(handler, {}, {"source": "keyring"}, "gemini_api_key")
    assert payload(handler)["ok"]
    routes.handle_put_credential(handler, {}, {"source": "keyring", "value": ""}, "gemini_api_key")
    assert not payload(handler)["ok"]
    assert backend.get("gemini_api_key") == "keep-secret"


@pytest.mark.parametrize("value", ["one\ntwo", "one\rtwo", "one\0two", "x" * 4097, "********", 123, None])
def test_invalid_values_cannot_be_persisted_or_echoed(handler, value):
    routes.handle_put_credential(handler, {}, {"source": "dotenv", "value": value}, "gemini_api_key")
    assert not payload(handler)["ok"]
    assert not (handler.config_path.parent / ".env").exists()


@pytest.mark.parametrize(
    "fields",
    [
        {"value": "secret"},
        {"kind": "unknown"},
        {"source": "unknown"},
        {"env_name": "PYTHONPATH"},
        {"backend_account": "../escape"},
    ],
)
def test_invalid_metadata_is_rejected(fields):
    with pytest.raises(ValueError):
        CredentialMetadata.parse("test_key", fields)


def test_migration_copies_verifies_and_preserves_dotenv(handler, backend):
    store = DotenvStore(handler.config_path.parent / ".env")
    store.set("GEMINI_API_KEY", "legacy-secret")
    routes.handle_post_credential_migrate(handler, {}, {"confirmed": True, "source": "keyring"}, "gemini_api_key")
    assert payload(handler)["ok"]
    assert backend.get("gemini_api_key") == "legacy-secret"
    assert store.get("GEMINI_API_KEY") == "legacy-secret"
    raw = yaml.safe_load(handler.config_path.read_text(encoding="utf-8"))
    assert raw["ai"]["providers"]["gemini"]["credential_ref"] == "gemini_api_key"
    assert "api_key" not in raw["ai"]["providers"]["gemini"]
    assert "legacy-secret" not in handler.config_path.read_text(encoding="utf-8")
    assert load_global_config(handler.config_path).ai.providers["gemini"].api_key == "legacy-secret"


def test_keyring_unavailable_falls_back_only_for_explicit_migration(handler, backend):
    raw = yaml.safe_load(handler.config_path.read_text(encoding="utf-8"))
    raw["ai"]["providers"]["gemini"]["api_key"] = "legacy-inline-secret"
    handler.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    backend.backend = None
    routes.handle_put_credential(handler, {}, {"value": "new-secret", "source": "keyring"}, "gemini_api_key")
    assert not payload(handler)["ok"]
    routes.handle_post_credential_migrate(handler, {}, {"confirmed": True, "source": "keyring"}, "gemini_api_key")
    assert payload(handler)["ok"]
    assert payload(handler)["fallback"] is True
    assert payload(handler)["source"] == "dotenv"
    assert load_global_config(handler.config_path).ai.providers["gemini"].api_key == "legacy-inline-secret"


def test_implicit_provider_retarget_requires_possession(handler, backend):
    from clio.ui.routes.config_routes import handle_put_config_global

    raw = {
        "config_version": "V2",
        "ai": {
            "providers": {
                "custom": {"type": "openai", "base_url": "https://old.example/v1", "models": ["test"]},
            }
        },
    }
    handler.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    backend.set("custom_api_key", "current-secret")
    candidate = yaml.safe_load(handler.config_path.read_text(encoding="utf-8"))
    candidate["ai"]["providers"]["custom"]["base_url"] = "https://new.example/v1"

    handle_put_config_global(handler, {}, candidate)

    assert payload(handler)["ok"] is False
    assert "re-enter the current API key" in payload(handler)["error"]
    assert yaml.safe_load(handler.config_path.read_text(encoding="utf-8"))["ai"]["providers"]["custom"]["base_url"] == (
        "https://old.example/v1"
    )


def test_implicit_credential_is_used_by_unsaved_provider_test(tmp_path, backend, monkeypatch):
    from clio.ai.base import AIResponse
    from clio.config import AppConfig, load_global_config
    from clio.ui.services.ai_test_service import test_provider_connection

    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "config_version": "V2",
                "ai": {
                    "providers": {"custom": {"type": "openai", "base_url": "https://example.com/v1", "models": ["m"]}}
                },
            }
        ),
        encoding="utf-8",
    )
    backend.set("custom_api_key", "current-secret")
    config = AppConfig(global_cfg=load_global_config(path), project_cfg=None)
    fake_provider = MagicMock()
    fake_provider.generate_text.return_value = AIResponse(text="ok")
    monkeypatch.setattr("clio.ui.services.ai_test_service._build_provider", lambda *_args: fake_provider)

    result = test_provider_connection(
        config,
        provider_name="custom",
        model="m",
        provider_override={"type": "openai", "base_url": "https://example.com/v1", "models": ["m"]},
    )

    assert result["ok"] is True


def test_candidate_metadata_cannot_retarget_implicit_credential(handler, backend):
    from clio.ui.routes.config_routes import handle_put_config_global

    raw = {
        "config_version": "V2",
        "ai": {
            "providers": {
                "custom": {"type": "openai", "base_url": "https://old.example/v1", "models": ["test"]},
            }
        },
    }
    handler.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    backend.set("custom_api_key", "current-secret")
    candidate = yaml.safe_load(handler.config_path.read_text(encoding="utf-8"))
    candidate["credentials"] = {"custom_api_key": {"env_name": "OTHER_API_KEY"}}
    candidate["ai"]["providers"]["custom"]["base_url"] = "https://new.example/v1"

    handle_put_config_global(handler, {}, candidate)

    assert payload(handler)["ok"] is False
    assert "credential API" in payload(handler)["error"]


def test_implicit_credential_id_collision_is_rejected(tmp_path, backend):
    raw = {
        "ai": {
            "providers": {
                "my-model": {"type": "openai", "api_key": "first-secret"},
                "my_model": {"type": "openai", "api_key": "second-secret"},
            }
        }
    }

    with pytest.raises(ValueError, match="share an implicit credential reference"):
        CredentialService(raw, tmp_path, keyring=backend)


def test_migration_preserves_explicit_destination_source(handler, backend):
    raw = {
        "config_version": "V2",
        "credentials": {"custom_api_key": {"source": "inline", "env_name": "CUSTOM_API_KEY"}},
        "ai": {
            "providers": {
                "custom": {
                    "type": "openai",
                    "credential_ref": "custom_api_key",
                    "api_key": "inline-secret",
                    "models": ["test"],
                }
            }
        },
    }
    handler.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    backend.set("custom_api_key", "stale-keyring-secret")

    routes.handle_post_credential_migrate(handler, {}, {"confirmed": True, "source": "dotenv"}, "custom_api_key")

    assert payload(handler)["ok"] is True
    assert payload(handler)["credential"]["effective_source"] == "dotenv"
    assert load_global_config(handler.config_path).ai.providers["custom"].api_key == "inline-secret"


def test_credential_write_rolls_back_backend_when_metadata_save_fails(handler, backend, monkeypatch):
    backend.set("gemini_api_key", "old-secret")
    monkeypatch.setattr(
        "clio.ui.routes.credentials.write_text_atomic", lambda *args: (_ for _ in ()).throw(PermissionError())
    )

    routes.handle_put_credential(handler, {}, {"value": "new-secret", "source": "keyring"}, "gemini_api_key")

    assert payload(handler)["ok"] is False
    assert backend.get("gemini_api_key") == "old-secret"


def test_migration_rolls_back_destination_when_metadata_save_fails(handler, backend, monkeypatch):
    store = DotenvStore(handler.config_path.parent / ".env")
    store.set("GEMINI_API_KEY", "legacy-secret")
    monkeypatch.setattr(
        "clio.ui.routes.credentials.write_text_atomic", lambda *args: (_ for _ in ()).throw(PermissionError())
    )

    routes.handle_post_credential_migrate(handler, {}, {"confirmed": True, "source": "keyring"}, "gemini_api_key")

    assert payload(handler)["ok"] is False
    assert backend.get("gemini_api_key") is None
    assert store.get("GEMINI_API_KEY") == "legacy-secret"


def test_delete_selected_backend_reports_remaining_source(handler, backend):
    backend.set("gemini_api_key", "keyring-secret")
    DotenvStore(handler.config_path.parent / ".env").set("GEMINI_API_KEY", "legacy-secret")
    routes.handle_delete_credential(handler, {"source": ["keyring"], "confirmed": ["true"]}, "gemini_api_key")
    assert payload(handler)["credential"]["effective_source"] == "dotenv"
    assert backend.get("gemini_api_key") is None


def test_runtime_doctor_and_asr_resolve_keyring(handler, backend):
    from clio.asr.readiness import check_asr_ready
    from clio.config import AppConfig, ProjectConfig
    from clio.doctor import provider_key_missing

    backend.set("gemini_api_key", "provider-secret")
    backend.set("dashscope_api_key", "asr-secret")
    cfg = load_global_config(handler.config_path)
    assert cfg.ai.providers["gemini"].api_key == "provider-secret"
    assert provider_key_missing(cfg.ai.providers["gemini"], {}) is None
    project = ProjectConfig()
    project.whisper.engine = "aliyun"
    app = AppConfig(global_cfg=cfg, project_cfg=project)
    assert check_asr_ready(app).ready
    assert "provider-secret" not in repr(cfg.ai.providers["gemini"])


def test_credential_bodies_are_never_captured_in_access_logs():
    from clio.http_access_log import prepare_body

    assert prepare_body(b'{"value":"secret"}', path="/api/credentials/gemini_api_key/reveal", limit=4096) is None


def test_unknown_reference_and_project_ownership(handler):
    from clio.ui.routes.config_routes import _validate_no_foreign_fields

    assert _validate_no_foreign_fields({"credentials": {}}, "project")
    with pytest.raises(ValueError):
        CredentialService({"ai": {"providers": {"test": {"credential_ref": "missing"}}}})


def test_possession_receipt_cannot_authorize_unrelated_secret(handler):
    from clio.ai.provider_security import provider_rebind_error
    from clio.credentials.proof import issue_proof

    previous = {"api_key_env": "GEMINI_API_KEY", "api_key": "old-secret", "base_url": "https://old.example"}
    candidate = {
        "api_key_env": "GEMINI_API_KEY",
        "base_url": "https://new.example",
        "credential_proof": issue_proof("other-secret"),
    }
    assert provider_rebind_error("gemini", previous, candidate)
    candidate["credential_proof"] = issue_proof("old-secret")
    assert provider_rebind_error("gemini", previous, candidate) is None


def test_changed_binding_requires_current_value_before_rotation(handler, backend):
    backend.set("gemini_api_key", "current-secret")
    routes.handle_put_credential(
        handler,
        {},
        {
            "source": "keyring",
            "value": "different-secret",
            "confirm_binding": True,
        },
        "gemini_api_key",
    )
    assert payload(handler)["ok"] is False
    assert backend.get("gemini_api_key") == "current-secret"


def test_provider_reference_save_uses_receipt_and_strips_secrets(handler, backend, monkeypatch):
    from clio.ui.routes.config_routes import handle_put_config_global

    monkeypatch.setattr("clio.ui.routes.config_routes._validate_global_against_registry", lambda *args: [])
    routes.handle_put_credential(handler, {}, {"source": "keyring", "value": "new-secret"}, "gemini_api_key")
    receipt = payload(handler)["credential_proof"]
    raw = yaml.safe_load(handler.config_path.read_text(encoding="utf-8"))
    raw["ai"]["providers"]["gemini"].update(credential_ref="gemini_api_key", credential_proof=receipt)
    handle_put_config_global(handler, {}, raw)
    assert payload(handler)["ok"]
    stored = handler.config_path.read_text(encoding="utf-8")
    assert "new-secret" not in stored
    assert "credential_proof" not in stored
    assert load_global_config(handler.config_path).ai.providers["gemini"].api_key == "new-secret"


def test_raw_env_cannot_overwrite_managed_secret(handler):
    from clio.ui.routes.env_routes import handle_put_env

    routes.handle_put_credential(handler, {}, {"source": "dotenv", "value": "keep-secret"}, "gemini_api_key")
    handle_put_env(handler, {}, {"content": "GEMINI_API_KEY=overwritten\n"})
    assert handler._send_json.call_args.args[1] == 409
    assert DotenvStore(handler.config_path.parent / ".env").get("GEMINI_API_KEY") == "keep-secret"


def test_raw_env_mask_roundtrip_preserves_quoted_managed_values(handler):
    from clio.ui.routes.env_routes import handle_get_env, handle_put_env

    secret = " a'\"\\key "
    routes.handle_put_credential(handler, {}, {"source": "dotenv", "value": secret}, "gemini_api_key")
    handle_get_env(handler, {})
    content = payload(handler)["content"] + "SAFE_SETTING=kept\n"
    handle_put_env(handler, {}, {"content": content})
    assert payload(handler)["ok"]
    assert DotenvStore(handler.config_path.parent / ".env").get("GEMINI_API_KEY") == secret


def test_reveal_http_auth_origin_and_no_store(handler, backend):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from clio.config import AppConfig
    from clio.ui.server import make_handler

    backend.set("gemini_api_key", "http-secret")
    cfg = AppConfig(global_cfg=load_global_config(handler.config_path), project_cfg=None)
    cls = make_handler(cfg, config_path=handler.config_path, api_token="test-token", enforce_request_origin=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}/api/credentials/gemini_api_key/reveal"
    try:

        def request(token=True, origin=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = "Bearer test-token"
            if origin:
                headers["Origin"] = origin
            return urlopen(Request(endpoint, data=b'{"confirmed":true}', headers=headers, method="POST"), timeout=5)

        with pytest.raises(HTTPError) as error:
            request(token=False)
        assert error.value.code == 401
        with pytest.raises(HTTPError) as error:
            request(origin="https://foreign.example")
        assert error.value.code == 403
        with request() as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert json.load(response)["value"] == "http-secret"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

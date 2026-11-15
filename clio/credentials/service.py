from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from clio.credentials.models import CredentialMetadata, validate_value
from clio.credentials.stores import CredentialStore, DotenvStore, EnvironmentStore, InlineLegacyStore, KeyringStore

BUILTIN_ENVS = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "MOONSHOT_API_KEY",
    "TONGYI_API_KEY",
    "ALIYUN_ASR_API_KEY",
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_SECRET",
    "BAIDU_APP_KEY",
    "BAIDU_SECRET_KEY",
    "BAIDU_ASR_API_KEY",
    "BAIDU_ASR_SECRET_KEY",
)


def credential_required(provider_type: str) -> bool:
    return provider_type != "ollama"


class CredentialService:
    def __deepcopy__(self, memo):
        # Stateless readers share platform backend objects; never copy OS handles.
        return self

    def __init__(
        self,
        raw: dict | None = None,
        base: Path | None = None,
        *,
        environ: Mapping[str, str] | None = None,
        keyring: KeyringStore | None = None,
    ):
        from clio.ai.provider_security import provider_env_name

        self.raw = raw or {}
        self.environment = EnvironmentStore(environ)
        self.dotenv = DotenvStore(base / ".env") if base is not None else None
        self.keyring = keyring if keyring is not None else KeyringStore()
        self.registry: dict[str, CredentialMetadata] = {}
        self.consumers: dict[str, list[str]] = {}
        self.legacy: dict[str, str] = {}
        self.provider_refs: dict[str, str] = {}
        metadata = self.raw.get("credentials") or {}
        if not isinstance(metadata, dict):
            raise ValueError("credentials must be a mapping")
        for credential_id, fields in metadata.items():
            self.registry[credential_id] = CredentialMetadata.parse(credential_id, fields)
        for env in BUILTIN_ENVS:
            self._discover(env.lower(), env)
        providers = (self.raw.get("ai") or {}).get("providers") or {}
        for name, provider in providers.items():
            if not isinstance(provider, dict):
                continue
            ref = provider.get("credential_ref") or provider_env_name(name).lower()
            if provider.get("credential_ref") and ref not in self.registry:
                raise ValueError("unknown provider credential reference")
            if not provider.get("credential_ref") and ref not in metadata:
                self.registry.pop(ref, None)
            self._discover(ref, provider.get("api_key_env") or provider_env_name(name))
            self.provider_refs[name] = ref
            self.consumers.setdefault(ref, []).append(f"ai.providers.{name}")
            inline = provider.get("api_key") or ""
            if inline not in {"***", "********", "••••••••••"}:
                self.legacy[ref] = inline
        self.consumers.setdefault("dashscope_api_key", []).append("asr.aliyun")

    def _discover(self, ref: str, env: str) -> None:
        if ref not in self.registry:
            self.registry[ref] = CredentialMetadata.parse(ref, {"label": env, "env_name": env})

    def metadata(self, ref: str) -> CredentialMetadata:
        if ref not in self.registry:
            raise KeyError("credential not found")
        return self.registry[ref]

    def _values(self, ref: str) -> dict[str, str]:
        meta = self.metadata(ref)
        values = {
            "environment": self.environment.get(meta.env_name) if meta.env_name else None,
            "keyring": self.keyring.get(meta.backend_account),
            "dotenv": self.dotenv.get(meta.env_name) if self.dotenv and meta.env_name else None,
            "inline": InlineLegacyStore(self.legacy).get(ref),
        }
        return {source: value for source, value in values.items() if value}

    def resolve(self, ref: str) -> str:
        values = self._values(ref)
        source = self.metadata(ref).source
        return next(iter(values.values()), "") if source == "auto" else values.get(source, "")

    def resolve_provider(self, provider: dict, name: str = "") -> str:
        ref = provider.get("credential_ref") or self.provider_refs.get(name)
        if ref:
            return self.resolve(ref)
        env = provider.get("api_key_env") or ""
        if env and env.lower() in self.registry and self.registry[env.lower()].env_name == env:
            value = self.resolve(env.lower())
            if value:
                return value
        fallback = self.environment.get(env) or (self.dotenv.get(env) if self.dotenv and env else None)
        inline = provider.get("api_key") or ""
        return fallback or ("" if inline in {"***", "********", "••••••••••"} else inline)

    def status(self, ref: str) -> dict:
        meta = self.metadata(ref)
        values = self._values(ref)
        source = next(iter(values), "") if meta.source == "auto" else meta.source if meta.source in values else ""
        value = values.get(source, "")
        return {
            "id": ref,
            **asdict(meta),
            "configured": bool(value),
            "effective_source": source,
            "shadowed_sources": [item for item in values if item != source],
            "masked_preview": f"••••{value[-4:]}" if len(value) > 4 else "••••" if value else "",
            "consumers": self.consumers.get(ref, []),
            "migration_needed": ref not in (self.raw.get("credentials") or {}) or bool(self.legacy.get(ref)),
        }

    def capabilities(self) -> dict:
        keyring = self.keyring.describe("")
        return {
            "keyring": keyring,
            "dotenv": {"available": self.dotenv is not None, "writable": self.dotenv is not None},
            "environment": {"available": True, "writable": False},
            "default_source": "keyring" if keyring["available"] else "dotenv",
        }

    def write(self, ref: str, value: str | None, source: str) -> None:
        meta = self.metadata(ref)
        store: CredentialStore
        if source == "keyring":
            store, account = self.keyring, meta.backend_account
        elif source == "dotenv" and self.dotenv is not None and meta.env_name:
            store, account = self.dotenv, meta.env_name
        else:
            raise ValueError("select an available writable credential source")
        if value is None:
            store.delete(account)
        else:
            validate_value(value)
            store.set(account, value)

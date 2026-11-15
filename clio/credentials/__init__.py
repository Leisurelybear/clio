"""Credential resolution and storage, shared by runtime and settings."""

from clio.credentials.service import CredentialService, credential_required

__all__ = ["CredentialService", "credential_required"]

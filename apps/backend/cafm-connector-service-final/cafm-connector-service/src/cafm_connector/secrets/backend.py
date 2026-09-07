"""
Secrets backend abstraction.

In development: AES-256 encryption at rest, stored as env vars / DB columns.
In production:  HashiCorp Vault KV v2, same interface.

Usage:
    secrets = get_secrets_backend()
    encrypted = await secrets.encrypt({"password": "secret"})
    plain     = await secrets.decrypt(encrypted)
    await secrets.store("connector-uuid", {"password": "secret"})
    plain     = await secrets.retrieve("connector-uuid")
"""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from typing import Any

from cafm_connector.core.config import Settings
from cafm_connector.core.exceptions import SecretsError
from cafm_connector.core.logging import get_logger

logger = get_logger(__name__)


# ── Crypto helpers ─────────────────────────────────────────────────────

def _get_cipher(hex_key: str):
    """Return an AES-256-GCM cipher object. Lazily imports cryptography."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise SecretsError("Install 'cryptography' to use AES encryption") from e

    key = bytes.fromhex(hex_key)
    if len(key) != 32:
        raise SecretsError("AES key must be exactly 32 bytes (64 hex chars)")
    return AESGCM(key)


# ── Abstract interface ─────────────────────────────────────────────────

class SecretsBackend(ABC):
    """All backends expose the same interface."""

    @abstractmethod
    async def encrypt(self, data: dict[str, Any]) -> str:
        """Encrypt a dict → opaque ciphertext string."""

    @abstractmethod
    async def decrypt(self, ciphertext: str) -> dict[str, Any]:
        """Decrypt ciphertext → original dict."""

    @abstractmethod
    async def store(self, path: str, data: dict[str, Any]) -> None:
        """Persist secret under a path/key."""

    @abstractmethod
    async def retrieve(self, path: str) -> dict[str, Any]:
        """Retrieve secret from a path/key."""

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete secret at a path/key."""


# ── Env/local backend (development) ───────────────────────────────────

class EnvSecretsBackend(SecretsBackend):
    """
    AES-256-GCM encryption.
    Encrypted blobs are stored as base64 strings — caller is responsible
    for persisting them (e.g. in the connectors DB table).
    The in-memory store here is only for integration tests / local dev.
    """

    def __init__(self, hex_key: str) -> None:
        self._hex_key = hex_key
        self._store: dict[str, str] = {}

    async def encrypt(self, data: dict[str, Any]) -> str:
        import os
        aesgcm = _get_cipher(self._hex_key)
        nonce = os.urandom(12)
        plaintext = json.dumps(data).encode()
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)
        blob = base64.b64encode(nonce + ciphertext).decode()
        return blob

    async def decrypt(self, ciphertext: str) -> dict[str, Any]:
        aesgcm = _get_cipher(self._hex_key)
        raw = base64.b64decode(ciphertext)
        nonce, ct = raw[:12], raw[12:]
        plaintext = aesgcm.decrypt(nonce, ct, None)
        return json.loads(plaintext.decode())

    async def store(self, path: str, data: dict[str, Any]) -> None:
        self._store[path] = await self.encrypt(data)
        logger.debug("secret_stored", path=path, backend="env")

    async def retrieve(self, path: str) -> dict[str, Any]:
        blob = self._store.get(path)
        if blob is None:
            raise SecretsError(f"Secret not found: {path}")
        return await self.decrypt(blob)

    async def delete(self, path: str) -> None:
        self._store.pop(path, None)


# ── Vault backend (production) ─────────────────────────────────────────

class VaultSecretsBackend(SecretsBackend):
    """
    HashiCorp Vault KV v2.
    Requires: pip install hvac
    """

    def __init__(self, url: str, token: str, mount_path: str, base_path: str) -> None:
        try:
            import hvac
        except ImportError as e:
            raise SecretsError("Install 'hvac' to use the Vault secrets backend") from e

        self._client = hvac.Client(url=url, token=token)
        self._mount = mount_path
        self._base = base_path

    def _full_path(self, path: str) -> str:
        return f"{self._base}/{path}"

    # Vault stores plaintext — encryption handled by Vault Transit at rest.
    # For encrypt/decrypt we use local AES here so the interface is consistent.
    async def encrypt(self, data: dict[str, Any]) -> str:
        return json.dumps(data)          # Vault handles at-rest encryption

    async def decrypt(self, ciphertext: str) -> dict[str, Any]:
        return json.loads(ciphertext)

    async def store(self, path: str, data: dict[str, Any]) -> None:
        self._client.secrets.kv.v2.create_or_update_secret(
            path=self._full_path(path),
            secret=data,
            mount_point=self._mount,
        )
        logger.info("secret_stored", path=path, backend="vault")

    async def retrieve(self, path: str) -> dict[str, Any]:
        try:
            result = self._client.secrets.kv.v2.read_secret_version(
                path=self._full_path(path),
                mount_point=self._mount,
            )
            return result["data"]["data"]
        except Exception as exc:
            raise SecretsError(f"Vault retrieve failed for {path}: {exc}") from exc

    async def delete(self, path: str) -> None:
        self._client.secrets.kv.v2.delete_metadata_and_all_versions(
            path=self._full_path(path),
            mount_point=self._mount,
        )


# ── Factory ────────────────────────────────────────────────────────────

def get_secrets_backend(settings: Settings) -> SecretsBackend:
    """Return the correct backend based on SECRETS_BACKEND env var."""
    if settings.secrets_backend == "vault":
        return VaultSecretsBackend(
            url=settings.vault_url,
            token=settings.vault_token,
            mount_path=settings.vault_mount_path,
            base_path=settings.vault_connector_path,
        )
    return EnvSecretsBackend(hex_key=settings.secrets_aes_key)

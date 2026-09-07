"""Unit tests for the secrets abstraction layer."""

from __future__ import annotations

import pytest

from cafm_connector.secrets.backend import EnvSecretsBackend
from cafm_connector.core.exceptions import SecretsError


VALID_KEY = "0" * 64   # 32 zero bytes as hex


@pytest.mark.asyncio
async def test_encrypt_decrypt_roundtrip():
    backend = EnvSecretsBackend(hex_key=VALID_KEY)
    data = {"password": "s3cr3t", "token": "abc123"}
    ciphertext = await backend.encrypt(data)
    assert isinstance(ciphertext, str)
    assert ciphertext != str(data)
    recovered = await backend.decrypt(ciphertext)
    assert recovered == data


@pytest.mark.asyncio
async def test_store_and_retrieve():
    backend = EnvSecretsBackend(hex_key=VALID_KEY)
    creds = {"username": "admin", "password": "hunter2"}
    await backend.store("connector-abc", creds)
    retrieved = await backend.retrieve("connector-abc")
    assert retrieved == creds


@pytest.mark.asyncio
async def test_retrieve_missing_raises():
    backend = EnvSecretsBackend(hex_key=VALID_KEY)
    with pytest.raises(SecretsError):
        await backend.retrieve("does-not-exist")


@pytest.mark.asyncio
async def test_delete():
    backend = EnvSecretsBackend(hex_key=VALID_KEY)
    await backend.store("temp", {"k": "v"})
    await backend.delete("temp")
    with pytest.raises(SecretsError):
        await backend.retrieve("temp")


def test_invalid_key_length():
    backend = EnvSecretsBackend(hex_key="tooshort")
    import pytest
    with pytest.raises(SecretsError):
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            backend.encrypt({"x": "y"})
        )

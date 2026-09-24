"""Secret stores. API keys live in the OS keyring locally, and encrypted in Firestore when it is active (P6)."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

SERVICE_NAME = "aidriven"

# Environment fallback per provider kind, used when no key was saved in the keyring.
ENV_FALLBACK = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
}


class KeyringSecretStore:
    """OS keyring (Windows Credential Manager, macOS Keychain, Secret Service)."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        import keyring

        self._keyring = keyring
        self._service = service

    def get(self, key: str) -> str | None:
        try:
            return self._keyring.get_password(self._service, key)
        except Exception as exc:
            log.warning("keyring read failed for %s: %s", key, exc)
            return None

    def set(self, key: str, value: str) -> None:
        self._keyring.set_password(self._service, key, value)

    def delete(self, key: str) -> None:
        try:
            self._keyring.delete_password(self._service, key)
        except Exception:
            pass


class MemorySecretStore:
    """In-memory store for tests."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)


SECRET_PREFIX = "secret__"


def derive_fernet_key(material: str | bytes) -> bytes:
    """Fernet key (urlsafe base64 of 32 bytes) derived with HKDF-SHA256 from secret material."""
    import base64

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    raw = material.encode() if isinstance(material, str) else material
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"aidriven-secrets-v1", info=b"api-keys").derive(raw)
    return base64.urlsafe_b64encode(key)


class EncryptedRepoSecretStore:
    """API keys encrypted (Fernet/AES) inside the document repository (`aidriven_settings`, doc `secret__<key>`).

    Used when Firestore is active so keys persist with the data (ADR-022). Only ciphertext is stored; the key
    is derived from the service account's private key (or AIDRIVEN_ENCRYPTION_KEY), so reading Firestore
    alone does not reveal them. Writes are mirrored to a local cache store (the OS keyring).
    """

    def __init__(self, repo: object, fernet_key: bytes, cache: object | None = None) -> None:
        from cryptography.fernet import Fernet

        self._repo = repo
        self._fernet = Fernet(fernet_key)
        self._cache = cache

    @staticmethod
    def _doc_id(key: str) -> str:
        return SECRET_PREFIX + key.replace("/", "_")

    def get(self, key: str) -> str | None:
        from cryptography.fernet import InvalidToken

        doc = self._repo.get_setting(self._doc_id(key))  # type: ignore[attr-defined]
        if doc and doc.get("value_enc"):
            try:
                value: str = self._fernet.decrypt(doc["value_enc"].encode()).decode()
                return value
            except InvalidToken:
                log.error("cannot decrypt secret %s: wrong key (AIDRIVEN_ENCRYPTION_KEY or service account)", key)
        return self._cache.get(key) if self._cache is not None else None  # type: ignore[attr-defined]

    def set(self, key: str, value: str) -> None:
        token = self._fernet.encrypt(value.encode()).decode()
        self._repo.set_setting(self._doc_id(key), {"value_enc": token, "algo": "fernet-hkdf-sha256"})  # type: ignore[attr-defined]
        if self._cache is not None:
            try:
                self._cache.set(key, value)  # type: ignore[attr-defined]
            except Exception as exc:
                log.warning("local secret cache write failed for %s: %s", key, exc)

    def delete(self, key: str) -> None:
        self._repo.delete_setting(self._doc_id(key))  # type: ignore[attr-defined]
        if self._cache is not None:
            self._cache.delete(key)  # type: ignore[attr-defined]


def mask(secret: str | None) -> str:
    if not secret:
        return ""
    return "••••••••" + secret[-4:] if len(secret) > 8 else "••••"


def resolve_api_key(store: object, secret_key: str, provider_kind: str) -> tuple[str | None, str | None]:
    """Return (api_key, source) where source is 'saved', 'env' or None."""
    saved = store.get(secret_key)  # type: ignore[attr-defined]
    if saved:
        return saved, "saved"
    env_name = ENV_FALLBACK.get(provider_kind)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name], "env"
    return None, None

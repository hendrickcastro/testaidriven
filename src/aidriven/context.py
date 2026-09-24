"""Composition root: builds adapters and services from configuration and hands them to the UI."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from aidriven.adapters.artifact_stores import FirebaseStorageArtifactStore, LocalArtifactStore
from aidriven.adapters.llm import BaseAdapter, build_adapter
from aidriven.adapters.repo_sqlite import SqliteRepository
from aidriven.adapters.sandbox import SandboxManager
from aidriven.adapters.secrets import (
    EncryptedRepoSecretStore,
    KeyringSecretStore,
    MemorySecretStore,
    derive_fernet_key,
    resolve_api_key,
)
from aidriven.config import AppConfig, ConfigStore, EnvSettings
from aidriven.domain.models import (
    ENTITY_TYPES,
    ModelProfile,
    ProviderConnection,
    utcnow,
)
from aidriven.ports import RepositoryPort
from aidriven.services.checks import CheckEngine
from aidriven.services.runner import RunnerService

log = logging.getLogger(__name__)


class AppContext:
    def __init__(self, env: EnvSettings, *, secrets: Any = None, repo: RepositoryPort | None = None) -> None:
        self.env = env
        env.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_store = ConfigStore(env.config_path)
        # Local store (OS keyring); when Firestore is active an encrypted Firestore store wraps it (ADR-022).
        self.local_secrets = secrets if secrets is not None else self._default_secrets()
        self._cloud_secrets: EncryptedRepoSecretStore | None = None
        self.sqlite = SqliteRepository(env.sqlite_path) if repo is None else None
        self._repo: RepositoryPort = repo or self.sqlite  # type: ignore[assignment]
        self._firebase_app: Any = None
        self._adapters: dict[str, tuple[str, BaseAdapter]] = {}
        self.local_store = LocalArtifactStore(env.artifacts_dir)
        self._cloud_store: FirebaseStorageArtifactStore | None = None
        self.sandbox = SandboxManager(self.config.sandbox)
        self.check_engine = CheckEngine(self.sandbox)
        self.runner = RunnerService(self)
        if repo is None and self.config.firestore.enabled:
            try:
                self.activate_firestore()
            except Exception as exc:
                log.error("Firestore is enabled but could not be activated, using local SQLite: %s", exc)

    @staticmethod
    def _default_secrets() -> Any:
        try:
            return KeyringSecretStore()
        except Exception as exc:  # no keyring backend available
            log.warning("OS keyring unavailable (%s); API keys will only live in memory", exc)
            return MemorySecretStore()

    @property
    def secrets(self) -> Any:
        return self._cloud_secrets if self._cloud_secrets is not None else self.local_secrets

    def _secret_key_material(self) -> str:
        if self.env.encryption_key:
            return self.env.encryption_key
        path = self.config.firestore.credentials_path
        if not path:
            raise RuntimeError("no service account to derive the secrets key from")
        material: str = json.loads(Path(path).read_text(encoding="utf-8"))["private_key"]
        return material

    def _enable_cloud_secrets(self) -> int:
        """Encrypted secrets in Firestore; upload keys that only exist locally. Returns how many were uploaded."""
        self._cloud_secrets = EncryptedRepoSecretStore(
            self._repo, derive_fernet_key(self._secret_key_material()), cache=self.local_secrets
        )
        connections = {c.id: c for c in self._repo.find(ProviderConnection)}
        if self.sqlite is not None:
            connections.update({c.id: c for c in self.sqlite.find(ProviderConnection)})
        uploaded = 0
        for conn in connections.values():
            local = self.local_secrets.get(conn.secret_key)
            doc_id = EncryptedRepoSecretStore._doc_id(conn.secret_key)
            if local and not self._repo.get_setting(doc_id):
                self._cloud_secrets.set(conn.secret_key, local)
                uploaded += 1
        if uploaded:
            log.info("uploaded %d API key(s) to Firestore (encrypted)", uploaded)
        self._adapters.clear()
        return uploaded

    # ------------------------------------------------------------------ config

    @property
    def config(self) -> AppConfig:
        return self.config_store.config

    def save_config(self) -> None:
        self.config_store.save()
        self.sandbox.config = self.config.sandbox
        self.sandbox.docker.config = self.config.sandbox

    # ------------------------------------------------------------------ persistence

    @property
    def repo(self) -> RepositoryPort:
        return self._repo

    @property
    def backend(self) -> str:
        return self._repo.backend

    def _firebase(self) -> Any:
        fs = self.config.firestore
        if not fs.credentials_path:
            raise RuntimeError("no Firestore credentials uploaded")
        if self._firebase_app is None:
            from aidriven.adapters.repo_firestore import init_firestore_app

            self._firebase_app = init_firestore_app(fs.credentials_path, fs.project_id)
        return self._firebase_app

    def install_credentials(self, content: bytes) -> dict[str, str]:
        """Validate an uploaded service-account JSON and copy it to data/secrets/ (gitignored)."""
        data = json.loads(content.decode("utf-8"))
        if data.get("type") != "service_account" or not data.get("project_id") or not data.get("private_key"):
            raise ValueError("not a Google service-account JSON (type/project_id/private_key missing)")
        self.env.secrets_dir.mkdir(parents=True, exist_ok=True)
        target = self.env.secrets_dir / "firestore-sa.json"
        target.write_bytes(content)
        fs = self.config.firestore
        fs.credentials_path = str(target.resolve())
        fs.project_id = data["project_id"]
        fs.last_test_ok = None
        self._reset_firebase()
        self.save_config()
        if not self.config.storage.bucket:
            self.config.storage.bucket = f"{data['project_id']}.firebasestorage.app"
            self.save_config()
        return {"project_id": data["project_id"], "client_email": data.get("client_email", "")}

    def _reset_firebase(self) -> None:
        if self._firebase_app is not None:
            import firebase_admin

            firebase_admin.delete_app(self._firebase_app)
        self._firebase_app = None
        self._cloud_store = None

    def test_firestore(self) -> tuple[bool, str]:
        from aidriven.adapters.repo_firestore import FirestoreRepository

        fs = self.config.firestore
        try:
            self._firebase()
            repo = FirestoreRepository(fs.credentials_path or "", fs.project_id, fs.database_id)
            detail = repo.health_check()
            ok = True
        except Exception as exc:
            log.error("Firestore connection test failed: %s", exc)
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        fs.last_test_ok = ok
        fs.last_test_at = utcnow().isoformat()
        fs.last_test_detail = detail[:1000]
        self.save_config()
        return ok, detail

    def activate_firestore(self) -> None:
        from aidriven.adapters.repo_firestore import FirestoreRepository

        fs = self.config.firestore
        self._firebase()
        self._repo = FirestoreRepository(fs.credentials_path or "", fs.project_id, fs.database_id)
        fs.enabled = True
        self.save_config()
        self._enable_cloud_secrets()
        log.info("persistence backend: Firestore (%s)", fs.project_id)

    def deactivate_firestore(self) -> None:
        if self.sqlite is None:
            self.sqlite = SqliteRepository(self.env.sqlite_path)
        self._repo = self.sqlite
        self._cloud_secrets = None
        self._adapters.clear()
        self.config.firestore.enabled = False
        self.save_config()
        log.info("persistence backend: local SQLite")

    def migrate_local_to_firestore(self) -> dict[str, int]:
        """Copy every local document to Firestore (upsert). Local data is kept."""
        if self._repo.backend != "firestore":
            raise RuntimeError("activate Firestore first")
        source = self.sqlite or SqliteRepository(self.env.sqlite_path)
        counts: dict[str, int] = {}
        for cls in ENTITY_TYPES:
            items = source.find(cls)
            self._repo.save_many(items)
            counts[cls.collection] = len(items)
        counts["api_keys"] = self._enable_cloud_secrets()
        log.info("migrated local data to Firestore: %s", counts)
        return counts

    def backup_local(self) -> Path:
        """Copy the SQLite database into data/backups/."""
        dest_dir = self.env.data_dir / "backups"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"aidriven-{datetime.now():%Y%m%d-%H%M%S}.db"
        shutil.copy2(self.env.sqlite_path, dest)
        return dest

    # ------------------------------------------------------------------ artifacts

    def artifact_store(self) -> Any:
        """Store for NEW artifacts according to the cloud toggle."""
        if self.config.storage.cloud_enabled:
            return self.artifact_store_for("firebase")
        return self.local_store

    def artifact_store_for(self, kind: str) -> Any:
        if kind == "local":
            return self.local_store
        if self._cloud_store is None:
            st = self.config.storage
            if not st.bucket:
                raise RuntimeError("no Firebase Storage bucket configured")
            self._cloud_store = FirebaseStorageArtifactStore(self._firebase(), st.bucket, st.prefix)
        return self._cloud_store

    def test_storage(self) -> tuple[bool, str]:
        try:
            self._cloud_store = None
            store = self.artifact_store_for("firebase")
            return True, store.health_check()
        except Exception as exc:
            log.error("Firebase Storage test failed: %s", exc)
            return False, f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ LLM

    def api_key_for(self, connection: ProviderConnection) -> tuple[str | None, str | None]:
        return resolve_api_key(self.secrets, connection.secret_key, connection.kind.value)

    def adapter_for_connection(self, connection: ProviderConnection) -> BaseAdapter:
        key, _ = self.api_key_for(connection)
        fingerprint = hashlib.sha256(
            (connection.model_dump_json(exclude={"created_at", "updated_at"}) + (key or "")).encode()
        ).hexdigest()
        cached = self._adapters.get(connection.id)
        if cached and cached[0] == fingerprint:
            return cached[1]
        adapter = build_adapter(connection, key)
        self._adapters[connection.id] = (fingerprint, adapter)
        return adapter

    def adapter_for(self, profile: ModelProfile) -> BaseAdapter:
        connection = self.repo.get(ProviderConnection, profile.provider_id)
        if connection is None:
            raise RuntimeError(f"provider {profile.provider_id} of model {profile.name} does not exist")
        if not connection.enabled:
            raise RuntimeError(f"provider {connection.name} is disabled")
        return self.adapter_for_connection(connection)

    def judge_profile(self) -> ModelProfile | None:
        j = self.config.judge
        if not j.enabled or not j.model_id:
            return None
        return self.repo.get(ModelProfile, j.model_id)

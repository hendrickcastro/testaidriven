"""Application configuration.

Two layers:
- `EnvSettings`: process-level settings from environment / `.env` (paths, host, port). Read once at startup.
- `AppConfig`: user-editable settings from the UI (language, Firestore, storage, judge, sandbox), persisted as
  `data/config.json`. It must live locally because it holds the Firestore connection itself.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

COLLECTION_PREFIX = "aidriven_"
BLOB_PREFIX = "aidriven/"


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIDRIVEN_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8731
    reload: bool = False
    storage_secret: str = "aidriven-local-ui"  # NiceGUI per-browser storage signing (local app)
    # Encrypts API keys stored in Firestore. Default: derived from the service account's private key.
    encryption_key: str | None = None

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "aidriven.db"


class FirestoreConfig(BaseModel):
    enabled: bool = False
    credentials_path: str | None = None  # copied service-account JSON under data/secrets/
    project_id: str | None = None
    database_id: str = "(default)"
    last_test_ok: bool | None = None
    last_test_at: str | None = None
    last_test_detail: str = ""


class StorageConfig(BaseModel):
    cloud_enabled: bool = False  # off by default: artifacts stay local
    bucket: str | None = None  # e.g. "<project>.firebasestorage.app"
    prefix: str = BLOB_PREFIX


class JudgeConfig(BaseModel):
    enabled: bool = True
    model_id: str | None = None  # ModelProfile id of the (stronger) judge model


class SandboxConfig(BaseModel):
    mode: Literal["auto", "docker", "process"] = "auto"
    python_image: str = "python:3.12-slim"
    # Built locally on first use from `browser_base_image` + the Python playwright package of the same version.
    playwright_image: str = "aidriven-browser:1.63.0"
    browser_base_image: str = "mcr.microsoft.com/playwright/python:v1.63.0-noble"
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 256
    default_timeout_s: float = 60.0


class AppConfig(BaseModel):
    language: Literal["en", "es"] = "en"
    theme: Literal["auto", "light", "dark"] = "auto"
    firestore: FirestoreConfig = Field(default_factory=FirestoreConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)


class ConfigStore:
    """Thread-safe load/save of `AppConfig` as JSON."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._config = self._load()

    def _load(self) -> AppConfig:
        if self._path.exists():
            return AppConfig.model_validate(json.loads(self._path.read_text(encoding="utf-8")))
        return AppConfig()

    @property
    def config(self) -> AppConfig:
        return self._config

    def save(self, config: AppConfig | None = None) -> None:
        with self._lock:
            if config is not None:
                self._config = config
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(self._config.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(self._path)

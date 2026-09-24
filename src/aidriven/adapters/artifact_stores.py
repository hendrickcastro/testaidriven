"""Artifact stores: local filesystem (default) and Firebase Storage (optional, toggle in Settings)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from aidriven.config import BLOB_PREFIX

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    """Flatten any path the model gave into a single safe file name (no traversal)."""
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".", "..")]
    cleaned = _SAFE.sub("_", "__".join(parts)).strip("._") or "artifact"
    return cleaned[:150]


class LocalArtifactStore:
    kind: Literal["local", "firebase"] = "local"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, run_id: str, result_id: str, name: str, content: bytes, mime: str) -> str:
        path = self.root / safe_name(run_id) / safe_name(result_id) / safe_name(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return str(path.relative_to(self.root)).replace("\\", "/")

    def _resolve(self, location: str) -> Path:
        path = (self.root / location).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact location escapes the store root")
        return path

    def get(self, location: str) -> bytes:
        return self._resolve(location).read_bytes()

    def delete(self, location: str) -> None:
        self._resolve(location).unlink(missing_ok=True)


class FirebaseStorageArtifactStore:
    kind: Literal["local", "firebase"] = "firebase"

    def __init__(self, app: Any, bucket: str, prefix: str = BLOB_PREFIX) -> None:
        from firebase_admin import storage

        self._bucket = storage.bucket(bucket, app=app)
        self._prefix = prefix if prefix.endswith("/") else prefix + "/"

    def put(self, run_id: str, result_id: str, name: str, content: bytes, mime: str) -> str:
        blob_path = f"{self._prefix}{safe_name(run_id)}/{safe_name(result_id)}/{safe_name(name)}"
        self._bucket.blob(blob_path).upload_from_string(content, content_type=mime)
        return blob_path

    def get(self, location: str) -> bytes:
        data: bytes = self._bucket.blob(location).download_as_bytes()
        return data

    def delete(self, location: str) -> None:
        self._bucket.blob(location).delete()

    def health_check(self) -> str:
        probe = f"{self._prefix}_health/probe.txt"
        blob = self._bucket.blob(probe)
        blob.upload_from_string(b"ok", content_type="text/plain")
        ok = blob.download_as_bytes() == b"ok"
        blob.delete()
        if not ok:
            raise RuntimeError("storage probe mismatch")
        return f"upload/download/delete OK on gs://{self._bucket.name}/{self._prefix}"

"""Firestore document repository (Admin SDK). Collections are `aidriven_<collection>` (constitution P1).

Queries use a single equality filter at most and sort in memory, so no composite index is required.
If Firestore still asks for one, the error (with its creation URL) is logged and shows up on the Logs page.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from aidriven.config import COLLECTION_PREFIX
from aidriven.domain.models import Entity, utcnow
from aidriven.ports import E

log = logging.getLogger(__name__)

MAX_DOC_BYTES = 900_000  # Firestore hard limit is 1 MiB per document
_TRUNCATABLE = ("response_text", "reasoning_text", "transcript")


NESTED_KEY = "aidriven_nested_json"  # Firestore reserves __names__


def encode_nested(value: Any) -> Any:
    """Firestore rejects arrays that directly contain arrays: store such lists as a JSON string wrapper."""
    if isinstance(value, dict):
        return {k: encode_nested(v) for k, v in value.items()}
    if isinstance(value, list):
        if any(isinstance(v, list) for v in value):
            return {NESTED_KEY: json.dumps(value)}
        return [encode_nested(v) for v in value]
    return value


def decode_nested(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {NESTED_KEY}:
            return json.loads(value[NESTED_KEY])
        return {k: decode_nested(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_nested(v) for v in value]
    return value


def init_firestore_app(credentials_path: str, project_id: str | None = None, app_name: str = "aidriven") -> Any:
    """Initialize (or reuse) a named firebase_admin app from a service-account JSON."""
    import firebase_admin
    from firebase_admin import credentials

    try:
        return firebase_admin.get_app(app_name)
    except ValueError:
        pass
    cred = credentials.Certificate(credentials_path)
    options: dict[str, Any] = {}
    if project_id:
        options["projectId"] = project_id
    return firebase_admin.initialize_app(cred, options, name=app_name)


def read_service_account(path: str | Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") != "service_account" or "private_key" not in data or "project_id" not in data:
        raise ValueError("not a Google service-account JSON (type/project_id/private_key missing)")
    return data


class FirestoreRepository:
    backend = "firestore"

    def __init__(self, credentials_path: str, project_id: str | None = None, database_id: str = "(default)") -> None:
        from firebase_admin import firestore

        self._app = init_firestore_app(credentials_path, project_id)
        if database_id and database_id != "(default)":
            self._db = firestore.client(self._app, database_id=database_id)
        else:
            self._db = firestore.client(self._app)

    def _col(self, cls: type[Entity]) -> Any:
        return self._db.collection(COLLECTION_PREFIX + cls.collection)

    @staticmethod
    def _to_doc(entity: Entity) -> dict[str, Any]:
        doc: dict[str, Any] = encode_nested(entity.model_dump(mode="json"))
        size = len(json.dumps(doc))
        if size > MAX_DOC_BYTES:
            for field in _TRUNCATABLE:
                if doc.get(field):
                    log.warning(
                        "document %s/%s too large (%d B): truncating %s",
                        entity.collection,
                        entity.id,
                        size,
                        field,
                    )
                    if isinstance(doc[field], str):
                        doc[field] = doc[field][: MAX_DOC_BYTES // 4] + "\n…[truncated for Firestore]"
                    else:
                        doc[field] = doc[field][-5:]
                    size = len(json.dumps(doc))
                    if size <= MAX_DOC_BYTES:
                        break
            if size > MAX_DOC_BYTES:
                raise ValueError(
                    f"{entity.collection}/{entity.id} is {size} bytes, above the Firestore document limit; "
                    "reduce inline attachments or the number/size of tasks in the run"
                )
        return doc

    def get(self, cls: type[E], entity_id: str) -> E | None:
        snap = self._col(cls).document(entity_id).get()
        return cls.model_validate(decode_nested(snap.to_dict())) if snap.exists else None

    def find(self, cls: type[E], *, where: dict[str, Any] | None = None, limit: int | None = None) -> list[E]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self._col(cls)
        for k, v in (where or {}).items():
            query = query.where(filter=FieldFilter(k, "==", v))
        try:
            docs = [cls.model_validate(decode_nested(d.to_dict())) for d in query.stream()]
        except Exception as exc:  # FailedPrecondition carries the index-creation URL
            log.error("Firestore query on %s%s failed: %s", COLLECTION_PREFIX, cls.collection, exc)
            raise
        docs.sort(key=lambda e: e.created_at, reverse=True)
        return docs[:limit] if limit else docs

    def save(self, entity: E) -> E:
        entity.updated_at = utcnow()
        self._col(type(entity)).document(entity.id).set(self._to_doc(entity))
        return entity

    def save_many(self, entities: list[E]) -> None:
        for i in range(0, len(entities), 400):
            batch = self._db.batch()
            for e in entities[i : i + 400]:
                e.updated_at = utcnow()
                batch.set(self._col(type(e)).document(e.id), self._to_doc(e))
            batch.commit()

    def delete(self, cls: type[E], entity_id: str) -> None:
        self._col(cls).document(entity_id).delete()

    def delete_where(self, cls: type[E], field_name: str, value: Any) -> int:
        from google.cloud.firestore_v1.base_query import FieldFilter

        n = 0
        for d in self._col(cls).where(filter=FieldFilter(field_name, "==", value)).stream():
            d.reference.delete()
            n += 1
        return n

    def get_setting(self, key: str) -> dict[str, Any] | None:
        snap = self._db.collection(COLLECTION_PREFIX + "settings").document(key).get()
        return snap.to_dict() if snap.exists else None

    def set_setting(self, key: str, value: dict[str, Any]) -> None:
        self._db.collection(COLLECTION_PREFIX + "settings").document(key).set(value)

    def delete_setting(self, key: str) -> None:
        self._db.collection(COLLECTION_PREFIX + "settings").document(key).delete()

    def list_settings(self, prefix: str) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for d in self._db.collection(COLLECTION_PREFIX + "settings").stream():
            if d.id.startswith(prefix):
                out[d.id] = d.to_dict() or {}
        return out

    def health_check(self) -> str:
        """Write, read back and delete a probe document in `aidriven_health`."""
        ref = self._db.collection(COLLECTION_PREFIX + "health").document(
            f"probe-{uuid.uuid4().hex[:8]}"
        )  # unique: concurrent tests
        stamp = utcnow().isoformat()
        ref.set({"at": stamp, "by": "aidriven"})
        got = ref.get().to_dict() or {}
        ref.delete()
        if got.get("at") != stamp:
            raise RuntimeError("probe read-back mismatch")
        return f"write/read/delete OK on {COLLECTION_PREFIX}health"

"""Local document repository on SQLite (default until Firestore is configured).

One table per collection with (id, data JSON, created_at). Filters use json_extract, so the same
single-field-equality query model as Firestore applies.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from aidriven.config import COLLECTION_PREFIX
from aidriven.domain.models import Entity, utcnow
from aidriven.ports import E


def table_name(cls: type[Entity]) -> str:
    return COLLECTION_PREFIX + cls.collection


class SqliteRepository:
    backend = "sqlite"

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=OFF")
        self._lock = threading.RLock()
        self._tables: set[str] = set()
        self._ensure_table(COLLECTION_PREFIX + "settings")

    def _ensure_table(self, name: str) -> None:
        if name in self._tables:
            return
        with self._lock:
            self._conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{name}" (id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT)'
            )
            self._conn.execute(f'CREATE INDEX IF NOT EXISTS "{name}_created" ON "{name}"(created_at)')
            self._tables.add(name)

    def get(self, cls: type[E], entity_id: str) -> E | None:
        t = table_name(cls)
        self._ensure_table(t)
        with self._lock:
            row = self._conn.execute(f'SELECT data FROM "{t}" WHERE id = ?', (entity_id,)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    def find(self, cls: type[E], *, where: dict[str, Any] | None = None, limit: int | None = None) -> list[E]:
        t = table_name(cls)
        self._ensure_table(t)
        sql = f'SELECT data FROM "{t}"'
        args: list[Any] = []
        if where:
            clauses = []
            for k, v in where.items():
                clauses.append("json_extract(data, ?) = ?")
                args.append("$." + k)
                args.append(json.dumps(v) if isinstance(v, (dict, list)) else (int(v) if isinstance(v, bool) else v))
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [cls.model_validate_json(r[0]) for r in rows]

    def save(self, entity: E) -> E:
        entity.updated_at = utcnow()
        t = table_name(type(entity))
        self._ensure_table(t)
        with self._lock:
            self._conn.execute(
                f'INSERT INTO "{t}"(id, data, created_at) VALUES(?,?,?) '
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (entity.id, entity.model_dump_json(), entity.created_at.isoformat()),
            )
        return entity

    def save_many(self, entities: list[E]) -> None:
        if not entities:
            return
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                for e in entities:
                    self.save(e)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def delete(self, cls: type[E], entity_id: str) -> None:
        t = table_name(cls)
        self._ensure_table(t)
        with self._lock:
            self._conn.execute(f'DELETE FROM "{t}" WHERE id = ?', (entity_id,))

    def delete_where(self, cls: type[E], field_name: str, value: Any) -> int:
        t = table_name(cls)
        self._ensure_table(t)
        with self._lock:
            cur = self._conn.execute(f'DELETE FROM "{t}" WHERE json_extract(data, ?) = ?', ("$." + field_name, value))
        return cur.rowcount

    def get_setting(self, key: str) -> dict[str, Any] | None:
        t = COLLECTION_PREFIX + "settings"
        with self._lock:
            row = self._conn.execute(f'SELECT data FROM "{t}" WHERE id = ?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_setting(self, key: str, value: dict[str, Any]) -> None:
        t = COLLECTION_PREFIX + "settings"
        with self._lock:
            self._conn.execute(
                f'INSERT INTO "{t}"(id, data, created_at) VALUES(?,?,?) '
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (key, json.dumps(value), utcnow().isoformat()),
            )

    def delete_setting(self, key: str) -> None:
        with self._lock:
            self._conn.execute(f'DELETE FROM "{COLLECTION_PREFIX}settings" WHERE id = ?', (key,))

    def list_settings(self, prefix: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f'SELECT id, data FROM "{COLLECTION_PREFIX}settings" WHERE id LIKE ?', (prefix + "%",)
            ).fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

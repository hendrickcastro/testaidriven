"""Task import/export (YAML/JSON) and the seed battery loader."""

from __future__ import annotations

import json
import logging
from importlib import resources
from typing import Any

import yaml

from aidriven.domain.models import Task, utcnow
from aidriven.ports import RepositoryPort

log = logging.getLogger(__name__)
_EXPORT_EXCLUDE = {"created_at", "updated_at"}


def seed_task_id(slug: str) -> str:
    return f"seed_{slug}"


def load_seed_tasks() -> list[Task]:
    folder = resources.files("aidriven") / "seed" / "tasks"
    tasks: list[Task] = []
    if not folder.is_dir():
        return tasks
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith((".yaml", ".yml")):
            continue
        data = yaml.safe_load(entry.read_text(encoding="utf-8"))
        data["id"] = seed_task_id(data["slug"])
        for i, check in enumerate(data.get("checks") or []):
            check.setdefault("id", f"{data['slug']}#{i}")  # stable across seed versions (comparisons)
        data["source"] = "seed"
        try:
            tasks.append(Task.model_validate(data))
        except Exception as exc:
            log.error("invalid seed task %s: %s", entry.name, exc)
    return tasks


def sync_seed_tasks(repo: RepositoryPort) -> int:
    """Insert seed tasks that are missing, or newer than the stored copy (user edits of seed tasks win
    unless the seed version is higher)."""
    changed = 0
    for task in load_seed_tasks():
        existing = repo.get(Task, task.id)
        if existing is None or existing.version < task.version:
            if existing is not None:
                task.created_at = existing.created_at
                task.enabled = existing.enabled
            repo.save(task)
            changed += 1
    return changed


def _dump(task: Task) -> dict[str, Any]:
    return task.model_dump(mode="json", by_alias=True, exclude=_EXPORT_EXCLUDE, exclude_defaults=False)


def export_tasks(tasks: list[Task], fmt: str = "yaml") -> str:
    data = [_dump(t) for t in tasks]
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def parse_tasks(text: str) -> list[Task]:
    """Parse YAML or JSON with one task or a list of tasks."""
    data = yaml.safe_load(text)  # YAML is a superset of JSON
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("expected a task object or a list of tasks")
    return [Task.model_validate(d) for d in data]


def import_tasks(repo: RepositoryPort, text: str) -> tuple[int, int]:
    """Upsert by slug: an existing slug gets a new version. Returns (created, updated)."""
    existing = {t.slug: t for t in repo.find(Task)}
    created = updated = 0
    for task in parse_tasks(text):
        prev = existing.get(task.slug)
        if prev is not None:
            task.id = prev.id
            task.version = prev.version + 1
            task.created_at = prev.created_at
            task.source = prev.source if prev.source != "seed" else "import"
            updated += 1
        else:
            task.source = "import"
            created += 1
        task.updated_at = utcnow()
        repo.save(task)
    return created, updated

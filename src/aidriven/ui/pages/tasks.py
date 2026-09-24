"""Tasks page: unlimited tasks, full editor (prompt, checks, rubric, attachments), import/export, quick run."""

from __future__ import annotations

import base64
from typing import Any

import yaml
from nicegui import ui

from aidriven.domain.models import ArtifactKind, Attachment, Check, ModelProfile, Task, utcnow
from aidriven.services.artifacts import extract_files
from aidriven.services.task_io import export_tasks, import_tasks
from aidriven.ui.common import action_slot, confirm, ctx, frame, notify_error, read_upload, root_dialog, section
from aidriven.ui.i18n import t

MAX_ATTACHMENT_BYTES = 512 * 1024
DIFFICULTIES = ["medium", "hard", "extreme"]
CHECK_TEMPLATE = """# One list item per check. kinds: exact, numeric, regex, json_schema, python_tests,
# python_verifier, sql_result, html_playwright. dimension: intelligence | conformity
- kind: exact
  name: final answer
  expected: "42"
"""

_filters: dict[str, Any] = {"q": "", "category": None, "source": None, "enabled": None}


def _tasks() -> list[Task]:
    return sorted(ctx().repo.find(Task), key=lambda x: (x.category, x.slug))


def _checks_yaml(checks: list[Check]) -> str:
    if not checks:
        return CHECK_TEMPLATE
    data = [c.model_dump(mode="json", by_alias=True, exclude_defaults=True) for c in checks]
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)


def _parse_checks(text: str) -> list[Check]:
    data = yaml.safe_load(text) or []
    if not isinstance(data, list):
        raise ValueError(t("tasks.checks_must_be_list"))
    return [Check.model_validate(d) for d in data]


def task_dialog(existing: Task | None, on_saved: Any) -> None:
    c = ctx()
    task = existing.model_copy(deep=True) if existing else Task(slug="", title="", category="reasoning", prompt="")
    attachments: list[Attachment] = list(task.attachments)
    with root_dialog() as dialog, ui.card().classes("w-[1100px] max-w-full"):
        ui.label(t("tasks.edit") if existing else t("tasks.add")).classes("text-h6")
        if task.source == "seed":
            ui.label(t("tasks.seed_note")).classes("text-caption text-warning")
        with ui.tabs().classes("w-full") as tabs:
            tab_gen = ui.tab(t("tasks.tab_general"))
            tab_prompt = ui.tab(t("tasks.tab_prompt"))
            tab_checks = ui.tab(t("tasks.tab_checks"))
            tab_judge = ui.tab(t("tasks.tab_judge"))
            tab_att = ui.tab(t("tasks.tab_attachments"))
        with ui.tab_panels(tabs, value=tab_gen).classes("w-full"):
            with ui.tab_panel(tab_gen):
                with ui.row().classes("w-full"):
                    slug = ui.input(t("tasks.slug"), value=task.slug).classes("flex-1")
                    title = ui.input(t("tasks.title"), value=task.title).classes("flex-[2]")
                with ui.row().classes("w-full"):
                    categories = sorted({x.category for x in _tasks()} | {task.category})
                    category = ui.select(
                        categories,
                        label=t("tasks.category"),
                        value=task.category,
                        with_input=True,
                        new_value_mode="add-unique",
                    ).classes("flex-1")
                    difficulty = ui.select(
                        {d: t(f"difficulty.{d}") for d in DIFFICULTIES},
                        label=t("tasks.difficulty"),
                        value=task.difficulty,
                    ).classes("flex-1")
                    requires = ui.select(
                        {"vision": t("cap.vision"), "tools": t("cap.tools"), "json_mode": t("cap.json_mode")},
                        label=t("tasks.requires"),
                        value=list(task.requires),
                        multiple=True,
                    ).classes("flex-1")
                with ui.row().classes("w-full"):
                    expected = ui.select(
                        [k.value for k in ArtifactKind],
                        label=t("tasks.expected_artifact"),
                        value=task.expected_artifact.value,
                    ).classes("flex-1")
                    names = ui.input(t("tasks.artifact_names"), value=", ".join(task.artifact_names)).classes(
                        "flex-[2]"
                    )
                tags = ui.input(t("common.tags"), value=", ".join(task.tags)).classes("w-full")
                enabled = ui.switch(t("common.enabled"), value=task.enabled)
            with ui.tab_panel(tab_prompt):
                prompt = ui.textarea(t("tasks.prompt"), value=task.prompt).classes("w-full font-mono").props("rows=22")
                ui.label(t("tasks.prompt_hint")).classes("text-caption text-grey-7")
            with ui.tab_panel(tab_checks):
                ui.label(t("tasks.checks_hint")).classes("text-caption text-grey-7")
                checks_text = ui.codemirror(_checks_yaml(task.checks), language="YAML").classes("w-full h-[420px]")
                with ui.expansion(t("tasks.try_checks"), icon="science").classes("w-full"):
                    sample = ui.textarea(t("tasks.sample_answer")).classes("w-full font-mono").props("rows=8")
                    out = ui.column().classes("w-full")

                    async def try_checks() -> None:
                        out.clear()
                        try:
                            checks = _parse_checks(checks_text.value)
                            names_list = [x.strip() for x in (names.value or "").split(",") if x.strip()]
                            files = {f.name: f.content for f in extract_files(sample.value or "", names_list)}
                            results = await c.check_engine.run_all(checks, sample.value or "", files)
                            with out:
                                ui.label(t("tasks.extracted", files=", ".join(files) or "—")).classes("text-caption")
                                for r in results:
                                    icon = "skip_next" if r.skipped else ("check_circle" if r.passed else "cancel")
                                    color = "grey" if r.skipped else ("positive" if r.passed else "negative")
                                    with ui.row().classes("items-start no-wrap"):
                                        ui.icon(icon).classes(f"text-{color}")
                                        ui.label(f"{r.name} — {r.score:.0%}").classes("font-medium")
                                    ui.code(r.detail or "", language="text").classes("w-full text-xs")
                        except Exception as exc:
                            notify_error(exc)

                    ui.button(t("tasks.run_checks"), icon="play_arrow", on_click=try_checks)
            with ui.tab_panel(tab_judge):
                rubric = ui.textarea(t("tasks.rubric"), value=task.rubric).classes("w-full").props("rows=10")
                reference = (
                    ui.textarea(t("tasks.reference"), value=task.reference_answer).classes("w-full").props("rows=10")
                )
                ui.label(t("tasks.reference_hint")).classes("text-caption text-grey-7")
            with ui.tab_panel(tab_att):
                att_list = ui.column().classes("w-full")

                def render_attachments() -> None:
                    att_list.clear()
                    with att_list:
                        if not attachments:
                            ui.label(t("tasks.no_attachments")).classes("text-grey-7")
                        for i, a in enumerate(attachments):
                            with ui.row().classes("items-center"):
                                ui.icon("image" if a.mime.startswith("image/") else "description")
                                ui.label(f"{a.name} · {a.mime} · {'seed:' + a.path if a.path else 'inline'}")
                                ui.button(
                                    icon="delete", on_click=lambda i=i: (attachments.pop(i), render_attachments())
                                ).props("flat dense color=negative")

                async def on_upload(e: Any) -> None:
                    try:
                        data = await read_upload(e)
                        if len(data) > MAX_ATTACHMENT_BYTES:
                            raise ValueError(t("tasks.attachment_too_big", kb=MAX_ATTACHMENT_BYTES // 1024))
                        mime = getattr(e.file, "content_type", None) or "application/octet-stream"
                        name = getattr(e.file, "name", "attachment")
                        is_text = mime.startswith("text/") or name.endswith((".txt", ".md", ".csv", ".json"))
                        attachments.append(
                            Attachment(
                                name=name,
                                mime=mime if not is_text or mime.startswith("text/") else "text/plain",
                                data_b64=base64.b64encode(data).decode(),
                                inline_text=is_text,
                            )
                        )
                        render_attachments()
                    except Exception as exc:
                        notify_error(exc)

                ui.upload(on_upload=on_upload, auto_upload=True, multiple=True, label=t("tasks.upload_attachment"))
                render_attachments()

        def save() -> None:
            try:
                new = task.model_copy(
                    update={
                        "slug": (slug.value or "").strip(),
                        "title": (title.value or "").strip(),
                        "category": category.value or "general",
                        "difficulty": difficulty.value,
                        "requires": list(requires.value or []),
                        "expected_artifact": ArtifactKind(expected.value),
                        "artifact_names": [x.strip() for x in (names.value or "").split(",") if x.strip()],
                        "tags": [x.strip() for x in (tags.value or "").split(",") if x.strip()],
                        "enabled": bool(enabled.value),
                        "prompt": prompt.value or "",
                        "checks": _parse_checks(checks_text.value),
                        "rubric": rubric.value or "",
                        "reference_answer": reference.value or "",
                        "attachments": attachments,
                    }
                )
                new = Task.model_validate(new.model_dump())
                if not new.slug or not new.title or not new.prompt.strip():
                    raise ValueError(t("tasks.required"))
                clash = next((x for x in _tasks() if x.slug == new.slug and x.id != new.id), None)
                if clash:
                    raise ValueError(t("tasks.slug_taken", slug=new.slug))
                if existing:
                    new.version = existing.version + 1
                    new.updated_at = utcnow()
                c.repo.save(new)
                dialog.close()
                ui.notify(t("common.saved"), type="positive")
                on_saved()
            except Exception as exc:
                notify_error(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("common.save"), on_click=save, icon="save")
    dialog.open()


def quick_run_dialog(tasks: list[Task]) -> None:
    """Run one task (or a selection) against chosen models without creating a suite."""
    c = ctx()
    profiles = sorted(c.repo.find(ModelProfile), key=lambda p: p.name)
    with root_dialog() as dialog, ui.card().classes("w-[560px] max-w-full"):
        ui.label(t("tasks.quick_run_title", n=len(tasks))).classes("text-h6")
        ui.label(", ".join(x.slug for x in tasks)).classes("text-caption text-grey-7")
        models = (
            ui.select({p.id: p.name for p in profiles}, label=t("suites.models"), multiple=True)
            .classes("w-full")
            .props("use-chips")
        )
        reps = ui.number(t("suites.repetitions"), value=1, min=1, max=50).classes("w-full")
        judge = ui.switch(t("suites.judge_enabled"), value=c.config.judge.enabled and bool(c.config.judge.model_id))

        def go() -> None:
            try:
                run = c.runner.create_run(
                    name=t("tasks.quick_run_name", slug=tasks[0].slug if len(tasks) == 1 else f"{len(tasks)} tasks"),
                    task_ids=[x.id for x in tasks],
                    model_ids=list(models.value or []),
                    repetitions=int(reps.value or 1),
                    judge_enabled=bool(judge.value),
                )
                c.runner.start(run.id)
                dialog.close()
                ui.navigate.to(f"/runs/{run.id}")
            except Exception as exc:
                notify_error(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("runs.start"), on_click=go, icon="play_arrow")
    dialog.open()


def import_dialog(on_saved: Any) -> None:
    c = ctx()
    with root_dialog() as dialog, ui.card().classes("w-[800px] max-w-full"):
        ui.label(t("tasks.import")).classes("text-h6")
        ui.label(t("tasks.import_hint")).classes("text-caption text-grey-7")
        text = ui.codemirror("", language="YAML").classes("w-full h-[360px]")

        async def on_upload(e: Any) -> None:
            text.value = (await read_upload(e)).decode("utf-8")

        ui.upload(on_upload=on_upload, auto_upload=True, label=t("tasks.upload_file")).props("accept=.yaml,.yml,.json")

        def do_import() -> None:
            try:
                created, updated = import_tasks(c.repo, text.value or "")
                ui.notify(t("tasks.imported", created=created, updated=updated), type="positive")
                dialog.close()
                on_saved()
            except Exception as exc:
                notify_error(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("common.import"), on_click=do_import, icon="upload")
    dialog.open()


@ui.refreshable
def tasks_table() -> None:
    c = ctx()
    tasks = _tasks()
    q = (_filters["q"] or "").lower()
    rows = []
    for x in tasks:
        if q and q not in f"{x.slug} {x.title} {' '.join(x.tags)}".lower():
            continue
        if _filters["category"] and x.category != _filters["category"]:
            continue
        if _filters["source"] and x.source != _filters["source"]:
            continue
        rows.append(
            {
                "id": x.id,
                "slug": x.slug,
                "title": x.title,
                "category": x.category,
                "difficulty": t(f"difficulty.{x.difficulty}"),
                "checks": len(x.checks),
                "requires": ", ".join(x.requires) or "—",
                "source": t(f"source.{x.source}"),
                "version": x.version,
                "enabled": "✔" if x.enabled else "—",
            }
        )
    columns: list[dict[str, Any]] = [
        {"name": "actions", "label": "", "field": "id"},
        {"name": "slug", "label": t("tasks.slug"), "field": "slug", "align": "left", "sortable": True},
        {"name": "title", "label": t("tasks.title"), "field": "title", "align": "left"},
        {"name": "category", "label": t("tasks.category"), "field": "category", "sortable": True},
        {"name": "difficulty", "label": t("tasks.difficulty"), "field": "difficulty", "sortable": True},
        {"name": "checks", "label": t("tasks.checks"), "field": "checks"},
        {"name": "requires", "label": t("tasks.requires"), "field": "requires"},
        {"name": "source", "label": t("tasks.source"), "field": "source"},
        {"name": "version", "label": "v", "field": "version"},
        {"name": "enabled", "label": t("common.enabled"), "field": "enabled"},
    ]
    table = ui.table(columns=columns, rows=rows, row_key="id", selection="multiple", pagination=50).classes("w-full")
    table.add_slot(
        "body-cell-actions",
        action_slot(
            [
                ("play_arrow", "run", t("tasks.quick_run")),
                ("edit", "edit", t("common.edit")),
                ("toggle_on", "toggle", t("tasks.toggle")),
                ("delete", "delete", t("common.delete")),
            ]
        ),
    )

    def get(row: dict[str, Any]) -> Task | None:
        return c.repo.get(Task, row["id"])

    table.on("edit", lambda e: task_dialog(get(e.args), tasks_table.refresh))

    def on_run(e: Any) -> None:
        task = get(e.args)
        if task:
            quick_run_dialog([task])

    def on_toggle(e: Any) -> None:
        task = get(e.args)
        if task:
            task.enabled = not task.enabled
            c.repo.save(task)
            tasks_table.refresh()

    def on_delete(e: Any) -> None:
        task = get(e.args)
        if task:
            msg = (
                t("tasks.confirm_delete_seed", slug=task.slug)
                if task.source == "seed"
                else t("tasks.confirm_delete", slug=task.slug)
            )
            confirm(msg, lambda: (c.repo.delete(Task, task.id), tasks_table.refresh()))

    table.on("run", on_run)
    table.on("toggle", on_toggle)
    table.on("delete", on_delete)

    with ui.row():

        def selected() -> list[Task]:
            ids = [r["id"] for r in table.selected]
            return [x for x in tasks if x.id in ids]

        def run_selected() -> None:
            sel = selected()
            if not sel:
                ui.notify(t("tasks.select_first"), type="warning")
                return
            quick_run_dialog(sel)

        def export(fmt: str) -> None:
            sel = selected() or tasks
            ext = "json" if fmt == "json" else "yaml"
            ui.download.content(export_tasks(sel, fmt), f"aidriven-tasks.{ext}")

        ui.button(t("tasks.run_selected"), icon="play_arrow", on_click=run_selected).props("flat")
        ui.button(t("tasks.export_yaml"), icon="download", on_click=lambda: export("yaml")).props("flat")
        ui.button(t("tasks.export_json"), icon="data_object", on_click=lambda: export("json")).props("flat")


def page() -> None:
    with frame("nav.tasks"), section(t("tasks.title_section"), t("tasks.caption")):
        tasks = _tasks()
        with ui.row().classes("w-full items-end"):
            ui.button(t("tasks.add"), icon="add", on_click=lambda: task_dialog(None, tasks_table.refresh))
            ui.button(t("tasks.import"), icon="upload", on_click=lambda: import_dialog(tasks_table.refresh)).props(
                "flat"
            )
            ui.space()
            ui.input(t("common.search"), on_change=lambda e: (_filters.update(q=e.value), tasks_table.refresh())).props(
                "dense clearable"
            )
            ui.select(
                sorted({x.category for x in tasks}),
                label=t("tasks.category"),
                clearable=True,
                on_change=lambda e: (_filters.update(category=e.value), tasks_table.refresh()),
            ).props("dense").classes("w-40")
            ui.select(
                {s: t(f"source.{s}") for s in ("seed", "user", "import")},
                label=t("tasks.source"),
                clearable=True,
                on_change=lambda e: (_filters.update(source=e.value), tasks_table.refresh()),
            ).props("dense").classes("w-32")
        tasks_table()


def task_preview(task: Task) -> None:
    """Read-only task view used by other pages."""
    ui.markdown(f"**{task.title}** · `{task.slug}` · {task.category} · {t(f'difficulty.{task.difficulty}')}")
    ui.code(task.prompt, language="markdown").classes("w-full max-h-80 overflow-auto")

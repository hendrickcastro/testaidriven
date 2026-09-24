"""Suites (batteries): tasks × models × rules/agent × repetitions. Start a run from here."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from aidriven.domain.models import AgentDef, ModelProfile, Ruleset, Suite, Task
from aidriven.ui.common import action_slot, confirm, ctx, frame, notify_error, root_dialog, section
from aidriven.ui.i18n import t


def suite_dialog(existing: Suite | None, on_saved: Any) -> None:
    c = ctx()
    suite = existing.model_copy(deep=True) if existing else Suite(name="")
    tasks = sorted(c.repo.find(Task), key=lambda x: (x.category, x.slug))
    profiles = sorted(c.repo.find(ModelProfile), key=lambda p: p.name)
    rulesets = sorted(c.repo.find(Ruleset), key=lambda r: r.name)
    agents = sorted(c.repo.find(AgentDef), key=lambda a: a.name)
    with root_dialog() as dialog, ui.card().classes("w-[900px] max-w-full"):
        ui.label(t("suites.edit") if existing else t("suites.add")).classes("text-h6")
        name = ui.input(t("common.name"), value=suite.name).classes("w-full")
        desc = ui.input(t("common.description"), value=suite.description).classes("w-full")
        task_opts = {x.id: f"[{x.category}] {x.slug}{'' if x.enabled else ' (off)'}" for x in tasks}
        task_sel = (
            ui.select(
                task_opts,
                label=t("suites.tasks"),
                value=[i for i in suite.task_ids if i in task_opts],
                multiple=True,
                with_input=True,
            )
            .classes("w-full")
            .props("use-chips")
        )
        with ui.row().classes("items-center gap-1"):
            ui.label(t("suites.add_by")).classes("text-caption")
            ui.button(
                t("suites.all_enabled"), on_click=lambda: task_sel.set_value([x.id for x in tasks if x.enabled])
            ).props("flat dense size=sm")
            for cat in sorted({x.category for x in tasks}):
                ui.button(
                    cat,
                    on_click=lambda cat=cat: task_sel.set_value(
                        sorted(set(task_sel.value or []) | {x.id for x in tasks if x.category == cat})
                    ),
                ).props("flat dense size=sm")
            ui.button(t("suites.clear"), on_click=lambda: task_sel.set_value([])).props(
                "flat dense size=sm color=negative"
            )
        models = (
            ui.select({p.id: p.name for p in profiles}, label=t("suites.models"), value=suite.model_ids, multiple=True)
            .classes("w-full")
            .props("use-chips")
        )
        rules = (
            ui.select(
                {r.id: r.name for r in rulesets}, label=t("suites.rulesets"), value=suite.ruleset_ids, multiple=True
            )
            .classes("w-full")
            .props("use-chips")
        )
        agent = ui.select(
            {a.id: a.name for a in agents}, label=t("suites.agent"), value=suite.agent_id, clearable=True
        ).classes("w-full")
        with ui.row().classes("w-full"):
            reps = ui.number(t("suites.repetitions"), value=suite.repetitions, min=1, max=50).classes("flex-1")
            conc = ui.number(t("suites.concurrency"), value=suite.concurrency, min=1, max=64).classes("flex-1")
            timeout = ui.number(t("suites.timeout"), value=suite.task_timeout_s, min=10, max=7200).classes("flex-1")
        judge = ui.switch(t("suites.judge_enabled"), value=suite.judge_enabled)
        estimate = ui.label().classes("text-caption text-grey-7")

        def update_estimate() -> None:
            n = len(task_sel.value or []) * len(models.value or []) * int(reps.value or 1)
            estimate.text = t("suites.estimate", n=n)

        for el in (task_sel, models, reps):
            el.on_value_change(lambda _: update_estimate())
        update_estimate()

        def collect() -> Suite:
            return suite.model_copy(
                update={
                    "name": (name.value or "").strip(),
                    "description": desc.value or "",
                    "task_ids": list(task_sel.value or []),
                    "model_ids": list(models.value or []),
                    "ruleset_ids": list(rules.value or []),
                    "agent_id": agent.value or None,
                    "repetitions": int(reps.value or 1),
                    "concurrency": int(conc.value or 4),
                    "task_timeout_s": float(timeout.value or 900),
                    "judge_enabled": bool(judge.value),
                }
            )

        def save() -> None:
            try:
                new = collect()
                if not new.name:
                    raise ValueError(t("common.name_required"))
                c.repo.save(new)
                dialog.close()
                on_saved()
            except Exception as exc:
                notify_error(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("common.save"), on_click=save, icon="save")
    dialog.open()


def start_suite(suite: Suite) -> None:
    c = ctx()
    try:
        run = c.runner.create_from_suite(suite)
        c.runner.start(run.id)
        ui.navigate.to(f"/runs/{run.id}")
    except Exception as exc:
        notify_error(exc)


@ui.refreshable
def suites_table() -> None:
    c = ctx()
    agents = {a.id: a.name for a in c.repo.find(AgentDef)}
    rows = [
        {
            "id": s.id,
            "name": s.name,
            "tasks": len(s.task_ids),
            "models": len(s.model_ids),
            "reps": s.repetitions,
            "items": len(s.task_ids) * len(s.model_ids) * s.repetitions,
            "agent": agents.get(s.agent_id or "", "—"),
            "judge": "✔" if s.judge_enabled else "—",
        }
        for s in sorted(c.repo.find(Suite), key=lambda s: s.name)
    ]
    columns = [
        {"name": "actions", "label": "", "field": "id"},
        {"name": "name", "label": t("common.name"), "field": "name", "align": "left"},
        {"name": "tasks", "label": t("suites.tasks"), "field": "tasks"},
        {"name": "models", "label": t("suites.models"), "field": "models"},
        {"name": "reps", "label": t("suites.repetitions"), "field": "reps"},
        {"name": "items", "label": t("suites.items"), "field": "items"},
        {"name": "agent", "label": t("suites.agent"), "field": "agent"},
        {"name": "judge", "label": t("suites.judge"), "field": "judge"},
    ]
    table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
    table.add_slot(
        "body-cell-actions",
        action_slot(
            [
                ("play_arrow", "run", t("suites.run")),
                ("edit", "edit", t("common.edit")),
                ("delete", "delete", t("common.delete")),
            ]
        ),
    )

    def get(row: dict[str, Any]) -> Suite | None:
        return c.repo.get(Suite, row["id"])

    table.on("run", lambda e: (s := get(e.args)) and start_suite(s))
    table.on("edit", lambda e: suite_dialog(get(e.args), suites_table.refresh))
    table.on(
        "delete",
        lambda e: confirm(
            t("common.confirm_delete", name=e.args["name"]),
            lambda: (c.repo.delete(Suite, e.args["id"]), suites_table.refresh()),
        ),
    )


def page() -> None:
    with frame("nav.suites"), section(t("suites.title"), t("suites.caption")):
        ui.button(t("suites.add"), icon="add", on_click=lambda: suite_dialog(None, suites_table.refresh))
        suites_table()

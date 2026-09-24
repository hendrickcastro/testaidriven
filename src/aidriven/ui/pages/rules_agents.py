"""Rules (system-prompt instructions) and agents (persona + sandbox tools) applied to batteries."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from aidriven.domain.models import AgentDef, AgentTool, Ruleset
from aidriven.ui.common import action_slot, confirm, ctx, frame, notify_error, root_dialog, section
from aidriven.ui.i18n import t


def ruleset_dialog(existing: Ruleset | None, on_saved: Any) -> None:
    rs = existing.model_copy(deep=True) if existing else Ruleset(name="", content="")
    with root_dialog() as dialog, ui.card().classes("w-[800px] max-w-full"):
        ui.label(t("rules.edit") if existing else t("rules.add")).classes("text-h6")
        name = ui.input(t("common.name"), value=rs.name).classes("w-full")
        desc = ui.input(t("common.description"), value=rs.description).classes("w-full")
        position = ui.select(
            {"prepend": t("rules.prepend"), "append": t("rules.append")}, label=t("rules.position"), value=rs.position
        ).classes("w-full")
        content = ui.textarea(t("rules.content"), value=rs.content).classes("w-full font-mono").props("rows=14")

        def save() -> None:
            try:
                if not (name.value or "").strip() or not (content.value or "").strip():
                    raise ValueError(t("rules.required"))
                new = rs.model_copy(
                    update={
                        "name": name.value.strip(),
                        "description": desc.value or "",
                        "position": position.value,
                        "content": content.value,
                    }
                )
                if existing:
                    new.version = existing.version + 1
                ctx().repo.save(new)
                dialog.close()
                on_saved()
            except Exception as exc:
                notify_error(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("common.save"), on_click=save, icon="save")
    dialog.open()


def agent_dialog(existing: AgentDef | None, on_saved: Any) -> None:
    ag = existing.model_copy(deep=True) if existing else AgentDef(name="", persona="")
    with root_dialog() as dialog, ui.card().classes("w-[800px] max-w-full"):
        ui.label(t("agents.edit") if existing else t("agents.add")).classes("text-h6")
        name = ui.input(t("common.name"), value=ag.name).classes("w-full")
        desc = ui.input(t("common.description"), value=ag.description).classes("w-full")
        persona = ui.textarea(t("agents.persona"), value=ag.persona).classes("w-full").props("rows=8")
        tools = (
            ui.select(
                {tool.value: t(f"tool.{tool.value}") for tool in AgentTool},
                label=t("agents.tools"),
                value=[x.value for x in ag.tools],
                multiple=True,
            )
            .classes("w-full")
            .props("use-chips")
        )
        with ui.row().classes("w-full"):
            turns = ui.number(t("agents.max_turns"), value=ag.max_turns, min=1, max=50).classes("flex-1")
            timeout = ui.number(t("agents.tool_timeout"), value=ag.tool_timeout_s, min=1, max=600).classes("flex-1")
        ui.label(t("agents.hint")).classes("text-caption text-grey-7")

        def save() -> None:
            try:
                if not (name.value or "").strip() or not (persona.value or "").strip():
                    raise ValueError(t("agents.required"))
                new = ag.model_copy(
                    update={
                        "name": name.value.strip(),
                        "description": desc.value or "",
                        "persona": persona.value,
                        "tools": [AgentTool(x) for x in tools.value or []],
                        "max_turns": int(turns.value or 8),
                        "tool_timeout_s": float(timeout.value or 30),
                    }
                )
                if existing:
                    new.version = existing.version + 1
                ctx().repo.save(new)
                dialog.close()
                on_saved()
            except Exception as exc:
                notify_error(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("common.save"), on_click=save, icon="save")
    dialog.open()


@ui.refreshable
def rules_table() -> None:
    c = ctx()
    rows = [
        {
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "position": t(f"rules.{r.position}"),
            "size": len(r.content),
            "version": r.version,
        }
        for r in sorted(c.repo.find(Ruleset), key=lambda r: r.name)
    ]
    columns = [
        {"name": "actions", "label": "", "field": "id"},
        {"name": "name", "label": t("common.name"), "field": "name", "align": "left"},
        {"name": "description", "label": t("common.description"), "field": "description", "align": "left"},
        {"name": "position", "label": t("rules.position"), "field": "position"},
        {"name": "size", "label": t("rules.chars"), "field": "size"},
        {"name": "version", "label": "v", "field": "version"},
    ]
    table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
    table.add_slot(
        "body-cell-actions", action_slot([("edit", "edit", t("common.edit")), ("delete", "delete", t("common.delete"))])
    )
    table.on("edit", lambda e: ruleset_dialog(c.repo.get(Ruleset, e.args["id"]), rules_table.refresh))
    table.on(
        "delete",
        lambda e: confirm(
            t("common.confirm_delete", name=e.args["name"]),
            lambda: (c.repo.delete(Ruleset, e.args["id"]), rules_table.refresh()),
        ),
    )


@ui.refreshable
def agents_table() -> None:
    c = ctx()
    rows = [
        {
            "id": a.id,
            "name": a.name,
            "description": a.description,
            "tools": ", ".join(t(f"tool.{x.value}") for x in a.tools),
            "turns": a.max_turns,
            "version": a.version,
        }
        for a in sorted(c.repo.find(AgentDef), key=lambda a: a.name)
    ]
    columns = [
        {"name": "actions", "label": "", "field": "id"},
        {"name": "name", "label": t("common.name"), "field": "name", "align": "left"},
        {"name": "description", "label": t("common.description"), "field": "description", "align": "left"},
        {"name": "tools", "label": t("agents.tools"), "field": "tools", "align": "left"},
        {"name": "turns", "label": t("agents.max_turns"), "field": "turns"},
        {"name": "version", "label": "v", "field": "version"},
    ]
    table = ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")
    table.add_slot(
        "body-cell-actions", action_slot([("edit", "edit", t("common.edit")), ("delete", "delete", t("common.delete"))])
    )
    table.on("edit", lambda e: agent_dialog(c.repo.get(AgentDef, e.args["id"]), agents_table.refresh))
    table.on(
        "delete",
        lambda e: confirm(
            t("common.confirm_delete", name=e.args["name"]),
            lambda: (c.repo.delete(AgentDef, e.args["id"]), agents_table.refresh()),
        ),
    )


def page() -> None:
    with frame("nav.rules"):
        with section(t("rules.title"), t("rules.caption")):
            ui.button(t("rules.add"), icon="add", on_click=lambda: ruleset_dialog(None, rules_table.refresh))
            rules_table()
        ui.separator()
        with section(t("agents.title"), t("agents.caption")):
            ui.button(t("agents.add"), icon="add", on_click=lambda: agent_dialog(None, agents_table.refresh))
            agents_table()

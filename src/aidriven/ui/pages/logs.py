"""Logs: live log viewer plus the list of Firestore index-creation URLs captured from errors."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from aidriven.logging_setup import CAPTURE
from aidriven.ui.common import fmt_dt, frame, section
from aidriven.ui.i18n import t

LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def page() -> None:
    state: dict[str, Any] = {"level": "INFO", "q": "", "auto": True}
    with frame("nav.logs"):
        with ui.card().classes("w-full"), section(t("logs.indexes"), t("logs.indexes_caption")):

            @ui.refreshable
            def hints() -> None:
                items = CAPTURE.hints()
                if not items:
                    ui.label(t("logs.no_indexes")).classes("text-grey-7")
                for h in items:
                    with ui.row().classes("w-full items-center no-wrap"):
                        ui.icon("warning", color="warning")
                        with ui.column().classes("flex-1 gap-0 min-w-0"):
                            ui.label(h.url).classes("font-mono text-xs break-all")
                            ui.label(t("logs.index_seen", count=h.count, last=fmt_dt(h.last_seen))).classes(
                                "text-caption"
                            )
                        ui.button(
                            icon="content_copy",
                            on_click=lambda h=h: (ui.clipboard.write(h.url), ui.notify(t("logs.copied"))),
                        ).props("flat dense")
                        ui.button(icon="open_in_new", on_click=lambda h=h: ui.navigate.to(h.url, new_tab=True)).props(
                            "flat dense"
                        )
                        ui.button(
                            icon="close", on_click=lambda h=h: (CAPTURE.dismiss_hint(h.url), hints.refresh())
                        ).props("flat dense")

            hints()
        with ui.card().classes("w-full"), section(t("logs.title")):
            with ui.row().classes("items-end w-full"):
                ui.select(
                    LEVELS,
                    label=t("logs.level"),
                    value="INFO",
                    on_change=lambda e: (state.update(level=e.value), entries.refresh()),
                ).classes("w-36")
                ui.input(
                    t("common.search"), on_change=lambda e: (state.update(q=e.value or ""), entries.refresh())
                ).props("clearable").classes("w-72")
                ui.switch(t("logs.auto_refresh"), value=True, on_change=lambda e: state.update(auto=e.value))
                ui.space()
                ui.button(
                    t("logs.clear"), icon="delete_sweep", on_click=lambda: (CAPTURE.clear(), entries.refresh())
                ).props("flat")

            @ui.refreshable
            def entries() -> None:
                min_level = LEVELS.index(state["level"])
                q = state["q"].lower()
                items = [
                    e
                    for e in CAPTURE.snapshot()
                    if LEVELS.index(e.level) >= min_level and (not q or q in e.message.lower() or q in e.logger.lower())
                ][-500:]
                lines = [f"{e.ts.astimezone():%H:%M:%S} {e.level:<7} {e.logger}: {e.message}" for e in reversed(items)]
                ui.code("\n".join(lines) or t("logs.empty"), language="text").classes(
                    "w-full text-xs max-h-[65vh] overflow-auto"
                )

            entries()

        def tick() -> None:
            if state["auto"]:
                entries.refresh()
                hints.refresh()

        ui.timer(3.0, tick)

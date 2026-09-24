"""Shared UI building blocks: page frame (header + navigation), formatting helpers, small widgets."""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from nicegui import app, ui

from aidriven.context import AppContext
from aidriven.ui.i18n import LANGUAGES, get_language, set_language, t

_CTX: dict[str, AppContext] = {}

# theme -> ui.dark_mode value (None = follow the OS)
THEMES: dict[str, bool | None] = {"auto": None, "light": False, "dark": True}
THEME_ORDER = ["auto", "light", "dark"]
THEME_ICONS = {"auto": "brightness_auto", "light": "light_mode", "dark": "dark_mode"}

NAV = [
    ("/", "dashboard", "nav.dashboard"),
    ("/models", "smart_toy", "nav.models"),
    ("/tasks", "assignment", "nav.tasks"),
    ("/rules", "rule", "nav.rules"),
    ("/suites", "playlist_play", "nav.suites"),
    ("/runs", "rocket_launch", "nav.runs"),
    ("/review", "rate_review", "nav.review"),
    ("/compare", "compare_arrows", "nav.compare"),
    ("/artifacts", "inventory_2", "nav.artifacts"),
    ("/settings", "settings", "nav.settings"),
    ("/logs", "receipt_long", "nav.logs"),
]

STATUS_COLORS = {
    "done": "positive",
    "completed": "positive",
    "running": "info",
    "pending": "grey",
    "error": "negative",
    "failed": "negative",
    "refused": "warning",
    "timeout": "warning",
    "skipped": "grey-6",
    "cancelled": "grey-7",
    "partial": "warning",
}


def set_context(ctx: AppContext) -> None:
    _CTX["ctx"] = ctx


def ctx() -> AppContext:
    return _CTX["ctx"]


def fmt_num(v: float | int | None, digits: int = 2) -> str:
    if v is None:
        return "—"
    if isinstance(v, int) or float(v).is_integer():
        return f"{int(v):,}"
    return f"{v:,.{digits}f}"


def fmt_ms(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v / 1000:.1f} s" if v >= 1000 else f"{v:.0f} ms"


def fmt_cost(v: float | None) -> str:
    if v is None:
        return "—"
    return f"${v:.4f}" if v < 1 else f"${v:,.2f}"


def fmt_dt(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return v.astimezone().strftime("%Y-%m-%d %H:%M")
    except AttributeError:
        return str(v)


def status_badge(status: str) -> ui.badge:
    return ui.badge(t(f"status.{status}"), color=STATUS_COLORS.get(status, "grey"))


def score_color(score: float | None) -> str:
    if score is None:
        return "grey"
    if score >= 8:
        return "positive"
    if score >= 5:
        return "warning"
    return "negative"


async def read_upload(event: Any) -> bytes:
    """NiceGUI 3 upload event → bytes (works whether `read` is sync or async)."""
    data = event.file.read()
    if inspect.isawaitable(data):
        data = await data
    return bytes(data)


def notify_error(exc: Exception) -> None:
    logging.getLogger("aidriven.ui").error("UI action failed", exc_info=exc)
    ui.notify(f"{type(exc).__name__}: {exc}", type="negative", multi_line=True, timeout=8000)


@contextmanager
def root_dialog() -> Iterator[ui.dialog]:
    """Dialog attached to the page root, so it survives refreshes of the (refreshable) element that opened it."""
    with ui.context.client.content:
        dialog = ui.dialog()
    with dialog:
        yield dialog


def confirm(message: str, on_yes: Callable[[], Any]) -> None:
    with root_dialog() as dialog, ui.card():
        ui.label(message)
        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")

            async def yes() -> None:
                dialog.close()
                res = on_yes()
                if inspect.isawaitable(res):
                    await res

            ui.button(t("common.confirm"), on_click=yes, color="negative")
    dialog.open()


@contextmanager
def frame(title_key: str) -> Iterator[None]:
    """Page chrome: header with backend/sandbox badges and language selector, left navigation."""
    context = ctx()
    lang = app.storage.user.get("lang") or context.config.language
    set_language(lang)
    ui.colors(primary="#3b5bdb", secondary="#495057", accent="#7048e8")
    theme = app.storage.user.get("theme") or context.config.theme
    dark = ui.dark_mode(THEMES[theme])
    ui.page_title(f"{t(title_key)} · aidriven")
    nav: dict[str, Any] = {}
    with ui.header().classes("items-center justify-between bg-primary"):
        with ui.row().classes("items-center gap-2"):
            ui.button(icon="menu", on_click=lambda: nav["drawer"].toggle()).props(  # noqa: PLW0108 (drawer made later)
                "flat color=white dense"
            )
            ui.label("aidriven").classes("text-h6 text-white")
            ui.label(t("app.tagline")).classes("text-caption text-white opacity-70 max-sm:hidden")
        with ui.row().classes("items-center gap-2"):
            backend = context.backend
            ui.badge(
                t("header.backend_firestore") if backend == "firestore" else t("header.backend_sqlite"),
                color="green-7" if backend == "firestore" else "blue-grey-8",
            ).props("text-color=white").tooltip(t("header.backend_tip"))
            status = context.sandbox.status()
            active = str(status["active"])
            if active == "docker" and not status["docker_available"]:
                ui.badge(t("header.sandbox_down"), color="negative").props("text-color=white").tooltip(
                    t("header.sandbox_down_tip")
                )
            else:
                ui.badge(
                    t("header.sandbox_docker") if active == "docker" else t("header.sandbox_process"),
                    color="green-7" if active == "docker" else "orange-8",
                ).props("text-color=white").tooltip(
                    t("header.sandbox_docker_tip") if active == "docker" else t("header.sandbox_process_tip")
                )

            def change_lang(e: Any) -> None:
                app.storage.user["lang"] = e.value
                context.config.language = e.value  # one setting: header and Settings stay in sync
                context.save_config()
                ui.navigate.reload()

            def cycle_theme() -> None:
                current = app.storage.user.get("theme") or context.config.theme
                nxt = THEME_ORDER[(THEME_ORDER.index(current) + 1) % len(THEME_ORDER)]
                app.storage.user["theme"] = nxt
                context.config.theme = nxt  # type: ignore[assignment]  # nxt is one of THEME_ORDER
                context.save_config()
                dark.set_value(THEMES[nxt])
                theme_btn.props(f"icon={THEME_ICONS[nxt]}")
                theme_btn.tooltip(t(f"theme.{nxt}"))

            theme_btn = ui.button(icon=THEME_ICONS[theme], on_click=cycle_theme).props("flat round dense color=white")
            theme_btn.tooltip(t(f"theme.{theme}"))

            ui.select(LANGUAGES, value=get_language(), on_change=change_lang).props(
                "dense dark options-dense borderless"
            ).classes("w-28 text-white")
    with ui.left_drawer(value=True, bordered=True).classes("bg-gray-50 dark:bg-neutral-900") as drawer:
        nav["drawer"] = drawer
        for path, icon, key in NAV:
            with (
                ui.link(target=path).classes("no-underline text-gray-800 dark:text-gray-200 w-full"),
                ui.row().classes(
                    "items-center gap-3 px-3 py-2 rounded hover:bg-gray-200 dark:hover:bg-neutral-800 w-full"
                ),
            ):
                ui.icon(icon).classes("text-primary")
                ui.label(t(key))
    with ui.column().classes("w-full max-w-screen-2xl mx-auto p-4 gap-4"):
        ui.label(t(title_key)).classes("text-h5 font-medium")
        yield


def section(title: str, caption: str | None = None) -> ui.column:
    col = ui.column().classes("w-full gap-2")
    with col:
        ui.label(title).classes("text-subtitle1 font-medium")
        if caption:
            ui.label(caption).classes("text-caption text-grey-7")
    return col


def kpi(label: str, value: str, icon: str = "insights", color: str = "primary") -> None:
    with ui.card().classes("min-w-[160px] flex-1"), ui.row().classes("items-center gap-3 no-wrap"):
        ui.icon(icon, size="md").classes(f"text-{color}")
        with ui.column().classes("gap-0"):
            ui.label(value).classes("text-h6")
            ui.label(label).classes("text-caption text-grey-7")


def sandboxed_iframe(html: str, height: str = "520px") -> ui.element:
    """Render model HTML isolated from the app: sandbox without allow-same-origin (no cookies, no parent access)."""
    frame_el = ui.element("iframe").classes("w-full border rounded bg-white").style(f"height:{height}")
    frame_el._props["sandbox"] = "allow-scripts"
    frame_el._props["referrerpolicy"] = "no-referrer"
    frame_el._props["srcdoc"] = html
    frame_el.update()
    return frame_el


def action_slot(buttons: list[tuple[str, str, str]]) -> str:
    """Quasar table cell with icon buttons emitting `event` with the row. buttons = [(icon, event, tooltip)]."""
    import html

    parts = []
    for icon, event, tip in buttons:
        color = ' color="negative"' if event == "delete" else ""
        parts.append(
            f'<q-btn flat dense icon="{icon}"{color} @click="$parent.$emit(\'{event}\', props.row)">'
            f"<q-tooltip>{html.escape(tip)}</q-tooltip></q-btn>"
        )
    return '<q-td :props="props" auto-width>' + "".join(parts) + "</q-td>"


_FENCE_INFO_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<lang>[\w+#.-]*)[ \t]+[^\n`]*?"
    r"file(?:name)?\s*[=:]\s*[\"']?(?P<name>[^\s`\"']+)[^\n`]*$",
    re.M,
)


def display_markdown(text: str) -> str:
    """Model answers use ```lang file=name; markdown renderers expect a bare language. Show the name as a caption."""
    return _FENCE_INFO_RE.sub(lambda m: f"{m['indent']}**`{m['name']}`**\n\n{m['indent']}{m['fence']}{m['lang']}", text)

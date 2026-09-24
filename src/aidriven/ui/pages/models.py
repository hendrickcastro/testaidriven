"""Models page (ContextAdmin-style): one card per provider with its key and connection, the curated catalog and a
live model search from the provider's API; one click adds a fully pre-configured profile. The profile editor is
dropdown-driven: model id, reasoning, limits and sampling are picked from lists, not typed.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from nicegui import ui

from aidriven.adapters.llm.catalog import PROVIDERS, CatalogEntry, ProviderInfo, catalog_for, find_entry, provider_info
from aidriven.adapters.secrets import mask
from aidriven.domain.models import (
    Capabilities,
    GenerationParams,
    ModelProfile,
    Pricing,
    ProviderConnection,
    ProviderKind,
    ReasoningMode,
    Result,
    new_id,
)
from aidriven.ports import DiscoveredModel
from aidriven.services import model_intel
from aidriven.ui.common import (
    action_slot,
    confirm,
    ctx,
    fmt_cost,
    frame,
    notify_error,
    read_upload,
    root_dialog,
    section,
)
from aidriven.ui.i18n import t

DEFAULT = "__default__"  # select sentinel = "not sent, provider default"
DISCOVERY_TTL_S = 600
MAX_LISTED = 300
WORKSPACE_HEADER = "anthropic-workspace-id"
_discovered: dict[str, tuple[float, list[DiscoveredModel]]] = {}  # connection id -> (timestamp, models)

MAX_TOKEN_PRESETS = [1024, 2048, 4096, 8192, 16000, 32000, 64000, 128000]
TEMPERATURE_PRESETS = [0.0, 0.2, 0.5, 0.7, 1.0, 1.5, 2.0]
TOP_P_PRESETS = [0.5, 0.8, 0.9, 0.95, 1.0]
TOP_K_PRESETS = [1, 10, 20, 40, 64, 100]
SEED_PRESETS = [0, 7, 42, 1234]
BUDGET_PRESETS = [1024, 2048, 4096, 8192, 16384, 24576, 32768, 65536]


def _providers() -> list[ProviderConnection]:
    return sorted(ctx().repo.find(ProviderConnection), key=lambda p: p.name.lower())


def _profiles() -> list[ModelProfile]:
    return sorted(ctx().repo.find(ModelProfile), key=lambda p: p.name.lower())


def _key_state(conn: ProviderConnection | None, info: ProviderInfo) -> tuple[str | None, str | None]:
    """(masked key, source) where source is 'saved' | 'env' | None."""
    if conn is None:
        return None, None
    key, source = ctx().api_key_for(conn)
    return (mask(key) if key else None), source


def _is_ready(conn: ProviderConnection | None, info: ProviderInfo) -> bool:
    if conn is None or not conn.enabled:
        return False
    if info.base_url == "required" and not conn.base_url:
        return False
    return not info.needs_key or _key_state(conn, info)[1] is not None


def _price_label(inp: float | None, out: float | None) -> str:
    if inp is None or out is None:
        return t("models.price_na")
    if inp == 0 and out == 0:
        return t("models.free")
    return f"${inp:g} / ${out:g}"


def _ctx_label(n: int | None) -> str:
    if not n:
        return ""
    return f"{n // 1_000_000}M ctx" if n >= 1_000_000 else f"{round(n / 1000)}k ctx"


def _unique_name(base: str) -> str:
    names = {p.name for p in _profiles()}
    if base not in names:
        return base
    i = 2
    while f"{base} ({i})" in names:
        i += 1
    return f"{base} ({i})"


# ---------------------------------------------------------------------------------------------- profiles from picks


def profile_from_pick(
    conn: ProviderConnection, model_id: str, discovered: DiscoveredModel | None = None
) -> ModelProfile:
    """A ready-to-run profile: capabilities, limits and prices from the catalog and/or the provider's API."""
    entry = find_entry(conn.kind, model_id)
    intel = model_intel.lookup(conn.kind, model_id)
    if entry:
        cap = entry.capabilities.model_copy(deep=True)
        pricing = entry.pricing.model_copy()
    elif intel:
        cap = model_intel.capabilities_from_intel(conn.kind, model_id, intel)
        pricing = model_intel.pricing_from_intel(intel)
    else:
        cap, pricing = Capabilities(), Pricing()
    if intel and intel.input_per_mtok is not None and conn.kind != ProviderKind.FAKE:
        pricing.input_per_mtok = intel.input_per_mtok  # live list price
        pricing.output_per_mtok = intel.output_per_mtok or pricing.output_per_mtok
    if discovered:
        if discovered.context_window:
            cap.context_window = discovered.context_window
        if discovered.max_output_tokens:
            cap.max_output_tokens = discovered.max_output_tokens
        if discovered.vision is not None:
            cap.vision = discovered.vision
        if discovered.input_per_mtok is not None:
            pricing.input_per_mtok = round(discovered.input_per_mtok, 4)
        if discovered.output_per_mtok is not None:
            pricing.output_per_mtok = round(discovered.output_per_mtok, 4)
    params = GenerationParams(max_output_tokens=min(16000, cap.max_output_tokens))
    if entry:
        params = params.model_copy(update=entry.default_params)
        params.max_output_tokens = min(params.max_output_tokens, cap.max_output_tokens)
    elif cap.reasoning in (ReasoningMode.EFFORT, ReasoningMode.LEVEL) and "high" in cap.reasoning_efforts:
        params.reasoning_effort = "high"
    label = (
        entry.label
        if entry
        else (
            intel.name.split(": ", 1)[-1]
            if intel
            else (discovered.label if discovered and discovered.label else model_id)
        )
    )
    if params.reasoning_effort:
        label = f"{label} · {params.reasoning_effort}"
    return ModelProfile(
        name=_unique_name(label),
        provider_id=conn.id,
        model=model_id,
        description=entry.notes if entry else (t("models.inferred_note") if intel else ""),
        capabilities=cap,
        params=params,
        pricing=pricing,
    )


def add_profile(conn: ProviderConnection, model_id: str, discovered: DiscoveredModel | None, refresh: Any) -> None:
    try:
        prof = profile_from_pick(conn, model_id, discovered)
        ctx().repo.save(prof)
        ui.notify(t("models.added", name=prof.name), type="positive")
        refresh()
    except Exception as exc:
        notify_error(exc)


async def discover(conn: ProviderConnection, force: bool = False) -> list[DiscoveredModel]:
    cached = _discovered.get(conn.id)
    if cached and not force and time.time() - cached[0] < DISCOVERY_TTL_S:
        return cached[1]
    models = await ctx().adapter_for_connection(conn).list_models()
    await model_intel.load_index()
    models = model_intel.enrich(conn.kind, models)  # adds prices/context/dates, newest first
    _discovered[conn.id] = (time.time(), models)
    return models


# ---------------------------------------------------------------------------------------------- provider cards


def model_row(title: str, model_id: str, badges: list[tuple[str, str]], note: str, added: int, on_add: Any) -> None:
    with ui.row().classes("w-full items-center no-wrap gap-3 py-1 border-b border-gray-200 dark:border-neutral-700"):
        with ui.column().classes("gap-0 flex-1 min-w-0"):
            with ui.row().classes("items-center gap-2"):
                ui.label(title).classes("font-medium")
                if added:
                    ui.badge(t("models.added_n", n=added), color="positive")
            ui.label(model_id).classes("font-mono text-xs text-grey-6")
            if note:
                ui.label(note).classes("text-caption text-grey-7")
        with ui.row().classes("items-center gap-1 no-wrap"):
            for text, color in badges:
                if text:
                    ui.badge(text, color=color).props("outline")
            ui.button(t("models.add") if not added else t("models.add_another"), icon="add", on_click=on_add).props(
                "dense unelevated size=sm" + ("" if not added else " outline")
            )


def catalog_badges(e: CatalogEntry, kind: ProviderKind) -> list[tuple[str, str]]:
    cap = e.capabilities
    intel = model_intel.lookup(kind, e.model) if kind != ProviderKind.FAKE else None
    inp = intel.input_per_mtok if intel and intel.input_per_mtok is not None else e.pricing.input_per_mtok
    out_p = intel.output_per_mtok if intel and intel.output_per_mtok is not None else e.pricing.output_per_mtok
    out = [
        (_price_label(inp, out_p), "grey-7"),
        (_ctx_label(cap.context_window), "grey-7"),
    ]
    if cap.vision:
        out.append((t("cap.vision"), "teal"))
    if cap.reasoning != ReasoningMode.NONE:
        out.append((t(f"reasoning.{cap.reasoning.value}"), "purple"))
    if cap.tools:
        out.append((t("cap.tools"), "blue-grey"))
    return out


def provider_card(info: ProviderInfo, conn: ProviderConnection | None, refresh: Any) -> None:
    ready = _is_ready(conn, info)
    masked, source = _key_state(conn, info)
    profiles = [p for p in _profiles() if conn and p.provider_id == conn.id]
    with ui.expansion().classes("w-full border rounded border-gray-200 dark:border-neutral-700") as exp:
        with exp.add_slot("header"), ui.row().classes("items-center gap-2 w-full no-wrap"):
            ui.label(conn.name if conn and conn.name != info.label else info.label).classes(
                "text-subtitle1 font-medium"
            )
            ui.badge(
                t("models.connected") if ready else t("models.not_configured"),
                color="positive" if ready else "orange-8",
            )
            if source:
                ui.badge(t(f"providers.keysrc_{source}"), color="grey-7").props("outline")
            if conn:
                ui.badge(t("models.n_profiles", n=len(profiles)), color="grey-7").props("outline")
            ui.space()
            ui.label(info.description).classes("text-caption text-grey-7 max-md:hidden text-right")
        with ui.column().classes("w-full gap-3 px-2 pb-3"):
            with ui.row().classes("gap-4 text-caption"):
                if info.key_url:
                    ui.link(t("models.get_key"), info.key_url, new_tab=True)
                if info.docs_url:
                    ui.link(t("models.docs"), info.docs_url, new_tab=True)
            connection_form(info, conn, masked, source, refresh)
            if conn is not None:
                models_panel(info, conn, profiles, refresh)


def connection_form(
    info: ProviderInfo, conn: ProviderConnection | None, masked: str | None, source: str | None, refresh: Any
) -> None:
    c = ctx()
    with ui.card().classes("w-full"):
        ui.label(t("models.connection")).classes("font-medium")
        with ui.row().classes("w-full items-end"):
            key = None
            if info.needs_key or info.kind == ProviderKind.OLLAMA:
                status = (
                    t("providers.key_current", key=masked)
                    if source == "saved"
                    else t("providers.key_env")
                    if source == "env"
                    else t("providers.key_none")
                )
                key = ui.input(
                    f"{t('providers.api_key')} — {status}",
                    password=True,
                    password_toggle_button=True,
                    placeholder=t("providers.key_hint"),
                ).classes("flex-[2] min-w-[260px]")
            workspace = None
            if info.kind == ProviderKind.ANTHROPIC:
                workspace = ui.input(
                    t("models.workspace_id"),
                    value=(conn.extra_headers.get(WORKSPACE_HEADER, "") if conn else ""),
                    placeholder="wrkspc_…",
                ).classes("flex-1 min-w-[220px]")
                workspace.tooltip(t("models.workspace_hint"))
            base = None
            if info.base_url != "none":
                base = ui.input(
                    t("providers.base_url") + (" *" if info.base_url == "required" else ""),
                    value=(conn.base_url if conn else "") or "",
                    placeholder=info.base_url_hint or t("providers.base_url_default"),
                ).classes("flex-[2] min-w-[260px]")
        with ui.expansion(t("models.advanced"), icon="tune").classes("w-full text-sm"):
            with ui.row().classes("w-full"):
                name = ui.input(t("common.name"), value=conn.name if conn else info.label).classes("flex-1")
                timeout = ui.select(
                    [60, 120, 300, 600, 1200, 3600],
                    label=t("providers.timeout"),
                    value=int(conn.timeout_s) if conn else 600,
                    new_value_mode="add-unique",
                ).classes("w-40")
                retries = ui.select(
                    [0, 1, 2, 3, 5], label=t("providers.retries"), value=conn.max_retries if conn else 2
                ).classes("w-32")
                enabled = ui.switch(t("common.enabled"), value=conn.enabled if conn else True)
            api_version = None
            if info.kind == ProviderKind.AZURE_OPENAI:
                api_version = ui.input(t("providers.api_version"), value=(conn.api_version if conn else "") or "")
            headers = (
                ui.textarea(
                    t("providers.headers"),
                    value=json.dumps({k: v for k, v in conn.extra_headers.items() if k != WORKSPACE_HEADER})
                    if conn and any(k != WORKSPACE_HEADER for k in conn.extra_headers)
                    else "",
                )
                .classes("w-full")
                .props("autogrow")
            )

        def save(refresh_after: bool = True) -> ProviderConnection | None:
            try:
                target = (
                    conn.model_copy(deep=True)
                    if conn
                    else ProviderConnection(
                        id=f"provider_{info.kind.value}"
                        if not c.repo.get(ProviderConnection, f"provider_{info.kind.value}")
                        else new_id("provider_"),
                        kind=info.kind,
                        name=info.label,
                    )
                )
                target.name = (name.value or info.label).strip()
                if base is not None:
                    target.base_url = (base.value or "").strip() or None
                if info.base_url == "required" and not target.base_url:
                    raise ValueError(t("models.base_url_required"))
                if api_version is not None:
                    target.api_version = (api_version.value or "").strip() or None
                target.timeout_s = float(timeout.value or 600)
                target.max_retries = int(retries.value or 0)
                target.enabled = bool(enabled.value)
                target.extra_headers = json.loads(headers.value) if (headers.value or "").strip() else {}
                if workspace is not None and (workspace.value or "").strip():
                    target.extra_headers[WORKSPACE_HEADER] = workspace.value.strip()
                c.repo.save(target)
                if key is not None and key.value:
                    c.secrets.set(target.secret_key, key.value.strip())
                _discovered.pop(target.id, None)
                if refresh_after:
                    ui.notify(t("common.saved"), type="positive")
                    refresh()
                return target
            except Exception as exc:
                notify_error(exc)
                return None

        async def test() -> None:
            # Save what is typed first (new key/workspace), but redraw only after showing the result:
            # redrawing deletes this card and any notification bound to it.
            target = save(refresh_after=False)
            if target is None:
                return
            try:
                ui.notify(t("providers.testing"), timeout=1500)
                res = await c.adapter_for_connection(target).test_connection(None)
                if not res.ok:
                    logging.getLogger("aidriven.ui").error("connection test failed for %s: %s", target.name, res.detail)
                ui.notify(
                    f"{'✔' if res.ok else '✖'} {res.detail} ({res.latency_ms:.0f} ms)",
                    type="positive" if res.ok else "negative",
                    multi_line=True,
                    timeout=15000,
                )
            except Exception as exc:
                notify_error(exc)
            ui.timer(0.2, refresh, once=True)

        def clear_key() -> None:
            if conn:
                c.secrets.delete(conn.secret_key)
                ui.notify(t("providers.key_cleared"))
                refresh()

        def delete_conn() -> None:
            if not conn:
                return
            used = [m.name for m in _profiles() if m.provider_id == conn.id]
            if used:
                ui.notify(t("providers.in_use", models=", ".join(used)), type="warning")
                return

            def do() -> None:
                c.repo.delete(ProviderConnection, conn.id)
                c.secrets.delete(conn.secret_key)
                refresh()

            confirm(t("providers.confirm_delete", name=conn.name), do)

        with ui.row():
            ui.button(t("models.save_connection"), icon="save", on_click=save)
            ui.button(t("providers.test"), icon="network_check", on_click=test).props("flat")
            if conn and source == "saved":
                ui.button(t("providers.clear_key"), icon="key_off", on_click=clear_key).props("flat color=negative")
            if conn:
                ui.button(t("models.delete_connection"), icon="delete", on_click=delete_conn).props(
                    "flat color=negative"
                )
        if c.backend == "firestore":
            ui.label(t("models.keys_in_firestore")).classes("text-caption text-grey-7")
        else:
            ui.label(t("models.keys_local")).classes("text-caption text-grey-7")


def models_panel(info: ProviderInfo, conn: ProviderConnection, profiles: list[ModelProfile], refresh: Any) -> None:
    added: dict[str, int] = {}
    for p in profiles:
        added[p.model] = added.get(p.model, 0) + 1
    entries = catalog_for(conn.kind)
    if entries:
        with ui.card().classes("w-full"):
            ui.label(t("models.catalog")).classes("font-medium")
            ui.label(t("models.catalog_hint")).classes("text-caption text-grey-7")
            for e in entries:
                model_row(
                    e.label,
                    e.model,
                    catalog_badges(e, conn.kind),
                    e.notes,
                    added.get(e.model, 0),
                    lambda e=e: add_profile(conn, e.model, None, refresh),
                )
    with ui.card().classes("w-full"):
        ui.label(t("models.search")).classes("font-medium")
        ui.label(t("models.search_hint")).classes("text-caption text-grey-7")
        state: dict[str, Any] = {"q": "", "models": None, "error": "", "all": False}
        with ui.row().classes("w-full items-center"):
            query = ui.input(t("models.search_placeholder")).props("clearable dense").classes("flex-1")
            ui.switch(
                t("models.show_non_chat"),
                value=False,
                on_change=lambda e: (state.update(all=bool(e.value)), results.refresh()),
            )
            load_btn = ui.button(t("models.load_models"), icon="travel_explore").props("unelevated")

        @ui.refreshable
        def results() -> None:
            if state["error"]:
                ui.label(state["error"]).classes("text-negative text-caption")
                return
            models: list[DiscoveredModel] | None = state["models"]
            if models is None:
                ui.label(t("models.not_loaded")).classes("text-caption text-grey-7")
                return
            q = state["q"].lower()
            pool = models if state["all"] else [m for m in models if model_intel.is_chat_model(m.id)]
            hits = [m for m in pool if q in m.id.lower() or q in (m.label or "").lower()]
            ui.label(
                t("models.search_count", shown=min(len(hits), MAX_LISTED), total=len(hits))
                + (" · " + t("models.hidden_non_chat", n=len(models) - len(pool)) if not state["all"] else "")
            ).classes("text-caption")
            with ui.column().classes("w-full gap-0 max-h-[520px] overflow-auto"):
                for m in hits[:MAX_LISTED]:
                    entry = find_entry(conn.kind, m.id)
                    badges = [
                        (
                            _price_label(m.input_per_mtok, m.output_per_mtok)
                            if m.input_per_mtok is not None
                            else (
                                _price_label(entry.pricing.input_per_mtok, entry.pricing.output_per_mtok)
                                if entry
                                else ""
                            ),
                            "grey-7",
                        ),
                        (
                            _ctx_label(m.context_window or (entry.capabilities.context_window if entry else None)),
                            "grey-7",
                        ),
                        (t("cap.vision") if m.vision or (entry and entry.capabilities.vision) else "", "teal"),
                        (t("models.in_catalog") if entry else "", "positive"),
                        (time.strftime("%Y-%m-%d", time.gmtime(m.created)) if m.created else "", "grey-6"),
                    ]
                    model_row(
                        m.label or m.id,
                        m.id,
                        badges,
                        "",
                        added.get(m.id, 0),
                        lambda m=m: add_profile(conn, m.id, m, refresh),
                    )

        async def load(force: bool = False) -> None:
            try:
                state["error"] = ""
                load_btn.props("loading")
                state["models"] = await discover(conn, force=force)
            except Exception as exc:
                logging.getLogger("aidriven.ui").error("listing models of %s failed", conn.name, exc_info=exc)
                state["error"] = f"{type(exc).__name__}: {exc}"[:400]
            finally:
                load_btn.props(remove="loading")
            results.refresh()

        load_btn.on_click(lambda: load(force=True))
        query.on_value_change(lambda e: (state.update(q=e.value or ""), results.refresh()))
        results()
        if conn.id in _discovered:
            state["models"] = _discovered[conn.id][1]
            results.refresh()


@ui.refreshable
def providers_section() -> None:
    connections = _providers()
    for info in PROVIDERS:
        mine = [c for c in connections if c.kind == info.kind]
        if not mine:
            provider_card(info, None, _refresh_all)
        for conn in mine:
            provider_card(info, conn, _refresh_all)
    multi = [p for p in PROVIDERS if p.multiple]
    with ui.row().classes("items-center"):
        ui.label(t("models.add_another_connection")).classes("text-caption")
        for info in multi:
            ui.button(info.label, icon="add", on_click=lambda info=info: _new_extra_connection(info)).props(
                "flat dense"
            )


def _new_extra_connection(info: ProviderInfo) -> None:
    c = ctx()
    n = sum(1 for p in _providers() if p.kind == info.kind) + 1
    c.repo.save(ProviderConnection(kind=info.kind, name=f"{info.label} {n}"))
    _refresh_all()


def _refresh_all() -> None:
    providers_section.refresh()
    profiles_table.refresh()


# ---------------------------------------------------------------------------------------------- profile editor


def _opts(values: list[Any], current: Any, default_label: str | None = None) -> dict[Any, str]:
    opts: dict[Any, str] = {}
    if default_label is not None:
        opts[DEFAULT] = default_label
    for v in values:
        opts[v] = str(v)
    if current is not None and current not in opts:
        opts[current] = str(current)
    return opts


def _num(v: Any, cast: type) -> Any:
    if v in (None, "", DEFAULT):
        return None
    return cast(float(v)) if cast is int else cast(v)


def profile_dialog(existing: ModelProfile, on_saved: Any, is_new: bool = False) -> None:
    c = ctx()
    prof = existing.model_copy(deep=True)
    conn = c.repo.get(ProviderConnection, prof.provider_id)
    par = prof.params
    state = {"cap": prof.capabilities.model_copy(deep=True)}
    with root_dialog() as dialog, ui.card().classes("w-[920px] max-w-full"):
        ui.label(t("profiles.add") if is_new else t("profiles.edit")).classes("text-h6")
        with ui.tabs().classes("w-full") as tabs:
            tab_gen = ui.tab(t("profiles.tab_general"))
            tab_par = ui.tab(t("profiles.tab_params"))
            tab_cap = ui.tab(t("profiles.tab_capabilities"))
            tab_price = ui.tab(t("profiles.tab_pricing"))
        with ui.tab_panels(tabs, value=tab_par if not is_new else tab_gen).classes("w-full"):
            # -------------------------------------------------------------- general
            with ui.tab_panel(tab_gen):
                name = ui.input(t("common.name"), value=prof.name).classes("w-full")
                model_opts: dict[str, str] = {}
                if conn:
                    model_opts.update({e.model: f"{e.label} — {e.model}" for e in catalog_for(conn.kind)})
                    for m in _discovered.get(conn.id, (0, []))[1]:
                        model_opts.setdefault(m.id, m.label or m.id)
                model_opts.setdefault(prof.model, prof.model)
                with ui.row().classes("w-full items-end"):
                    model = ui.select(
                        model_opts, label=t("profiles.model_id"), value=prof.model, with_input=True
                    ).classes("flex-1")

                    async def refresh_models() -> None:
                        if not conn:
                            return
                        try:
                            for m in await discover(conn, force=True):
                                model_opts.setdefault(m.id, m.label or m.id)
                            model.set_options(model_opts, value=model.value)
                            ui.notify(t("profiles.discovered", n=len(model_opts)))
                        except Exception as exc:
                            notify_error(exc)

                    ui.button(icon="refresh", on_click=refresh_models).props("flat").tooltip(t("profiles.discover"))
                ui.label(t("profiles.provider_fixed", provider=conn.name if conn else "?")).classes(
                    "text-caption text-grey-7"
                )
                desc = ui.input(t("common.description"), value=prof.description).classes("w-full")
                tags = ui.input(t("common.tags"), value=", ".join(prof.tags)).classes("w-full")
                system = (
                    ui.textarea(t("profiles.system_prompt"), value=prof.system_prompt)
                    .classes("w-full")
                    .props("autogrow")
                )
            # -------------------------------------------------------------- parameters (dropdowns)
            with ui.tab_panel(tab_par):
                problems_label = ui.label().classes("text-negative text-caption")
                params_box = ui.column().classes("w-full")
                widgets: dict[str, Any] = {}

                def render_params() -> None:
                    cap = state["cap"]
                    params_box.clear()
                    widgets.clear()
                    dflt = t("param.default")
                    with params_box:
                        with ui.grid(columns=3).classes("w-full"):
                            if cap.reasoning in (ReasoningMode.EFFORT, ReasoningMode.LEVEL):
                                widgets["effort"] = ui.select(
                                    _opts(cap.reasoning_efforts, par.reasoning_effort, dflt),
                                    label=t("param.reasoning_effort"),
                                    value=par.reasoning_effort or DEFAULT,
                                )
                            if cap.reasoning == ReasoningMode.BUDGET:
                                budgets = [b for b in BUDGET_PRESETS if b <= cap.reasoning_budget_max]
                                widgets["budget"] = ui.select(
                                    _opts(budgets, par.reasoning_budget, dflt),
                                    label=t("param.reasoning_budget"),
                                    value=par.reasoning_budget or DEFAULT,
                                    new_value_mode="add-unique",
                                )
                            if cap.reasoning != ReasoningMode.NONE:
                                widgets["think"] = ui.select(
                                    {DEFAULT: dflt, "on": t("param.think_on"), "off": t("param.think_off")},
                                    label=t("param.reasoning_enabled"),
                                    value=DEFAULT
                                    if par.reasoning_enabled is None
                                    else ("on" if par.reasoning_enabled else "off"),
                                )
                            presets = [v for v in MAX_TOKEN_PRESETS if v <= cap.max_output_tokens] or [
                                cap.max_output_tokens
                            ]
                            if cap.max_output_tokens not in presets:
                                presets.append(cap.max_output_tokens)
                            widgets["max_tokens"] = ui.select(
                                _opts(presets, par.max_output_tokens),
                                label=t("param.max_output_tokens"),
                                value=par.max_output_tokens,
                                new_value_mode="add-unique",
                            )
                            if cap.temperature:
                                widgets["temperature"] = ui.select(
                                    _opts(TEMPERATURE_PRESETS, par.temperature, dflt),
                                    label=t("param.temperature"),
                                    value=par.temperature if par.temperature is not None else DEFAULT,
                                    new_value_mode="add-unique",
                                )
                            if cap.top_p:
                                widgets["top_p"] = ui.select(
                                    _opts(TOP_P_PRESETS, par.top_p, dflt),
                                    label=t("param.top_p"),
                                    value=par.top_p if par.top_p is not None else DEFAULT,
                                    new_value_mode="add-unique",
                                )
                            if cap.top_k:
                                widgets["top_k"] = ui.select(
                                    _opts(TOP_K_PRESETS, par.top_k, dflt),
                                    label=t("param.top_k"),
                                    value=par.top_k if par.top_k is not None else DEFAULT,
                                    new_value_mode="add-unique",
                                )
                            if cap.seed:
                                widgets["seed"] = ui.select(
                                    _opts(SEED_PRESETS, par.seed, t("param.no_seed")),
                                    label=t("param.seed"),
                                    value=par.seed if par.seed is not None else DEFAULT,
                                    new_value_mode="add-unique",
                                )
                            if cap.verbosity:
                                widgets["verbosity"] = ui.select(
                                    {DEFAULT: dflt, "low": "low", "medium": "medium", "high": "high"},
                                    label=t("param.verbosity"),
                                    value=par.verbosity or DEFAULT,
                                )
                        with ui.row():
                            if cap.json_mode:
                                widgets["json_mode"] = ui.switch(t("param.json_mode"), value=par.json_mode)
                            widgets["stream"] = ui.switch(t("param.stream"), value=par.stream)
                        with ui.expansion(t("models.advanced"), icon="tune").classes("w-full"):
                            if cap.stop_sequences:
                                widgets["stop"] = ui.input(
                                    t("param.stop_sequences"), value=" | ".join(par.stop_sequences)
                                ).classes("w-full")
                            widgets["extra"] = (
                                ui.textarea(
                                    t("param.extra"), value=json.dumps(par.extra, indent=1) if par.extra else ""
                                )
                                .classes("w-full")
                                .props("autogrow")
                            )
                            ui.label(t("param.extra_hint")).classes("text-caption text-grey-7")
                    for w in widgets.values():
                        w.on_value_change(lambda _: validate())

                render_params()
            # -------------------------------------------------------------- capabilities (auto, overridable)
            with ui.tab_panel(tab_cap):
                ui.label(t("profiles.cap_auto")).classes("text-caption text-grey-7")
                chips = ui.row().classes("gap-1")

                def render_chips() -> None:
                    cap = state["cap"]
                    chips.clear()
                    with chips:
                        for k in (
                            "vision",
                            "tools",
                            "json_mode",
                            "streaming",
                            "temperature",
                            "top_p",
                            "top_k",
                            "seed",
                            "verbosity",
                        ):
                            ui.badge(t(f"cap.{k}"), color="positive" if getattr(cap, k) else "grey-5")
                        ui.badge(t(f"reasoning.{cap.reasoning.value}"), color="purple")
                        ui.badge(_ctx_label(cap.context_window), color="grey-7")
                        ui.badge(f"{cap.max_output_tokens:,} out", color="grey-7")

                render_chips()

                def redetect() -> None:
                    if not conn:
                        return
                    dm = next((m for m in _discovered.get(conn.id, (0, []))[1] if m.id == model.value), None)
                    fresh = profile_from_pick(conn, model.value, dm)
                    state["cap"] = fresh.capabilities
                    p_in.value, p_out.value = fresh.pricing.input_per_mtok, fresh.pricing.output_per_mtok
                    p_cached.value = fresh.pricing.cached_input_per_mtok
                    render_chips()
                    render_params()
                    sync_switches()
                    ui.notify(t("profiles.redetected"))

                ui.button(t("profiles.redetect"), icon="auto_fix_high", on_click=redetect).props("flat")
                with ui.expansion(t("profiles.cap_override"), icon="edit").classes("w-full"):
                    with ui.grid(columns=3).classes("w-full"):
                        sw = {
                            k: ui.switch(t(f"cap.{k}"), value=getattr(state["cap"], k))
                            for k in (
                                "vision",
                                "tools",
                                "json_mode",
                                "streaming",
                                "temperature",
                                "top_p",
                                "top_k",
                                "seed",
                                "stop_sequences",
                                "verbosity",
                            )
                        }
                    reasoning_mode = ui.select(
                        {m.value: t(f"reasoning.{m.value}") for m in ReasoningMode},
                        label=t("cap.reasoning"),
                        value=state["cap"].reasoning.value,
                    )
                    efforts = ui.select(
                        ["minimal", "low", "medium", "high", "xhigh", "max"],
                        multiple=True,
                        label=t("cap.reasoning_efforts"),
                        value=[e for e in state["cap"].reasoning_efforts],
                    ).props("use-chips")
                    with ui.row().classes("w-full"):
                        ctx_window = ui.select(
                            _opts([32000, 128000, 200000, 400000, 1000000, 2000000], state["cap"].context_window),
                            label=t("cap.context_window"),
                            value=state["cap"].context_window,
                            new_value_mode="add-unique",
                        ).classes("flex-1")
                        max_out = ui.select(
                            _opts(MAX_TOKEN_PRESETS, state["cap"].max_output_tokens),
                            label=t("cap.max_output_tokens"),
                            value=state["cap"].max_output_tokens,
                            new_value_mode="add-unique",
                        ).classes("flex-1")

                    def sync_switches() -> None:
                        cap = state["cap"]
                        for k, s in sw.items():
                            s.value = getattr(cap, k)
                        reasoning_mode.value = cap.reasoning.value
                        efforts.set_options(
                            sorted(set(efforts.options) | set(cap.reasoning_efforts)), value=list(cap.reasoning_efforts)
                        )

                    def apply_override(_: Any = None) -> None:
                        cap = state["cap"].model_copy(update={k: bool(s.value) for k, s in sw.items()})
                        cap.reasoning = ReasoningMode(reasoning_mode.value)
                        cap.reasoning_efforts = list(efforts.value or [])
                        cap.context_window = int(float(ctx_window.value or cap.context_window))
                        cap.max_output_tokens = int(float(max_out.value or cap.max_output_tokens))
                        state["cap"] = cap
                        render_chips()
                        render_params()

                    for el in [*sw.values(), reasoning_mode, efforts, ctx_window, max_out]:
                        el.on_value_change(apply_override)
            # -------------------------------------------------------------- pricing
            with ui.tab_panel(tab_price):
                ui.label(t("profiles.price_auto")).classes("text-caption text-grey-7")
                with ui.row().classes("w-full"):
                    p_in = ui.number(t("price.input"), value=prof.pricing.input_per_mtok, min=0, step=0.01).classes(
                        "flex-1"
                    )
                    p_out = ui.number(t("price.output"), value=prof.pricing.output_per_mtok, min=0, step=0.01).classes(
                        "flex-1"
                    )
                    p_cached = ui.number(
                        t("price.cached"), value=prof.pricing.cached_input_per_mtok, min=0, step=0.01
                    ).classes("flex-1")

        def collect() -> ModelProfile:
            w = widgets

            def val(k: str) -> Any:
                return w[k].value if k in w else None

            think = val("think")
            new_par = GenerationParams(
                reasoning_effort=None if val("effort") in (None, DEFAULT) else val("effort"),
                reasoning_budget=_num(val("budget"), int),
                reasoning_enabled=None if think in (None, DEFAULT) else think == "on",
                max_output_tokens=int(float(val("max_tokens") or 1024)),
                temperature=_num(val("temperature"), float),
                top_p=_num(val("top_p"), float),
                top_k=_num(val("top_k"), int),
                seed=_num(val("seed"), int),
                verbosity=None if val("verbosity") in (None, DEFAULT) else val("verbosity"),
                json_mode=bool(val("json_mode")),
                stream=bool(val("stream")) if "stream" in w else True,
                stop_sequences=[s.strip() for s in (val("stop") or "").split("|") if s.strip()],
                extra=json.loads(val("extra")) if (val("extra") or "").strip() else {},
            )
            return prof.model_copy(
                update={
                    "name": (name.value or "").strip(),
                    "model": model.value,
                    "description": desc.value or "",
                    "tags": [x.strip() for x in (tags.value or "").split(",") if x.strip()],
                    "system_prompt": system.value or "",
                    "capabilities": state["cap"],
                    "params": new_par,
                    "pricing": Pricing(
                        input_per_mtok=float(p_in.value or 0),
                        output_per_mtok=float(p_out.value or 0),
                        cached_input_per_mtok=None if p_cached.value in (None, "") else float(p_cached.value),
                    ),
                }
            )

        def validate() -> None:
            try:
                problems_label.text = " · ".join(collect().validate_params())
            except Exception as exc:
                problems_label.text = str(exc)

        def save() -> None:
            try:
                new = collect()
                if not new.name or not new.model:
                    raise ValueError(t("profiles.required"))
                clash = next((m for m in _profiles() if m.name == new.name and m.id != new.id), None)
                if clash:
                    raise ValueError(t("profiles.name_taken", name=new.name))
                problems = new.validate_params()
                if problems:
                    raise ValueError("; ".join(problems))
                if (
                    not is_new
                    and new.name != existing.name
                    and c.repo.find(Result, where={"model_id": new.id}, limit=1)
                ):
                    ui.notify(t("profiles.rename_warning"), type="warning", timeout=8000)
                if not is_new:
                    new.version = existing.version + 1
                c.repo.save(new)
                dialog.close()
                ui.notify(t("common.saved"), type="positive")
                on_saved()
            except Exception as exc:
                notify_error(exc)

        validate()
        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("common.save"), on_click=save, icon="save")
    dialog.open()


# ---------------------------------------------------------------------------------------------- profiles table


@ui.refreshable
def profiles_table() -> None:
    c = ctx()
    providers = {p.id: p.name for p in _providers()}
    judge_id = c.config.judge.model_id
    rows = []
    for m in _profiles():
        cap, par = m.capabilities, m.params
        feats = [t(f"cap.{k}") for k in ("vision", "tools", "json_mode") if getattr(cap, k)]
        reasoning = (
            par.reasoning_effort
            or (str(par.reasoning_budget) if par.reasoning_budget else "")
            or ("" if par.reasoning_enabled is None else ("on" if par.reasoning_enabled else "off"))
        )
        problems = m.validate_params()
        rows.append(
            {
                "id": m.id,
                "name": m.name + (" ⚖" if m.id == judge_id else ""),
                "provider": providers.get(m.provider_id, "?"),
                "model": m.model,
                "features": ", ".join(feats),
                "reasoning": reasoning or t("param.default"),
                "max_tokens": f"{par.max_output_tokens:,}",
                "price": f"{fmt_cost(m.pricing.input_per_mtok)} / {fmt_cost(m.pricing.output_per_mtok)}",
                "problems": ("⚠ " + "; ".join(problems)) if problems else "",
            }
        )
    columns: list[dict[str, Any]] = [
        {"name": "actions", "label": "", "field": "id"},
        {"name": "name", "label": t("common.name"), "field": "name", "align": "left", "sortable": True},
        {"name": "provider", "label": t("profiles.provider"), "field": "provider", "align": "left", "sortable": True},
        {"name": "model", "label": t("profiles.model_id"), "field": "model", "align": "left"},
        {"name": "reasoning", "label": t("param.reasoning_effort"), "field": "reasoning"},
        {"name": "max_tokens", "label": t("param.max_output_tokens"), "field": "max_tokens"},
        {"name": "features", "label": t("profiles.features"), "field": "features", "align": "left"},
        {"name": "price", "label": t("profiles.price"), "field": "price"},
        {"name": "problems", "label": "", "field": "problems", "classes": "text-negative"},
    ]
    if not rows:
        ui.label(t("models.no_profiles")).classes("text-grey-7")
        return
    table = ui.table(columns=columns, rows=rows, row_key="id", pagination=25).classes("w-full")
    table.add_slot(
        "body-cell-actions",
        action_slot(
            [
                ("edit", "edit", t("common.edit")),
                ("content_copy", "clone", t("common.clone")),
                ("gavel", "judge", t("profiles.use_as_judge")),
                ("network_check", "test", t("providers.test")),
                ("delete", "delete", t("common.delete")),
            ]
        ),
    )

    def get(row: dict[str, Any]) -> ModelProfile | None:
        return c.repo.get(ModelProfile, row["id"])

    def on_edit(e: Any) -> None:
        if prof := get(e.args):
            profile_dialog(prof, _refresh_all)

    def on_clone(e: Any) -> None:
        if prof := get(e.args):
            data = prof.model_dump(exclude={"id", "created_at", "updated_at", "version"})
            clone = ModelProfile.model_validate({**data, "name": _unique_name(t("profiles.copy_of", name=prof.name))})
            profile_dialog(clone, _refresh_all, is_new=True)

    def on_judge(e: Any) -> None:
        if prof := get(e.args):
            c.config.judge.model_id = prof.id
            c.config.judge.enabled = True
            c.save_config()
            ui.notify(t("profiles.judge_set", name=prof.name), type="positive")
            profiles_table.refresh()

    async def on_test(e: Any) -> None:
        prof = get(e.args)
        conn = c.repo.get(ProviderConnection, prof.provider_id) if prof else None
        if not (prof and conn):
            return
        try:
            ui.notify(t("providers.testing"), timeout=1500)
            res = await c.adapter_for_connection(conn).test_connection(prof.model)
            ui.notify(
                f"{'✔' if res.ok else '✖'} {prof.name}: {res.detail} ({res.latency_ms:.0f} ms)",
                type="positive" if res.ok else "negative",
                multi_line=True,
                timeout=10000,
            )
        except Exception as exc:
            notify_error(exc)

    def on_delete(e: Any) -> None:
        if prof := get(e.args):
            confirm(
                t("profiles.confirm_delete", name=prof.name),
                lambda: (c.repo.delete(ModelProfile, prof.id), _refresh_all()),
            )

    table.on("edit", on_edit)
    table.on("clone", on_clone)
    table.on("judge", on_judge)
    table.on("test", on_test)
    table.on("delete", on_delete)


# ---------------------------------------------------------------------------------------------- page


def page() -> None:
    with frame("nav.models"):
        with section(t("profiles.title"), t("profiles.caption")):
            with ui.row():
                ui.button(
                    t("profiles.import"), icon="upload", on_click=lambda: import_profiles_dialog(_refresh_all)
                ).props("flat")
                ui.button(t("profiles.export"), icon="download", on_click=export_profiles).props("flat")
            profiles_table()
        ui.separator()
        with section(t("providers.title"), t("models.providers_caption")):
            providers_section()


def export_profiles() -> None:
    data = [m.model_dump(mode="json", exclude={"created_at", "updated_at"}) for m in _profiles()]
    ui.download.content(json.dumps(data, indent=2), "aidriven-models.json", "application/json")


def import_profiles_dialog(on_saved: Any) -> None:
    async def handle(e: Any) -> None:
        try:
            data = json.loads((await read_upload(e)).decode("utf-8"))
            items = data if isinstance(data, list) else [data]
            known = {p.id for p in _providers()}
            n = 0
            for item in items:
                prof = ModelProfile.model_validate(item)
                if prof.provider_id not in known:
                    raise ValueError(t("profiles.unknown_provider", name=prof.name))
                ctx().repo.save(prof)
                n += 1
            ui.notify(t("common.imported", n=n), type="positive")
            dialog.close()
            on_saved()
        except Exception as exc:
            notify_error(exc)

    with root_dialog() as dialog, ui.card():
        ui.label(t("profiles.import")).classes("text-h6")
        ui.upload(on_upload=handle, auto_upload=True).props("accept=.json")
    dialog.open()


__all__ = ["page", "profile_from_pick", "provider_info"]

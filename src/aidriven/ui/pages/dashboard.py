"""Dashboard: counts, health warnings, recent runs and a leaderboard of the latest completed runs."""

from __future__ import annotations

from nicegui import ui

from aidriven.domain.models import ModelProfile, Result, ResultStatus, Run, RunStatus, Suite, Task
from aidriven.services.scoring import aggregate_by_model
from aidriven.ui.charts import radar
from aidriven.ui.common import ctx, fmt_cost, fmt_dt, fmt_num, frame, kpi, section, status_badge
from aidriven.ui.i18n import t


def page() -> None:
    c = ctx()
    with frame("nav.dashboard"):
        tasks = c.repo.find(Task)
        models = c.repo.find(ModelProfile)
        runs = c.repo.find(Run)
        suites = c.repo.find(Suite)
        with ui.row().classes("w-full"):
            kpi(t("dash.tasks"), str(len(tasks)), "assignment")
            kpi(t("dash.models"), str(len(models)), "smart_toy", "teal")
            kpi(t("dash.suites"), str(len(suites)), "playlist_play", "orange")
            kpi(t("dash.runs"), str(len(runs)), "rocket_launch", "purple")

        warnings = []
        if c.sandbox.current().isolation == "process":
            warnings.append(("warning", t("dash.warn_sandbox")))
        if not c.config.judge.model_id:
            warnings.append(("gavel", t("dash.warn_judge")))
        if c.backend != "firestore":
            warnings.append(("cloud_off", t("dash.warn_firestore")))
        if all(m.provider_id == "provider_fake" for m in models):
            warnings.append(("smart_toy", t("dash.warn_only_fake")))
        if warnings:
            with ui.card().classes("w-full bg-amber-50 dark:bg-amber-950"):
                for icon, text in warnings:
                    with ui.row().classes("items-center"):
                        ui.icon(icon).classes("text-warning")
                        ui.label(text)

        with ui.row().classes("w-full items-start no-wrap max-lg:flex-wrap"):
            with ui.card().classes("flex-1 min-w-[380px]"), section(t("dash.recent_runs")):
                if not runs:
                    ui.label(t("dash.no_runs"))
                for r in runs[:8]:
                    with ui.row().classes("items-center w-full"):
                        status_badge(RunStatus.RUNNING.value if c.runner.is_running(r.id) else r.status.value)
                        ui.link(r.name, f"/runs/{r.id}").classes("flex-1")
                        ui.label(f"{r.done_items}/{r.total_items}").classes("text-caption")
                        ui.label(fmt_dt(r.created_at)).classes("text-caption text-grey-6")
                with ui.row():
                    ui.button(
                        t("dash.go_suites"), icon="playlist_play", on_click=lambda: ui.navigate.to("/suites")
                    ).props("flat")
                    ui.button(
                        t("dash.go_compare"), icon="compare_arrows", on_click=lambda: ui.navigate.to("/compare")
                    ).props("flat")
            last = next((r for r in runs if r.status == RunStatus.COMPLETED), None)
            with ui.card().classes("flex-1 min-w-[380px]"), section(t("dash.leaderboard")):
                if last is None:
                    ui.label(t("dash.no_completed"))
                else:
                    ui.link(last.name, f"/runs/{last.id}").classes("text-caption")
                    results = c.repo.find(Result, where={"run_id": last.id})
                    aggs = sorted(aggregate_by_model(results), key=lambda a: -(a.mean_final or 0))
                    for i, a in enumerate(aggs, 1):
                        with ui.row().classes("items-center w-full"):
                            ui.label(f"{i}.").classes("w-6")
                            ui.label(a.model_name).classes("flex-1 font-medium")
                            ui.label(fmt_num(a.mean_final)).classes("font-mono")
                            ui.label(fmt_cost(a.cost_usd)).classes("text-caption text-grey-7 w-20 text-right")
                    pending = sum(1 for r in results if r.status == ResultStatus.DONE and not r.score.reviewed)
                    if pending:
                        ui.link(t("dash.pending_review", n=pending), "/review")
                    ui.echart(radar([(a.model_name, a) for a in aggs])).classes("w-full h-72")

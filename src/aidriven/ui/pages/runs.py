"""Runs: list, live detail (matrix + per-model dimensions), re-run dialog, result detail with scoring."""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from nicegui import ui

from aidriven.domain.models import Artifact, Result, ResultStatus, Run, RunStatus, Task
from aidriven.services import backfill
from aidriven.services.scoring import aggregate_by_model
from aidriven.ui.charts import radar
from aidriven.ui.common import (
    action_slot,
    confirm,
    ctx,
    display_markdown,
    fmt_cost,
    fmt_dt,
    fmt_ms,
    fmt_num,
    frame,
    kpi,
    notify_error,
    root_dialog,
    sandboxed_iframe,
    score_color,
    section,
    status_badge,
)
from aidriven.ui.i18n import t

FAILED = {ResultStatus.ERROR.value, ResultStatus.TIMEOUT.value, ResultStatus.REFUSED.value}


def _runs() -> list[Run]:
    return ctx().repo.find(Run)


# ---------------------------------------------------------------------------------------------- re-run


def rerun_dialog(run: Run, preselect_results: list[str] | None = None) -> None:
    c = ctx()
    results = c.repo.find(Result, where={"run_id": run.id})
    failed_ids = [r.id for r in results if r.status.value in FAILED]
    with root_dialog() as dialog, ui.card().classes("w-[720px] max-w-full"):
        ui.label(t("rerun.title", name=run.name)).classes("text-h6")
        scope = ui.radio(
            {
                "full": t("rerun.full"),
                "tasks": t("rerun.tasks"),
                "failed": t("rerun.failed", n=len(failed_ids)),
                "results": t("rerun.results"),
            },
            value="results" if preselect_results else "full",
        ).props("inline")
        slugs = (
            ui.select([x.slug for x in run.snapshot.tasks], label=t("rerun.pick_tasks"), multiple=True, with_input=True)
            .classes("w-full")
            .props("use-chips")
        )
        models = (
            ui.select([m.name for m in run.snapshot.models], label=t("rerun.pick_models"), multiple=True)
            .classes("w-full")
            .props("use-chips")
        )
        res_opts = {
            r.id: f"{r.task_slug} · {r.model_name} #{r.repetition + 1} ({t('status.' + r.status.value)})"
            for r in results
        }
        picked = (
            ui.select(
                res_opts, label=t("rerun.pick_results"), multiple=True, with_input=True, value=preselect_results or []
            )
            .classes("w-full")
            .props("use-chips")
        )
        reps = ui.number(t("suites.repetitions"), value=run.repetitions, min=1, max=50).classes("w-full")
        current = ui.switch(t("rerun.use_current"), value=False)
        ui.label(t("rerun.use_current_hint")).classes("text-caption text-grey-7")

        def sync() -> None:
            slugs.set_visibility(scope.value == "tasks")
            models.set_visibility(scope.value == "tasks")
            picked.set_visibility(scope.value == "results")

        scope.on_value_change(lambda _: sync())
        sync()

        def go() -> None:
            try:
                if scope.value == "failed":
                    if not failed_ids:
                        raise ValueError(t("rerun.no_failed"))
                    new = c.runner.create_rerun(
                        run.id,
                        scope="results",
                        result_ids=failed_ids,
                        use_current_versions=bool(current.value),
                        repetitions=1,
                    )
                elif scope.value == "results":
                    new = c.runner.create_rerun(
                        run.id,
                        scope="results",
                        result_ids=list(picked.value or []),
                        use_current_versions=bool(current.value),
                        repetitions=int(reps.value or 1),
                    )
                elif scope.value == "tasks":
                    new = c.runner.create_rerun(
                        run.id,
                        scope="tasks",
                        task_slugs=list(slugs.value or []),
                        model_names=list(models.value or []),
                        use_current_versions=bool(current.value),
                        repetitions=int(reps.value or 1),
                    )
                else:
                    new = c.runner.create_rerun(
                        run.id, scope="full", use_current_versions=bool(current.value), repetitions=int(reps.value or 1)
                    )
                c.runner.start(new.id)
                dialog.close()
                ui.navigate.to(f"/runs/{new.id}")
            except Exception as exc:
                notify_error(exc)

        with ui.row().classes("w-full justify-end"):
            ui.button(t("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(t("rerun.start"), on_click=go, icon="replay")
    dialog.open()


# ---------------------------------------------------------------------------------------------- list


@ui.refreshable
def runs_table() -> None:
    c = ctx()
    runs = _runs()
    names = {r.id: r.name for r in runs}
    rows = []
    for r in runs:
        running = c.runner.is_running(r.id)
        status = RunStatus.RUNNING.value if running else r.status.value
        rows.append(
            {
                "id": r.id,
                "name": r.name,
                "status": t(f"status.{status}"),
                "progress": f"{r.done_items}/{r.total_items}",
                "tasks": len(r.snapshot.tasks),
                "models": ", ".join(m.name for m in r.snapshot.models),
                "rerun_of": names.get(r.rerun_of or "", "—") if r.rerun_of else "—",
                "created": fmt_dt(r.created_at),
            }
        )
    columns: list[dict[str, Any]] = [
        {"name": "actions", "label": "", "field": "id"},
        {"name": "name", "label": t("common.name"), "field": "name", "align": "left"},
        {"name": "status", "label": t("common.status"), "field": "status"},
        {"name": "progress", "label": t("runs.progress"), "field": "progress"},
        {"name": "tasks", "label": t("suites.tasks"), "field": "tasks"},
        {"name": "models", "label": t("suites.models"), "field": "models", "align": "left"},
        {"name": "rerun_of", "label": t("runs.rerun_of"), "field": "rerun_of", "align": "left"},
        {"name": "created", "label": t("common.created"), "field": "created", "sortable": True},
    ]
    table = ui.table(columns=columns, rows=rows, row_key="id", pagination=25).classes("w-full")
    table.add_slot(
        "body-cell-actions",
        action_slot(
            [
                ("open_in_new", "open", t("common.open")),
                ("replay", "rerun", t("runs.rerun")),
                ("delete", "delete", t("common.delete")),
            ]
        ),
    )
    table.on("open", lambda e: ui.navigate.to(f"/runs/{e.args['id']}"))

    def on_rerun(e: Any) -> None:
        run = c.repo.get(Run, e.args["id"])
        if run:
            rerun_dialog(run)

    def on_delete(e: Any) -> None:
        if c.runner.is_running(e.args["id"]):
            ui.notify(t("runs.cancel_first"), type="warning")
            return
        confirm(
            t("runs.confirm_delete", name=e.args["name"]),
            lambda: (c.runner.delete_run(e.args["id"]), runs_table.refresh()),
        )

    table.on("rerun", on_rerun)
    table.on("delete", on_delete)


def list_page() -> None:
    with frame("nav.runs"), section(t("runs.title"), t("runs.caption")):
        with ui.row():
            ui.button(t("runs.new_from_suite"), icon="playlist_play", on_click=lambda: ui.navigate.to("/suites"))
            ui.button(t("common.refresh"), icon="refresh", on_click=runs_table.refresh).props("flat")
        runs_table()

        def auto_refresh() -> None:
            if any(ctx().runner.is_running(r.id) for r in _runs()):
                runs_table.refresh()

        ui.timer(3.0, auto_refresh)


# ---------------------------------------------------------------------------------------------- detail


def export_results(run: Run, results: list[Result], fmt: str) -> None:
    if fmt == "json":
        data = {"run": run.model_dump(mode="json"), "results": [r.model_dump(mode="json") for r in results]}
        ui.download.content(json.dumps(data, indent=2, ensure_ascii=False), f"run-{run.id}.json", "application/json")
        return
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "task",
            "model",
            "rep",
            "status",
            "final",
            "auto",
            "judge",
            "user",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "latency_ms",
            "ttft_ms",
            "cost_usd",
            "error",
        ]
    )
    for r in results:
        m = r.metrics
        w.writerow(
            [
                r.task_slug,
                r.model_name,
                r.repetition,
                r.status.value,
                r.score.final,
                r.score.auto,
                r.score.judge,
                r.score.user,
                m.input_tokens,
                m.output_tokens,
                m.reasoning_tokens,
                round(m.latency_ms),
                m.ttft_ms,
                m.cost_usd,
                r.error or "",
            ]
        )
    ui.download.content(buf.getvalue(), f"run-{run.id}.csv", "text/csv")


def detail_page(run_id: str) -> None:
    c = ctx()
    run = c.repo.get(Run, run_id)
    with frame("runs.detail"):
        if run is None:
            ui.label(t("common.not_found"))
            return
        with ui.row().classes("items-center w-full"):
            ui.label(run.name).classes("text-h6")
            status_holder = ui.row()
            ui.space()

            def start() -> None:
                c.runner.start(run.id)
                ui.notify(t("runs.started"))

            ui.button(t("runs.resume"), icon="play_arrow", on_click=start).bind_visibility_from(
                run, "status", backward=lambda s: s not in (RunStatus.COMPLETED,)
            )
            ui.button(t("runs.cancel"), icon="stop", color="negative", on_click=lambda: c.runner.cancel(run.id)).props(
                "flat"
            )
            ui.button(t("runs.rerun"), icon="replay", on_click=lambda: rerun_dialog(run)).props("flat")
            ui.button(
                "CSV",
                icon="download",
                on_click=lambda: export_results(run, c.repo.find(Result, where={"run_id": run.id}), "csv"),
            ).props("flat")
            ui.button(
                "JSON",
                icon="data_object",
                on_click=lambda: export_results(run, c.repo.find(Result, where={"run_id": run.id}), "json"),
            ).props("flat")
            if backfill.candidates(c, run):

                async def estimate() -> None:
                    try:
                        ui.notify(t("runs.estimating"), timeout=2000)
                        n = await backfill.estimate_anthropic_reasoning(c, run)
                        ui.notify(t("runs.estimated", n=n), type="positive")
                        ui.navigate.reload()
                    except Exception as exc:
                        notify_error(exc)

                ui.button(t("runs.estimate_reasoning"), icon="psychology", on_click=estimate).props("flat").tooltip(
                    t("runs.estimate_reasoning_tip")
                )
        if run.rerun_of:
            orig = c.repo.get(Run, run.rerun_of)
            with ui.row().classes("items-center text-caption"):
                ui.icon("replay")
                ui.label(
                    t(
                        "runs.rerun_info",
                        scope=t(f"rerun.scope_{run.rerun_scope}"),
                        mode=t(f"rerun.mode_{run.snapshot_mode}"),
                    )
                )
                if orig:
                    ui.link(orig.name, f"/runs/{orig.id}")
                    ui.link(t("runs.compare_with_original"), f"/compare?runs={orig.id},{run.id}")
        info = ui.label().classes("text-caption text-grey-7")
        progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
        live_box = ui.column().classes("w-full")

        @ui.refreshable
        def body() -> None:
            results = c.repo.find(Result, where={"run_id": run.id})
            render_body(run, results)

        body()
        last = {"done": -1, "running": None}

        def tick() -> None:
            fresh = c.repo.get(Run, run.id)
            if fresh is None:
                return
            running = c.runner.is_running(run.id)
            status_holder.clear()
            with status_holder:
                status_badge(RunStatus.RUNNING.value if running else fresh.status.value)
            total = max(fresh.total_items, 1)
            progress.value = fresh.done_items / total
            info.text = t(
                "runs.info",
                done=fresh.done_items,
                total=fresh.total_items,
                started=fmt_dt(fresh.started_at),
                finished=fmt_dt(fresh.finished_at),
            )
            live = c.runner.live.get(run.id)
            live_box.clear()
            if live and running:
                with live_box:
                    if live.active:
                        ui.label(t("runs.active", items=" | ".join(live.active.values()))).classes("text-caption")
                    ui.code("\n".join(list(live.events)[-12:]), language="text").classes("w-full text-xs")
            if fresh.done_items != last["done"] or running != last["running"]:
                last["done"], last["running"] = fresh.done_items, running
                run.status = fresh.status
                body.refresh()

        ui.timer(1.0, tick)


def render_body(run: Run, results: list[Result]) -> None:
    aggs = aggregate_by_model(results)
    done = [r for r in results if r.status == ResultStatus.DONE]
    with ui.row().classes("w-full"):
        kpi(t("runs.kpi_results"), f"{len(done)}/{len(results)}", "task_alt")
        kpi(t("runs.kpi_cost"), fmt_cost(sum(r.metrics.cost_usd for r in results)), "payments", "orange")
        kpi(
            t("runs.kpi_tokens"),
            fmt_num(sum(r.metrics.input_tokens + r.metrics.output_tokens for r in results)),
            "token",
            "teal",
        )
        kpi(t("runs.kpi_review"), str(sum(1 for r in done if not r.score.reviewed)), "rate_review", "purple")
    if not results:
        return
    with ui.row().classes("w-full items-start no-wrap max-lg:flex-wrap"):
        with ui.card().classes("flex-[3] min-w-[480px] overflow-auto"):
            ui.label(t("runs.by_model")).classes("text-subtitle1")
            rows = [
                {
                    "model": a.model_name,
                    "final": fmt_num(a.mean_final),
                    "reliability": fmt_num(a.dims.get("reliability")),
                    "performance": fmt_num(a.dims.get("performance")),
                    "conformity": fmt_num(a.dims.get("conformity")),
                    "intelligence": fmt_num(a.dims.get("intelligence")),
                    "success": f"{a.success_rate:.0%}",
                    "pass": f"{a.pass_at_k:.0%}" if a.pass_at_k is not None else "—",
                    "p50": fmt_ms(a.latency_p50_ms),
                    "tps": fmt_num(a.tokens_per_s, 1),
                    "tokens": f"{fmt_num(a.input_tokens)} / {fmt_num(a.output_tokens)}",
                    "reasoning": ("≈ " if a.reasoning_estimated else "") + fmt_num(a.reasoning_tokens),
                    "cost": fmt_cost(a.cost_usd),
                }
                for a in aggs
            ]
            cols = [
                ("model", "common.model"),
                ("final", "score.final"),
                ("reliability", "dim.reliability"),
                ("performance", "dim.performance"),
                ("conformity", "dim.conformity"),
                ("intelligence", "dim.intelligence"),
                ("success", "runs.success"),
                ("pass", "runs.pass_at_k"),
                ("p50", "runs.latency_p50"),
                ("tps", "runs.tps"),
                ("tokens", "runs.tokens_io"),
                ("reasoning", "runs.reasoning_tokens"),
                ("cost", "runs.cost"),
            ]
            ui.table(
                columns=[
                    {"name": k, "label": t(lbl), "field": k, "align": "left" if k == "model" else "right"}
                    for k, lbl in cols
                ],
                rows=rows,
                row_key="model",
            ).classes("w-full").props("dense flat")
        with ui.card().classes("flex-[2] min-w-[320px]"):
            ui.label(t("runs.dimensions")).classes("text-subtitle1")
            ui.echart(radar([(a.model_name, a) for a in aggs])).classes("w-full h-80")
    matrix(run, results)


MATRIX_METRICS = ["score", "tokens", "reasoning", "latency", "cost"]
_matrix_metric: dict[str, str] = {"value": "score"}


def matrix(run: Run, results: list[Result]) -> None:
    """Task × model grid; each cell shows the chosen metric per repetition and links to its result."""
    with ui.card().classes("w-full overflow-auto"):
        with ui.row().classes("items-center w-full"):
            ui.label(t("runs.matrix")).classes("text-subtitle1")
            ui.space()
            ui.toggle(
                {k: t(f"runs.cell_{k}") for k in MATRIX_METRICS},
                value=_matrix_metric["value"],
                on_change=lambda e: (_matrix_metric.update(value=e.value), grid.refresh()),
            ).props("dense no-caps")
        ui.label(t("runs.matrix_hint")).classes("text-caption text-grey-7")

        @ui.refreshable
        def grid() -> None:
            matrix_table(run, results, _matrix_metric["value"])

        grid()


def matrix_table(run: Run, results: list[Result], metric: str) -> None:
    models = [m.name for m in run.snapshot.models]
    with ui.element("table").classes("w-full text-sm border-collapse"):
        with ui.element("thead"), ui.element("tr"):
            with ui.element("th").classes("text-left p-2 border-b"):
                ui.label(t("common.task"))
            for m in models:
                with ui.element("th").classes("p-2 border-b"):
                    ui.label(m)
        with ui.element("tbody"):
            for task in run.snapshot.tasks:
                with ui.element("tr").classes("hover:bg-gray-100 dark:hover:bg-neutral-800"):
                    with ui.element("td").classes("p-2 border-b"):
                        ui.label(task.slug).classes("font-mono text-xs")
                        ui.label(task.category).classes("text-caption text-grey-6")
                    for m in models:
                        cell = [r for r in results if r.task_slug == task.slug and r.model_name == m]
                        with (
                            ui.element("td").classes("p-2 border-b text-center"),
                            ui.row().classes("justify-center gap-1"),
                        ):
                            for r in sorted(cell, key=lambda r: r.repetition):
                                result_chip(r, metric)


def _metric_label(r: Result, metric: str) -> str:
    m = r.metrics
    if metric == "tokens":
        return f"{fmt_k(m.input_tokens)}→{fmt_k(m.output_tokens)}"
    if metric == "reasoning":
        return (("≈" if m.reasoning_estimated else "") + fmt_k(m.reasoning_tokens)) if m.reasoning_tokens else "—"
    if metric == "latency":
        return fmt_ms(m.latency_ms)
    if metric == "cost":
        return fmt_cost(m.cost_usd)
    return fmt_num(r.score.final, 1) if r.score.final is not None else "—"


def fmt_k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def result_chip(r: Result, metric: str = "score") -> None:
    status = r.status.value
    if status in ("pending", "running"):
        label = "…"
        color = "grey-5" if status == "pending" else "info"
    elif status == "done":
        label = _metric_label(r, metric)
        color = score_color(r.score.final)
    else:
        label = t(f"status.{status}")[:6]
        color = "grey-6" if status == "skipped" else "negative"
    chip = ui.button(label, on_click=lambda r=r: ui.navigate.to(f"/results/{r.id}")).props(
        f"dense unelevated size=sm color={color}" + ("" if r.score.reviewed else " outline")
    )
    tip = f"#{r.repetition + 1} {t('status.' + status)}"
    if r.score.auto is not None:
        tip += f" · auto {r.score.auto}"
    if r.score.judge is not None:
        tip += f" · judge {r.score.judge}"
    if r.score.user is not None:
        tip += f" · user {r.score.user}"
    m = r.metrics
    if status == "done":
        tip += (
            f" · {t('metric.input_tokens')} {m.input_tokens:,} · {t('metric.output_tokens')} {m.output_tokens:,}"
            f" · {t('metric.reasoning_tokens')} {m.reasoning_tokens:,}"
            f" · {fmt_ms(m.latency_ms)} · {fmt_cost(m.cost_usd)}"
        )
    if r.error:
        tip += f" · {r.error[:120]}"
    chip.tooltip(tip)


# ---------------------------------------------------------------------------------------------- result detail


def result_page(result_id: str) -> None:
    c = ctx()
    result = c.repo.get(Result, result_id)
    with frame("results.detail"):
        if result is None:
            ui.label(t("common.not_found"))
            return
        run = c.repo.get(Run, result.run_id)
        task: Task | None = next((x for x in run.snapshot.tasks if x.id == result.task_id), None) if run else None
        with ui.row().classes("items-center w-full"):
            ui.label(f"{result.task_slug} · {result.model_name} #{result.repetition + 1}").classes("text-h6")
            status_badge(result.status.value)
            ui.space()
            if run:
                ui.link(t("results.back_to_run", name=run.name), f"/runs/{run.id}")
        if result.error:
            ui.label(result.error).classes("text-negative")
        m = result.metrics
        with ui.row().classes("w-full"):
            kpi(t("metric.input_tokens"), fmt_num(m.input_tokens), "input", "teal")
            kpi(t("metric.output_tokens"), fmt_num(m.output_tokens), "output", "teal")
            kpi(
                t("metric.reasoning_tokens"),
                (("≈ " if m.reasoning_estimated else "") + fmt_num(m.reasoning_tokens)) if m.reasoning_tokens else "—",
                "psychology",
                "purple",
            )
            kpi(t("metric.latency"), fmt_ms(m.latency_ms), "timer", "orange")
            kpi(t("metric.ttft"), fmt_ms(m.ttft_ms), "bolt", "orange")
            kpi(t("metric.tps"), fmt_num(m.output_tokens_per_s, 1), "speed", "blue-grey")
            kpi(t("metric.cost"), fmt_cost(m.cost_usd), "payments", "orange")
        with ui.row().classes("w-full items-start no-wrap max-lg:flex-wrap"):
            with ui.column().classes("flex-[3] min-w-[420px]"):
                with ui.tabs() as tabs:
                    tab_resp = ui.tab(t("results.response"))
                    tab_raw = ui.tab(t("results.raw"))
                    tab_reason = ui.tab(t("results.reasoning"))
                    tab_task = ui.tab(t("common.task"))
                    tab_tr = ui.tab(t("results.transcript"))
                with ui.tab_panels(tabs, value=tab_resp).classes("w-full"):
                    with ui.tab_panel(tab_resp):
                        ui.markdown(
                            display_markdown(result.response_text) or "_(empty)_",
                            extras=["fenced-code-blocks", "tables"],
                        ).classes("w-full max-h-[70vh] overflow-auto")
                    with ui.tab_panel(tab_raw):
                        ui.code(result.response_text or "", language="markdown").classes(
                            "w-full max-h-[70vh] overflow-auto"
                        )
                    with ui.tab_panel(tab_reason):
                        ui.code(result.reasoning_text or t("results.no_reasoning"), language="text").classes(
                            "w-full max-h-[70vh] overflow-auto"
                        )
                    with ui.tab_panel(tab_task):
                        if task:
                            ui.code(task.prompt, language="markdown").classes("w-full max-h-[60vh] overflow-auto")
                            if task.reference_answer:
                                with ui.expansion(t("tasks.reference"), icon="visibility"):
                                    ui.markdown(task.reference_answer)
                    with ui.tab_panel(tab_tr):
                        if result.transcript:
                            ui.code(
                                json.dumps(result.transcript, indent=1, ensure_ascii=False)[:200_000], language="json"
                            ).classes("w-full max-h-[70vh] overflow-auto")
                        else:
                            ui.label(t("results.no_transcript"))
                artifacts_panel(result)
            with ui.column().classes("flex-[2] min-w-[340px]"):
                scoring_card(result, task)
                metrics_card(result)
                checks_card(result)
                judge_card(result)


def scoring_card(result: Result, task: Task | None) -> None:
    c = ctx()
    with ui.card().classes("w-full"):
        ui.label(t("results.scoring")).classes("text-subtitle1")
        with ui.row().classes("gap-4"):
            for key, val in (
                ("auto", result.score.auto),
                ("judge", result.score.judge),
                ("user", result.score.user),
                ("final", result.score.final),
            ):
                with ui.column().classes("items-center gap-0"):
                    ui.label(fmt_num(val, 1)).classes(f"text-h5 text-{score_color(val)}")
                    ui.label(t(f"score.{key}")).classes("text-caption")
        ui.label(t("results.user_hint")).classes("text-caption text-grey-7")
        initial = result.score.user if result.score.user is not None else (result.score.judge or result.score.auto or 5)
        slider = ui.slider(min=1, max=10, step=0.5, value=max(1.0, float(initial))).props("label-always markers")
        comment = ui.textarea(t("results.comment"), value=result.score.user_comment).classes("w-full").props("autogrow")

        def save() -> None:
            try:
                c.runner.set_user_score(result.id, float(slider.value), comment.value or "")
                ui.notify(t("common.saved"), type="positive")
                ui.navigate.reload()
            except Exception as exc:
                notify_error(exc)

        def clear() -> None:
            c.runner.set_user_score(result.id, None, comment.value or "")
            ui.navigate.reload()

        async def reevaluate() -> None:
            ui.notify(t("results.reevaluating"))
            try:
                await c.runner.reevaluate(result.id)
                ui.navigate.reload()
            except Exception as exc:
                notify_error(exc)

        def rerun_this() -> None:
            run = c.repo.get(Run, result.run_id)
            if run:
                rerun_dialog(run, preselect_results=[result.id])

        with ui.row():
            ui.button(t("results.save_score"), icon="check", on_click=save)
            if result.score.judge is not None:
                ui.button(
                    t("results.accept_judge"),
                    icon="gavel",
                    on_click=lambda: (c.runner.accept_judge(result.id), ui.navigate.reload()),
                ).props("flat")
            ui.button(t("results.clear_score"), on_click=clear).props("flat")
        with ui.row():
            ui.button(t("results.reevaluate"), icon="fact_check", on_click=reevaluate).props("flat")
            ui.button(t("results.rerun_this"), icon="replay", on_click=rerun_this).props("flat")


def metrics_card(result: Result) -> None:
    m = result.metrics
    with ui.card().classes("w-full"):
        ui.label(t("results.metrics")).classes("text-subtitle1")
        rows = [
            (t("metric.input_tokens"), fmt_num(m.input_tokens)),
            (t("metric.output_tokens"), fmt_num(m.output_tokens)),
            (t("metric.reasoning_tokens"), fmt_num(m.reasoning_tokens)),
            (t("metric.cached_tokens"), fmt_num(m.cached_tokens)),
            (t("metric.latency"), fmt_ms(m.latency_ms)),
            (t("metric.ttft"), fmt_ms(m.ttft_ms)),
            (t("metric.tps"), fmt_num(m.output_tokens_per_s, 1)),
            (t("metric.cost"), fmt_cost(m.cost_usd)),
            (t("metric.retries"), str(m.retries)),
            (t("metric.turns"), f"{m.turns} / {m.tool_calls} {t('metric.tool_calls')}"),
            (t("metric.finish_reason"), result.finish_reason or "—"),
        ]
        with ui.grid(columns=2).classes("w-full gap-x-4 gap-y-1 text-sm"):
            for k, v in rows:
                ui.label(k).classes("text-grey-7")
                ui.label(v).classes("text-right font-mono")


def checks_card(result: Result) -> None:
    with ui.card().classes("w-full"):
        ui.label(t("results.checks")).classes("text-subtitle1")
        if not result.checks:
            ui.label(t("results.no_checks")).classes("text-grey-7")
        for ch in result.checks:
            icon = "skip_next" if ch.skipped else ("check_circle" if ch.passed else "cancel")
            color = "grey" if ch.skipped else ("positive" if ch.passed else ("warning" if ch.score > 0 else "negative"))
            with ui.expansion(f"{ch.name} — {ch.score:.0%}", icon=icon).classes(f"w-full text-{color}"):
                ui.label(
                    f"{t('check.dimension')}: {t('dim.' + ch.dimension)} · {t('check.weight')}: {ch.weight}"
                    f" · {fmt_ms(ch.duration_ms)}"
                ).classes("text-caption")
                ui.code(ch.detail or "", language="text").classes("w-full text-xs")


def judge_card(result: Result) -> None:
    j = result.judge
    with ui.card().classes("w-full"):
        ui.label(t("results.judge")).classes("text-subtitle1")
        if j is None:
            ui.label(t("results.no_judge")).classes("text-grey-7")
            return
        if j.same_model_warning:
            ui.label(t("results.same_model_warning")).classes("text-warning text-caption")
        if j.error:
            ui.label(j.error).classes("text-negative text-caption")
        ui.label(f"{j.judge_model_name}: {fmt_num(j.score, 1)} / 10").classes("font-medium")
        for cr in j.criteria:
            ui.label(f"• {cr.name}: {fmt_num(cr.score, 1)} — {cr.comment}").classes("text-sm")
        if j.rationale:
            ui.markdown(j.rationale).classes("text-sm")
        ui.label(
            t(
                "results.judge_cost",
                tokens=j.metrics.input_tokens + j.metrics.output_tokens,
                cost=fmt_cost(j.metrics.cost_usd),
            )
        ).classes("text-caption text-grey-7")


def artifacts_panel(result: Result) -> None:
    c = ctx()
    arts = [a for a in (c.repo.get(Artifact, i) for i in result.artifact_ids) if a is not None]
    with ui.card().classes("w-full"):
        ui.label(t("results.artifacts")).classes("text-subtitle1")
        if not arts:
            ui.label(t("results.no_artifacts")).classes("text-grey-7")
            return
        from aidriven.ui.pages.artifacts import artifact_viewer

        for art in arts:
            with ui.expansion(
                f"{art.name} · {art.kind.value} · {fmt_num(art.size)} B · {art.storage}", icon="description"
            ).classes("w-full"):
                artifact_viewer(art)


__all__ = ["detail_page", "list_page", "result_page", "sandboxed_iframe"]

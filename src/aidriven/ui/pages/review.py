"""Review queue: go through results, see the judge preview, set the final 1-10 score quickly."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from aidriven.domain.models import Result, ResultStatus, Run
from aidriven.ui.common import ctx, display_markdown, fmt_dt, fmt_num, frame, notify_error, score_color, section
from aidriven.ui.i18n import t


def page() -> None:
    c = ctx()
    with frame("nav.review"), section(t("review.title"), t("review.caption")):
        runs = c.repo.find(Run)
        state: dict[str, Any] = {"run": runs[0].id if runs else None, "only_pending": True, "idx": 0}
        with ui.row().classes("items-end w-full"):
            ui.select(
                {r.id: f"{r.name} · {fmt_dt(r.created_at)}" for r in runs},
                label=t("common.run"),
                value=state["run"],
                on_change=lambda e: (state.update(run=e.value, idx=0), queue.refresh()),
            ).classes("w-full max-w-xl")
            ui.switch(
                t("review.only_pending"),
                value=True,
                on_change=lambda e: (state.update(only_pending=e.value, idx=0), queue.refresh()),
            )

        @ui.refreshable
        def queue() -> None:
            if not state["run"]:
                ui.label(t("review.empty"))
                return
            items = [r for r in c.repo.find(Result, where={"run_id": state["run"]}) if r.status == ResultStatus.DONE]
            items.sort(key=lambda r: (r.task_slug, r.model_name, r.repetition))
            if state["only_pending"]:
                items = [r for r in items if not r.score.reviewed]
            reviewed = sum(1 for r in c.repo.find(Result, where={"run_id": state["run"]}) if r.score.reviewed)
            ui.label(t("review.progress", pending=len(items), reviewed=reviewed)).classes("text-caption")
            if not items:
                ui.label(t("review.all_done")).classes("text-positive")
                return
            idx = min(state["idx"], len(items) - 1)
            r = items[idx]
            render_item(r, idx, len(items), state, queue.refresh)

        queue()


def render_item(r: Result, idx: int, total: int, state: dict[str, Any], refresh: Any) -> None:
    c = ctx()
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center w-full"):
            ui.label(f"{idx + 1}/{total}").classes("text-caption")
            ui.label(f"{r.task_slug} · {r.model_name} #{r.repetition + 1}").classes("text-subtitle1 font-medium")
            ui.space()
            ui.link(t("review.open_full"), f"/results/{r.id}", new_tab=True)
        with ui.row().classes("w-full items-start no-wrap max-lg:flex-wrap"):
            with ui.column().classes("flex-[3] min-w-[400px]"):
                ui.markdown(display_markdown(r.response_text) or "_(empty)_", extras=["fenced-code-blocks"]).classes(
                    "w-full max-h-[55vh] overflow-auto border rounded p-2"
                )
            with ui.column().classes("flex-[2] min-w-[300px]"):
                with ui.row().classes("gap-6"):
                    for key, val in (("auto", r.score.auto), ("judge", r.score.judge)):
                        with ui.column().classes("items-center gap-0"):
                            ui.label(fmt_num(val, 1)).classes(f"text-h5 text-{score_color(val)}")
                            ui.label(t(f"score.{key}")).classes("text-caption")
                failed = [ch for ch in r.checks if not ch.passed and not ch.skipped]
                ui.label(
                    t("review.checks_summary", passed=sum(ch.passed for ch in r.checks), total=len(r.checks))
                ).classes("text-caption")
                for ch in failed[:5]:
                    ui.label(f"✖ {ch.name}: {ch.detail.splitlines()[0] if ch.detail else ''}").classes(
                        "text-caption text-negative"
                    )
                if r.judge and r.judge.rationale:
                    ui.label(t("results.judge")).classes("text-caption text-grey-7")
                    ui.label(r.judge.rationale).classes("text-sm")
                initial = r.score.user or r.score.judge or r.score.auto or 5
                slider = ui.slider(min=1, max=10, step=0.5, value=max(1.0, float(initial))).props(
                    "label-always markers"
                )
                comment = ui.input(t("results.comment"), value=r.score.user_comment).classes("w-full")

                def save(value: float | None = None) -> None:
                    try:
                        c.runner.set_user_score(
                            r.id, float(value if value is not None else slider.value), comment.value or ""
                        )
                        if not state["only_pending"]:
                            state["idx"] = idx + 1
                        refresh()
                    except Exception as exc:
                        notify_error(exc)

                with ui.row():
                    ui.button(t("review.save_next"), icon="check", on_click=lambda: save(None))
                    if r.score.judge is not None:
                        ui.button(t("results.accept_judge"), icon="gavel", on_click=lambda: save(r.score.judge)).props(
                            "flat"
                        )
                    ui.button(t("review.skip"), on_click=lambda: (state.update(idx=idx + 1), refresh())).props("flat")
                    if idx > 0:
                        ui.button(t("review.previous"), on_click=lambda: (state.update(idx=idx - 1), refresh())).props(
                            "flat"
                        )

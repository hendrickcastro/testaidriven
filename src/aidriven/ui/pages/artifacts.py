"""Artifacts: browse, view, download and replay model outputs in the sandbox (never on the host)."""

from __future__ import annotations

from typing import Any

from nicegui import ui

from aidriven.domain.models import Artifact, ArtifactKind, Result, Run
from aidriven.ports import SandboxJob
from aidriven.services.agent import HTML_PROBE
from aidriven.services.checks import harness_source
from aidriven.ui.common import ctx, fmt_dt, fmt_ms, fmt_num, frame, notify_error, sandboxed_iframe, section
from aidriven.ui.i18n import t

LANG = {
    ArtifactKind.PYTHON: "python",
    ArtifactKind.HTML: "html",
    ArtifactKind.JAVASCRIPT: "javascript",
    ArtifactKind.JSON: "json",
    ArtifactKind.SQL: "sql",
    ArtifactKind.MARKDOWN: "markdown",
}


def load_content(art: Artifact) -> str:
    return ctx().artifact_store_for(art.storage).get(art.location).decode("utf-8", errors="replace")


def sibling_files(art: Artifact) -> dict[str, bytes]:
    """All artifacts of the same result, so multi-file programs replay together."""
    c = ctx()
    files: dict[str, bytes] = {}
    for other in c.repo.find(Artifact, where={"result_id": art.result_id}):
        try:
            files[other.name] = c.artifact_store_for(other.storage).get(other.location)
        except Exception:
            continue
    return files


def artifact_viewer(art: Artifact) -> None:
    c = ctx()
    try:
        content = load_content(art)
    except Exception as exc:
        ui.label(f"{t('artifacts.load_failed')}: {exc}").classes("text-negative")
        return
    holder: dict[str, Any] = {}

    async def run_python() -> None:
        output = holder["out"]
        output.clear()
        with output:
            ui.spinner()
        try:
            rec = await c.sandbox.run(
                SandboxJob(
                    files=sibling_files(art), command=["python", art.name], timeout_s=c.config.sandbox.default_timeout_s
                )
            )
            output.clear()
            with output:
                ui.label(
                    t(
                        "artifacts.exec_summary",
                        code=rec.exit_code,
                        ms=fmt_ms(rec.duration_ms),
                        iso=rec.isolation,
                        timeout=t("common.yes") if rec.timed_out else t("common.no"),
                    )
                ).classes("text-caption")
                if rec.isolation == "process":
                    ui.label(t("artifacts.process_warning")).classes("text-caption text-warning")
                ui.code(rec.stdout or "(no stdout)", language="text").classes("w-full text-xs")
                if rec.stderr:
                    ui.code(rec.stderr, language="text").classes("w-full text-xs text-negative")
        except Exception as exc:
            output.clear()
            notify_error(exc)

    async def render_html() -> None:
        output = holder["out"]
        output.clear()
        with output:
            ui.spinner()
        try:
            files = sibling_files(art)
            files["aidriven_harness.py"] = harness_source()
            files["_probe.py"] = (f"NAME = {art.name!r}\n" + HTML_PROBE).encode()
            rec = await c.sandbox.run(
                SandboxJob(
                    files=files,
                    command=["python", "_probe.py"],
                    timeout_s=60,
                    needs_browser=True,
                    collect=["_screenshot.png"],
                )
            )
            output.clear()
            with output:
                if "AIDRIVEN_SKIP" in rec.stdout:
                    ui.label(t("artifacts.no_browser")).classes("text-warning")
                if rec.screenshot_b64:
                    ui.image(f"data:image/png;base64,{rec.screenshot_b64}").classes("w-full border")
                ui.code((rec.stdout or "") + ("\n" + rec.stderr if rec.stderr else ""), language="text").classes(
                    "w-full text-xs"
                )
        except Exception as exc:
            output.clear()
            notify_error(exc)

    with ui.row():
        ui.button(
            t("common.download"),
            icon="download",
            on_click=lambda: ui.download.content(content.encode("utf-8"), art.name, art.mime),
        ).props("flat dense")
        if art.kind == ArtifactKind.PYTHON:
            ui.button(t("artifacts.run_sandbox"), icon="play_arrow", on_click=run_python).props("flat dense")
        if art.kind == ArtifactKind.HTML:
            ui.button(t("artifacts.render_headless"), icon="photo_camera", on_click=render_html).props("flat dense")
    if art.kind == ArtifactKind.HTML:
        with ui.tabs() as tabs:
            tab_prev = ui.tab(t("artifacts.preview"))
            tab_src = ui.tab(t("artifacts.source"))
        with ui.tab_panels(tabs, value=tab_prev).classes("w-full"):
            with ui.tab_panel(tab_prev):
                ui.label(t("artifacts.iframe_note")).classes("text-caption text-grey-7")
                sandboxed_iframe(content)
            with ui.tab_panel(tab_src):
                ui.code(content, language="html").classes("w-full max-h-[60vh] overflow-auto")
    else:
        ui.code(content[:300_000], language=LANG.get(art.kind, "text")).classes("w-full max-h-[60vh] overflow-auto")
    holder["out"] = ui.column().classes("w-full")


def page() -> None:
    c = ctx()
    with frame("nav.artifacts"), section(t("artifacts.title"), t("artifacts.caption")):
        runs = c.repo.find(Run)
        state: dict[str, Any] = {"run": runs[0].id if runs else None}
        ui.select(
            {r.id: f"{r.name} · {fmt_dt(r.created_at)}" for r in runs},
            label=t("common.run"),
            value=state["run"],
            on_change=lambda e: (state.update(run=e.value), listing.refresh()),
        ).classes("w-full max-w-xl")

        @ui.refreshable
        def listing() -> None:
            if not state["run"]:
                ui.label(t("artifacts.none"))
                return
            arts = c.repo.find(Artifact, where={"run_id": state["run"]})
            results = {r.id: r for r in c.repo.find(Result, where={"run_id": state["run"]})}
            if not arts:
                ui.label(t("artifacts.none"))
            for art in sorted(
                arts, key=lambda a: (results[a.result_id].task_slug if a.result_id in results else "", a.name)
            ):
                r = results.get(art.result_id)
                who = f"{r.task_slug} · {r.model_name} #{r.repetition + 1}" if r else "?"
                title = f"{who} — {art.name}"
                with ui.expansion(f"{title} ({fmt_num(art.size)} B · {art.storage})", icon="description").classes(
                    "w-full"
                ):
                    artifact_viewer(art)

        listing()

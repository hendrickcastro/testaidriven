"""NiceGUI application: builds the AppContext, bootstraps data and registers the pages."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from nicegui import app, ui

from aidriven.config import EnvSettings
from aidriven.context import AppContext
from aidriven.logging_setup import setup_logging
from aidriven.services import model_intel
from aidriven.services.bootstrap import bootstrap
from aidriven.ui import common
from aidriven.ui.i18n import set_language

log = logging.getLogger(__name__)


def register_pages() -> None:
    from aidriven.ui.pages import (
        artifacts,
        compare,
        dashboard,
        logs,
        models,
        review,
        rules_agents,
        runs,
        settings,
        suites,
        tasks,
    )

    ui.page("/")(dashboard.page)
    ui.page("/models")(models.page)
    ui.page("/tasks")(tasks.page)
    ui.page("/rules")(rules_agents.page)
    ui.page("/suites")(suites.page)
    ui.page("/runs")(runs.list_page)
    ui.page("/runs/{run_id}")(runs.detail_page)
    ui.page("/results/{result_id}")(runs.result_page)
    ui.page("/review")(review.page)

    @ui.page("/compare")
    def _compare(runs: str | None = None) -> None:
        compare.page(runs)

    ui.page("/artifacts")(artifacts.page)
    ui.page("/settings")(settings.page)
    ui.page("/logs")(logs.page)


def build_context(env: EnvSettings | None = None) -> AppContext:
    env = env or EnvSettings()
    setup_logging(env.logs_dir)
    ctx = AppContext(env)
    try:
        bootstrap(ctx.repo)
    except Exception:
        log.exception("bootstrap failed")
    set_language(ctx.config.language)
    common.set_context(ctx)
    return ctx


def _launched_with_dash_m() -> bool:
    """`python -m aidriven` sets __main__.__spec__; NiceGUI can only auto-reload a script launched by path."""
    return getattr(sys.modules.get("__main__"), "__spec__", None) is not None


def run() -> None:
    env = EnvSettings()
    reload = env.reload
    if reload and _launched_with_dash_m():
        reload = False
        print("aidriven: auto-reload needs the script form: python src/aidriven/__main__.py (or ./run.sh dev)")
    ctx = build_context(env)
    register_pages()
    # Warm live model metadata (OpenRouter public list) in the background; offline is fine.
    app.on_startup(model_intel.load_index)
    app.on_shutdown(lambda: [ctx.runner.cancel(rid) for rid in list(ctx.runner.live)])
    log.info(
        "aidriven starting on http://%s:%d (backend=%s, sandbox=%s)",
        env.host,
        env.port,
        ctx.backend,
        ctx.sandbox.status()["active"],
    )
    ui.run(
        host=env.host,
        port=env.port,
        title="aidriven",
        reload=reload,
        uvicorn_reload_dirs=str(Path(__file__).resolve().parent),  # watch only the package, not .venv/data
        show=False,
        storage_secret=env.storage_secret,
        favicon="🧪",
        show_welcome_message=True,
    )

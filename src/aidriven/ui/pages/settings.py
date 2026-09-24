"""Settings: language, Firestore (upload service-account JSON, test, activate, migrate), cloud artifact
storage, judge model, sandbox, local data."""

from __future__ import annotations

from typing import Any

from nicegui import app, ui

from aidriven.domain.models import ModelProfile
from aidriven.ports import SandboxJob
from aidriven.ui.common import ctx, fmt_dt, fmt_ms, frame, notify_error, read_upload, section
from aidriven.ui.i18n import LANGUAGES, get_language, t

# Sandbox smoke test: Python version and whether outbound network is (correctly) blocked.
SANDBOX_PROBE = """import platform, socket
print(platform.python_version())
try:
    socket.create_connection(("1.1.1.1", 53), 2)
    print("network: OPEN")
except OSError:
    print("network: blocked")
"""


def page() -> None:
    c = ctx()
    cfg = c.config
    with frame("nav.settings"):
        # ------------------------------------------------------------------ general
        with ui.card().classes("w-full"), section(t("settings.general")):

            def set_lang(e: Any) -> None:
                cfg.language = e.value
                c.save_config()
                app.storage.user["lang"] = e.value
                ui.navigate.reload()

            ui.select(LANGUAGES, label=t("settings.language"), value=get_language(), on_change=set_lang).classes("w-60")
            ui.label(t("settings.data_dir", path=str(c.env.data_dir.resolve()))).classes("text-caption text-grey-7")

        # ------------------------------------------------------------------ firestore
        with ui.card().classes("w-full"), section(t("settings.firestore"), t("settings.firestore_caption")):
            fs = cfg.firestore

            @ui.refreshable
            def fs_status() -> None:
                with ui.row().classes("items-center gap-3"):
                    ui.badge(
                        t("settings.fs_active") if c.backend == "firestore" else t("settings.fs_inactive"),
                        color="positive" if c.backend == "firestore" else "grey",
                    )
                    if fs.project_id:
                        ui.label(t("settings.fs_project", project=fs.project_id))
                    if fs.last_test_at:
                        ok = fs.last_test_ok
                        ui.label(
                            t(
                                "settings.fs_last_test",
                                when=fs.last_test_at[:19].replace("T", " "),
                                result="✔" if ok else "✖",
                            )
                        ).classes("text-positive" if ok else "text-negative")
                if fs.last_test_detail:
                    ui.label(fs.last_test_detail).classes("text-caption text-grey-7")
                if not fs.credentials_path:
                    ui.label(t("settings.fs_no_credentials")).classes("text-caption text-warning")

            fs_status()

            async def on_upload(e: Any) -> None:
                try:
                    info = c.install_credentials(await read_upload(e))
                    ui.notify(
                        t("settings.fs_uploaded", project=info["project_id"], email=info["client_email"]),
                        type="positive",
                    )
                    fs_status.refresh()
                except Exception as exc:
                    notify_error(exc)

            ui.upload(on_upload=on_upload, auto_upload=True, label=t("settings.fs_upload")).props(
                "accept=.json"
            ).classes("w-full max-w-lg")
            ui.label(t("settings.fs_upload_hint")).classes("text-caption text-grey-7")
            db = ui.input(t("settings.fs_database"), value=fs.database_id).classes("w-60")

            async def test() -> None:
                fs.database_id = db.value or "(default)"
                c.save_config()
                ui.notify(t("settings.testing"), timeout=1500)
                ok, detail = await _in_thread(c.test_firestore)
                ui.notify(detail, type="positive" if ok else "negative", multi_line=True, timeout=10000)
                fs_status.refresh()

            async def activate() -> None:
                try:
                    await _in_thread(c.activate_firestore)
                    from aidriven.services.bootstrap import bootstrap

                    await _in_thread(lambda: bootstrap(c.repo))
                    ui.notify(t("settings.fs_activated"), type="positive")
                    ui.navigate.reload()
                except Exception as exc:
                    notify_error(exc)

            def deactivate() -> None:
                c.deactivate_firestore()
                ui.notify(t("settings.fs_deactivated"))
                ui.navigate.reload()

            async def migrate() -> None:
                try:
                    ui.notify(t("settings.fs_migrating"), timeout=2000)
                    counts = await _in_thread(c.migrate_local_to_firestore)
                    ui.notify(
                        t("settings.fs_migrated", counts=", ".join(f"{k}: {v}" for k, v in counts.items())),
                        type="positive",
                        multi_line=True,
                    )
                except Exception as exc:
                    notify_error(exc)

            with ui.row():
                ui.button(t("settings.fs_test"), icon="network_check", on_click=test)
                if c.backend == "firestore":
                    ui.button(t("settings.fs_deactivate"), icon="storage", on_click=deactivate).props("flat")
                    ui.button(t("settings.fs_migrate"), icon="cloud_upload", on_click=migrate).props("flat")
                else:
                    ui.button(t("settings.fs_activate"), icon="cloud_done", on_click=activate).props("flat")
            ui.label(t("settings.fs_rules_note")).classes("text-caption text-grey-7")

        # ------------------------------------------------------------------ storage
        with ui.card().classes("w-full"), section(t("settings.storage"), t("settings.storage_caption")):
            st = cfg.storage
            cloud = ui.switch(t("settings.storage_cloud"), value=st.cloud_enabled)
            bucket = ui.input(t("settings.storage_bucket"), value=st.bucket or "").classes("w-full max-w-lg")
            prefix = ui.input(t("settings.storage_prefix"), value=st.prefix).classes("w-60")

            def save_storage() -> None:
                st.cloud_enabled = bool(cloud.value)
                st.bucket = (bucket.value or "").strip() or None
                st.prefix = prefix.value or "aidriven/"
                c._cloud_store = None
                c.save_config()
                ui.notify(t("common.saved"), type="positive")

            async def test_storage() -> None:
                save_storage()
                ok, detail = await _in_thread(c.test_storage)
                ui.notify(detail, type="positive" if ok else "negative", multi_line=True, timeout=10000)

            with ui.row():
                ui.button(t("common.save"), icon="save", on_click=save_storage)
                ui.button(t("settings.storage_test"), icon="network_check", on_click=test_storage).props("flat")
            ui.label(t("settings.storage_note")).classes("text-caption text-grey-7")

        # ------------------------------------------------------------------ judge
        with ui.card().classes("w-full"), section(t("settings.judge"), t("settings.judge_caption")):
            profiles = sorted(c.repo.find(ModelProfile), key=lambda p: p.name)
            judge_enabled = ui.switch(t("settings.judge_enabled"), value=cfg.judge.enabled)
            judge_model = ui.select(
                {p.id: f"{p.name} ({p.model})" for p in profiles},
                label=t("settings.judge_model"),
                value=cfg.judge.model_id,
                clearable=True,
            ).classes("w-full max-w-lg")
            ui.label(t("settings.judge_hint")).classes("text-caption text-grey-7")

            def save_judge() -> None:
                cfg.judge.enabled = bool(judge_enabled.value)
                cfg.judge.model_id = judge_model.value
                c.save_config()
                ui.notify(t("common.saved"), type="positive")

            ui.button(t("common.save"), icon="save", on_click=save_judge)

        # ------------------------------------------------------------------ sandbox
        with ui.card().classes("w-full"), section(t("settings.sandbox"), t("settings.sandbox_caption")):
            sb = cfg.sandbox
            status = c.sandbox.status()
            ui.label(
                t(
                    "settings.sandbox_status",
                    active=status["active"],
                    docker=t("common.yes") if status["docker_available"] else t("common.no"),
                )
            ).classes("font-medium")
            if status["active"] == "process":
                ui.label(t("settings.sandbox_process_warning")).classes("text-caption text-warning")
            mode = ui.select(
                {"auto": t("settings.sandbox_auto"), "docker": "Docker", "process": t("settings.sandbox_process")},
                label=t("settings.sandbox_mode"),
                value=sb.mode,
            ).classes("w-60")
            with ui.row().classes("w-full"):
                py_img = ui.input(t("settings.sandbox_python_image"), value=sb.python_image).classes("flex-1")
                pw_img = ui.input(t("settings.sandbox_playwright_image"), value=sb.playwright_image).classes("flex-1")
            with ui.row().classes("w-full"):
                mem = ui.number(t("settings.sandbox_memory"), value=sb.memory_mb, min=64, max=16384).classes("flex-1")
                cpus = ui.number(t("settings.sandbox_cpus"), value=sb.cpus, min=0.1, max=16, step=0.1).classes("flex-1")
                pids = ui.number(t("settings.sandbox_pids"), value=sb.pids_limit, min=16, max=4096).classes("flex-1")
                timeout = ui.number(t("settings.sandbox_timeout"), value=sb.default_timeout_s, min=1, max=3600).classes(
                    "flex-1"
                )

            def save_sandbox() -> None:
                sb.mode = mode.value
                sb.python_image = py_img.value
                sb.playwright_image = pw_img.value
                sb.memory_mb = int(mem.value or 512)
                sb.cpus = float(cpus.value or 1)
                sb.pids_limit = int(pids.value or 256)
                sb.default_timeout_s = float(timeout.value or 60)
                c.save_config()
                ui.notify(t("common.saved"), type="positive")

            async def smoke() -> None:
                save_sandbox()
                code = SANDBOX_PROBE
                rec = await c.sandbox.run(
                    SandboxJob(files={"probe.py": code.encode()}, command=["python", "probe.py"], timeout_s=60)
                )
                ui.notify(
                    f"{rec.isolation} · exit {rec.exit_code} · {fmt_ms(rec.duration_ms)}\n"
                    f"{rec.stdout}{rec.stderr[-300:]}",
                    type="positive" if rec.ok else "negative",
                    multi_line=True,
                    timeout=12000,
                )

            with ui.row():
                ui.button(t("common.save"), icon="save", on_click=save_sandbox)
                ui.button(t("settings.sandbox_test"), icon="science", on_click=smoke).props("flat")

        # ------------------------------------------------------------------ data
        with ui.card().classes("w-full"), section(t("settings.data")):

            def backup() -> None:
                try:
                    path = c.backup_local()
                    ui.notify(t("settings.backup_done", path=str(path)), type="positive")
                except Exception as exc:
                    notify_error(exc)

            ui.button(t("settings.backup"), icon="backup", on_click=backup).props("flat")
            ui.label(t("settings.now", now=fmt_dt(__import__("datetime").datetime.now()))).classes(
                "text-caption text-grey-6"
            )


async def _in_thread(fn: Any) -> Any:
    import asyncio

    return await asyncio.to_thread(fn)

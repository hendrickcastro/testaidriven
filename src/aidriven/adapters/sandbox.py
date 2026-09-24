"""Sandboxes for executing model artifacts (constitution P4: nothing a model produces runs on the host as-is).

- `DockerSandbox`: no network, CPU/memory/pids limits, read-only root, non-root user, workdir bind-mounted.
- `ProcessSandbox`: fallback when Docker is unavailable. Temp dir, scrubbed environment, timeout, process-tree kill.
  It isolates much less; the UI warns when it is in use.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Literal

from aidriven.config import SandboxConfig
from aidriven.domain.models import ExecutionRecord
from aidriven.ports import SandboxJob

log = logging.getLogger(__name__)

MAX_OUTPUT = 200_000
BROWSER_IMAGE_PREFIX = "aidriven-browser:"
BROWSER_DOCKERFILE = """FROM {base}
RUN pip install --no-cache-dir --break-system-packages playwright=={version} \
    || pip install --no-cache-dir playwright=={version}
"""


def _truncate(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + "\n…[output truncated]"


def _write_files(workdir: Path, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        target = (workdir / name).resolve()
        if workdir.resolve() not in target.parents:
            raise ValueError(f"file name escapes the workdir: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _collect(workdir: Path, names: list[str]) -> tuple[dict[str, str], str | None]:
    files: dict[str, str] = {}
    screenshot = None
    for name in names:
        p = workdir / name
        if not p.exists() or not p.is_file():
            continue
        if name.endswith(".png"):
            screenshot = base64.b64encode(p.read_bytes()).decode()
        else:
            files[name] = _truncate(p.read_text(encoding="utf-8", errors="replace"))
    shot = workdir / "_screenshot.png"
    if screenshot is None and shot.exists():
        screenshot = base64.b64encode(shot.read_bytes()).decode()
    return files, screenshot


class ProcessSandbox:
    isolation: Literal["docker", "process"] = "process"

    def available(self) -> bool:
        return True

    async def run(self, job: SandboxJob) -> ExecutionRecord:
        return await asyncio.to_thread(self._run_sync, job)

    def _run_sync(self, job: SandboxJob) -> ExecutionRecord:
        workdir = Path(tempfile.mkdtemp(prefix="aidriven_sbx_"))
        try:
            _write_files(workdir, job.files)
            cmd = [sys.executable if c == "python" else c for c in job.command]
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "TEMP": str(workdir),
                "TMP": str(workdir),
                "HOME": str(workdir),
            }
            for keep in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PLAYWRIGHT_BROWSERS_PATH", "LOCALAPPDATA"):
                if keep in os.environ:
                    env[keep] = os.environ[keep]
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            t0 = time.perf_counter()
            proc = subprocess.Popen(
                cmd,
                cwd=workdir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=flags,
                start_new_session=sys.platform != "win32",
            )
            timed_out = False
            try:
                out, err = proc.communicate(timeout=job.timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_tree(proc)
                out, err = proc.communicate()
            ms = (time.perf_counter() - t0) * 1000
            files, shot = _collect(workdir, job.collect)
            return ExecutionRecord(
                ok=(proc.returncode == 0 and not timed_out),
                exit_code=proc.returncode,
                stdout=_truncate(out.decode("utf-8", errors="replace")),
                stderr=_truncate(err.decode("utf-8", errors="replace")),
                duration_ms=ms,
                timed_out=timed_out,
                isolation="process",
                files=files,
                screenshot_b64=shot,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @staticmethod
    def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            import signal

            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.kill()


class DockerSandbox:
    isolation: Literal["docker", "process"] = "docker"

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self._client = None
        self._last_ping = 0.0
        self._last_ok = False
        self._pull_lock = threading.Lock()
        self._ready_images: set[str] = set()

    def _docker(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env(timeout=10)
        return self._client

    def available(self) -> bool:
        now = time.monotonic()
        if now - self._last_ping < 15:
            return self._last_ok
        self._last_ping = now
        try:
            self._last_ok = bool(self._docker().ping())
        except Exception:
            self._client = None
            self._last_ok = False
        return self._last_ok

    async def run(self, job: SandboxJob) -> ExecutionRecord:
        return await asyncio.to_thread(self._run_sync, job)

    def _ensure_image(self, image: str) -> None:
        client = self._docker()
        with self._pull_lock:  # concurrent jobs must not pull the same image N times
            if image in self._ready_images:
                return
            try:
                client.images.get(image)
            except Exception:
                if image.startswith(BROWSER_IMAGE_PREFIX):
                    self._build_browser_image(image)
                else:
                    log.info("pulling sandbox image %s (first use, may take a while)", image)
                    client.images.pull(image)
            self._ready_images.add(image)

    def _build_browser_image(self, tag: str) -> None:
        """The official Playwright image ships browsers but not the Python package: add it (tag = version)."""
        version = tag.split(":", 1)[1] if ":" in tag else "latest"
        dockerfile = BROWSER_DOCKERFILE.format(base=self.config.browser_base_image, version=version)
        log.info(
            "building sandbox browser image %s from %s (first use, may take a while)",
            tag,
            self.config.browser_base_image,
        )
        self._docker().images.build(fileobj=io.BytesIO(dockerfile.encode()), tag=tag, rm=True, pull=True)

    def _run_sync(self, job: SandboxJob) -> ExecutionRecord:
        image = self.config.playwright_image if job.needs_browser else self.config.python_image
        self._ensure_image(image)
        workdir = Path(tempfile.mkdtemp(prefix="aidriven_dkr_"))
        container = None
        try:
            _write_files(workdir, job.files)
            os.chmod(workdir, 0o777)
            cmd = ["python3" if c == "python" else c for c in job.command]
            t0 = time.perf_counter()
            container = self._docker().containers.run(
                image,
                cmd,
                detach=True,
                working_dir="/work",
                volumes={str(workdir): {"bind": "/work", "mode": "rw"}},
                network_disabled=True,
                mem_limit=f"{self.config.memory_mb}m",
                memswap_limit=f"{self.config.memory_mb}m",
                nano_cpus=int(self.config.cpus * 1e9),
                pids_limit=self.config.pids_limit,
                read_only=True,
                tmpfs={"/tmp": "rw,size=128m"},
                user="pwuser" if job.needs_browser else "65534:65534",
                environment={"HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"},
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
                ipc_mode="private",
                shm_size="256m" if job.needs_browser else "64m",  # Chromium crashes with the 64 MB default
            )
            timed_out = False
            exit_code: int | None = None
            try:
                res = container.wait(timeout=job.timeout_s)
                exit_code = int(res.get("StatusCode", -1))
            except Exception:
                timed_out = True
                try:
                    container.kill()
                except Exception:
                    pass
            ms = (time.perf_counter() - t0) * 1000
            out = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            err = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
            files, shot = _collect(workdir, job.collect)
            return ExecutionRecord(
                ok=(exit_code == 0 and not timed_out),
                exit_code=exit_code,
                stdout=_truncate(out),
                stderr=_truncate(err),
                duration_ms=ms,
                timed_out=timed_out,
                isolation="docker",
                files=files,
                screenshot_b64=shot,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            shutil.rmtree(workdir, ignore_errors=True)


class SandboxManager:
    """Selects the sandbox according to config (`auto` = Docker if the daemon answers, else process)."""

    def __init__(self, config: SandboxConfig) -> None:
        self.config = config
        self.docker = DockerSandbox(config)
        self.process = ProcessSandbox()

    @property
    def isolation(self) -> Literal["docker", "process"]:
        return self.current().isolation

    def available(self) -> bool:
        return self.current().available()

    def current(self) -> DockerSandbox | ProcessSandbox:
        mode = self.config.mode
        if mode == "process":
            return self.process
        if mode == "docker":
            return self.docker
        return self.docker if self.docker.available() else self.process

    async def run(self, job: SandboxJob) -> ExecutionRecord:
        sandbox = self.current()
        if isinstance(sandbox, DockerSandbox) and not sandbox.available():
            return ExecutionRecord(
                ok=False,
                exit_code=None,
                stderr="Docker is required (sandbox mode = docker) but not reachable",
            )
        return await sandbox.run(job)

    def status(self) -> dict[str, object]:
        docker_ok = self.docker.available()
        active = "docker" if (self.config.mode == "docker" or (self.config.mode == "auto" and docker_ok)) else "process"
        return {"mode": self.config.mode, "docker_available": docker_ok, "active": active}

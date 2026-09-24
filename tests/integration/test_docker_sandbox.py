"""Docker sandbox isolation. Skipped when the Docker daemon is not reachable."""

from __future__ import annotations

import pytest

from aidriven.adapters.sandbox import DockerSandbox
from aidriven.config import SandboxConfig
from aidriven.ports import SandboxJob

CONFIG = SandboxConfig()
SANDBOX = DockerSandbox(CONFIG)
pytestmark = [pytest.mark.docker, pytest.mark.skipif(not SANDBOX.available(), reason="Docker not available")]

PROBE = b"""
import os, socket
try:
    socket.create_connection(("1.1.1.1", 53), 2)
    print("NET_OPEN")
except OSError:
    print("NET_BLOCKED")
try:
    open("/etc/aidriven_probe", "w").write("x")
    print("ROOT_WRITABLE")
except OSError:
    print("ROOT_READONLY")
open("out.txt", "w").write("hello")
print("UID", os.getuid())
"""


async def test_no_network_readonly_root_and_workdir_output() -> None:
    rec = await SANDBOX.run(
        SandboxJob(files={"p.py": PROBE}, command=["python", "p.py"], timeout_s=60, collect=["out.txt"])
    )
    assert rec.isolation == "docker"
    assert rec.ok, rec.stderr
    assert "NET_BLOCKED" in rec.stdout
    assert "ROOT_READONLY" in rec.stdout
    assert "UID 0" not in rec.stdout
    assert rec.files["out.txt"] == "hello"


async def test_timeout_kills_container() -> None:
    rec = await SANDBOX.run(
        SandboxJob(files={"s.py": b"import time\ntime.sleep(60)\n"}, command=["python", "s.py"], timeout_s=3)
    )
    assert rec.timed_out
    assert not rec.ok


def _has_browser_image() -> bool:
    try:
        SANDBOX._docker().images.get(CONFIG.playwright_image)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_browser_image(), reason="playwright image not pulled")
async def test_html_page_in_headless_browser() -> None:
    from aidriven.services.checks import harness_source

    html = b"<html><body><h1 id='t'>hi</h1><script>document.getElementById('t').textContent='ok'</script></body></html>"
    code = (
        b"from aidriven_harness import open_page, case, report\n"
        b"with open_page('index.html') as p:\n"
        b"    @case('script ran', timeout=0)\n"
        b"    def _():\n"
        b"        assert p.page.inner_text('#t') == 'ok'\n"
        b"    report()\n"
    )
    rec = await SANDBOX.run(
        SandboxJob(
            files={"index.html": html, "c.py": code, "aidriven_harness.py": harness_source()},
            command=["python", "c.py"],
            timeout_s=120,
            needs_browser=True,
        )
    )
    assert '"passed": 1' in rec.stdout, rec.stdout + rec.stderr

"""Darwin E2E against real cua-driver. Opt-in only.

Requires:
  - macOS
  - cua-driver installed + daemon running
  - Accessibility + Screen Recording granted to CuaDriver.app
  - KAGEHA_COMPUTER_E2E=1
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools import computer_driver as driver
from kageha.harness.tools.computer import register_computer_tools

_IS_DARWIN = platform.system() == "Darwin"
_E2E = os.environ.get("KAGEHA_COMPUTER_E2E", "").strip() in {"1", "true", "yes"}

pytestmark = [
    pytest.mark.live_ui,
    pytest.mark.skipif(not _IS_DARWIN, reason="Darwin-only"),
    pytest.mark.skipif(not _E2E, reason="set KAGEHA_COMPUTER_E2E=1 to run"),
]


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    return HarnessContext(
        workspace=SessionWorkspace(run_id="e2e", root=root),
        approvals=ApprovalGate(auto_approve=True, approver=None),
        router=SimpleNamespace(),
    )


@pytest.fixture(scope="module")
def driver_ready():
    if not driver.driver_available():
        pytest.skip("cua-driver not installed")

    async def _check():
        try:
            await driver.ensure_daemon(timeout=8.0)
        except driver.ComputerDriverError as exc:
            pytest.skip(str(exc))
        return await driver.permissions_status()

    return asyncio.run(_check())


def test_list_apps_includes_system_apps(tmp_path, driver_ready):
    ctx = _ctx(tmp_path)
    reg = register_computer_tools(ctx)

    async def _run():
        out = await reg.get("computer_list_apps").call(running_only=False)
        assert not out.startswith("ERROR:"), out
        data = json.loads(out)
        names = {a.get("name") for a in data.get("apps") or []}
        bids = {a.get("bundle_id") for a in data.get("apps") or []}
        assert (
            "Calculator" in names
            or "com.apple.calculator" in bids
            or "TextEdit" in names
        ), data

    asyncio.run(_run())


def test_get_state_and_click_calculator(tmp_path, driver_ready):
    if not driver_ready.get("accessibility"):
        pytest.skip(
            "Accessibility not granted to CuaDriver.app "
            "(run: cua-driver permissions grant)"
        )
    ctx = _ctx(tmp_path)
    reg = register_computer_tools(ctx)

    async def _run():
        front_before = None
        apps = json.loads(await reg.get("computer_list_apps").call(running_only=True))
        for a in apps.get("apps") or []:
            if a.get("active"):
                front_before = a.get("name")
                break

        st_raw = await reg.get("computer_get_state").call(
            app="Calculator",
            include_screenshot=True,
            max_elements=60,
        )
        if st_raw.startswith("ERROR:"):
            pytest.skip(st_raw)
        st = json.loads(st_raw)
        assert st.get("pid")
        assert st.get("window_id")
        if st.get("screenshot"):
            shot = ctx.workspace.root / st["screenshot"]
            assert shot.is_file() and shot.stat().st_size > 0

        # Prefer a digit button from snapshot
        ref = None
        for line in (st.get("snapshot") or "").splitlines():
            if "button" in line.lower() and any(ch in line for ch in "123456789"):
                ref = line.split()[0]
                break
        if not ref and not st.get("degraded"):
            # any eN
            for line in (st.get("snapshot") or "").splitlines():
                if line.startswith("e"):
                    ref = line.split()[0]
                    break
        if not ref:
            pytest.skip(f"no clickable refs / degraded AX: {st.get('degraded_reason')}")

        clicked = await reg.get("computer_click").call(ref=ref)
        if clicked.startswith("ERROR:") and (
            "closed connection" in clicked or "daemon" in clicked.lower()
        ):
            # TCC half-granted / daemon crash — treat as environment skip
            pytest.skip(clicked)
        assert not clicked.startswith("ERROR:"), clicked
        assert not clicked.startswith("DENIED:"), clicked

        apps2 = json.loads(await reg.get("computer_list_apps").call(running_only=True))
        front_after = None
        for a in apps2.get("apps") or []:
            if a.get("active"):
                front_after = a.get("name")
                break
        # Soft heuristic: if we knew frontmost and it wasn't Calculator, it should stay
        if front_before and front_before != "Calculator" and front_after:
            assert front_after == front_before or front_after != "Calculator", (
                f"focus stolen: {front_before} → {front_after}"
            )

    asyncio.run(_run())

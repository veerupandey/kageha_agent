"""Native /browser and /research slash commands + agent wiring."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kageha.chat import browser_commands
from kageha.context.assembler import SYSTEM_PROMPT
from kageha.harness.approvals import ApprovalGate
from kageha.harness.browser import backends as be
from kageha.harness.browser import prefs as prefs_mod
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.builtin import load_entry_point_tools
from kageha.harness.tool_packs import resolve_enabled_packs


@pytest.fixture()
def isolated_prefs(tmp_path: Path, monkeypatch):
    path = tmp_path / "browser.json"
    monkeypatch.setattr(prefs_mod, "prefs_path", lambda: path)
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.delenv("KAGEHA_BROWSER_PACK", raising=False)
    monkeypatch.delenv("KAGEHA_BROWSER_MODE", raising=False)
    monkeypatch.delenv("KAGEHA_HEADLESS_BACKEND", raising=False)
    # Reset to clean prefs
    prefs_mod.save_browser_prefs(prefs_mod.BrowserPrefs())
    prefs_mod.apply_browser_prefs()
    return path


def test_backend_catalog_has_expected_ids() -> None:
    ids = set(be.all_backend_ids())
    assert {
        "http",
        "chromium",
        "lightpanda",
        "comet",
        "cdp",
        "docker",
        "headless",
    } <= ids


@pytest.mark.asyncio
async def test_browser_list_and_use(isolated_prefs, monkeypatch) -> None:
    handled, msg = await browser_commands.handle_browser_command("/browser list")
    assert handled
    assert "lightpanda" in msg
    assert "comet" in msg

    handled, msg = await browser_commands.handle_browser_command("/browser use lightpanda")
    assert handled
    assert "lightpanda" in msg
    prefs = prefs_mod.load_browser_prefs()
    assert prefs.backend == "lightpanda"
    import os

    assert os.environ.get("KAGEHA_HEADLESS_BACKEND") == "lightpanda"


@pytest.mark.asyncio
async def test_browser_comet_enables_pack(isolated_prefs, monkeypatch) -> None:
    monkeypatch.setattr(
        "kageha.chat.comet.ensure_comet",
        AsyncMock(return_value="Comet ready · test"),
    )
    handled, msg = await browser_commands.handle_browser_command("/browser comet status")
    assert handled
    assert "Comet ready" in msg
    prefs = prefs_mod.load_browser_prefs()
    assert prefs.backend == "comet"
    assert prefs.enable_browser_pack is True
    import os

    packs = resolve_enabled_packs(env=dict(os.environ))
    assert "browser" in packs


@pytest.mark.asyncio
async def test_browser_diagnose_validates_input(isolated_prefs) -> None:
    handled, msg = await browser_commands.handle_browser_command("/browser diagnose")
    assert handled
    assert "Usage:" in msg
    handled, msg = await browser_commands.handle_browser_command(
        "/browser diagnose javascript:alert(1)"
    )
    assert handled
    assert "URL must use" in msg


@pytest.mark.asyncio
async def test_research_slash_runs_backend(isolated_prefs, monkeypatch) -> None:
    async def fake_run(query: str, depth: str = "", **kwargs):  # noqa: ANN003
        return f"# Research ({depth or 'flash'})\nquery: {query}\nOK"

    monkeypatch.setattr("kageha.research.backend.research_run", fake_run)
    handled, msg = await browser_commands.handle_research_command(
        "/research flash what is lightpanda"
    )
    assert handled
    assert "lightpanda" in msg
    assert "Research (flash)" in msg


def test_system_prompt_requires_research_run() -> None:
    assert "research_run" in SYSTEM_PROMPT
    assert "REQUIRED" in SYSTEM_PROMPT or "prefer" in SYSTEM_PROMPT.lower()
    assert "/browser" in SYSTEM_PROMPT
    assert "Chat-first" in SYSTEM_PROMPT
    assert (
        "unsolicited" in SYSTEM_PROMPT.lower()
        or "unless the user explicitly asked" in SYSTEM_PROMPT
    )


def test_default_tools_include_research_run(tmp_path: Path, monkeypatch, isolated_prefs) -> None:
    monkeypatch.setenv("KAGEHA_TOOL_PACKS", "")  # core only
    # empty string might parse as None - use del
    monkeypatch.delenv("KAGEHA_TOOL_PACKS", raising=False)
    monkeypatch.delenv("KAGEHA_BROWSER_PACK", raising=False)
    root = tmp_path / "session"
    root.mkdir()
    ws = SessionWorkspace(run_id="t", root=root)
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    reg = load_entry_point_tools(ctx)
    names = set(reg.names())
    assert "research_run" in names
    assert "parallel_web_fetch" in names
    assert "web_fetch" in names
    # browser pack off by default with clean prefs
    assert "browser_open" not in names


def test_browser_use_auto_enables_pack_in_tools(
    tmp_path: Path, monkeypatch, isolated_prefs
) -> None:
    prefs_mod.set_backend("headless", enable_pack=True)
    root = tmp_path / "session"
    root.mkdir()
    ws = SessionWorkspace(run_id="t", root=root)
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )
    # Playwright may not be installed — registration still happens.
    reg = load_entry_point_tools(ctx)
    assert "browser" in (ctx.meta.get("tool_packs_enabled") or [])
    assert "browser_open" in reg.names()
    assert "research_run" in reg.names()


def test_core_packs_include_research() -> None:
    from kageha.harness.tool_packs import CORE_PACK_NAMES

    assert "research" in CORE_PACK_NAMES


def test_prefs_persist(isolated_prefs) -> None:
    prefs_mod.set_backend("chromium")
    raw = json.loads(isolated_prefs.read_text())
    assert raw["backend"] == "chromium"
    again = prefs_mod.load_browser_prefs()
    assert again.backend == "chromium"

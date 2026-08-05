"""Regression tests for interactive browser tools + web_browse / web_research skills."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.memory.skills import SkillRegistry

# Expected interactive + short-name tool names from register_browser_tools.
EXPECTED_BROWSER_TOOLS = frozenset(
    {
        "browser_connect",
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_fill",
        "browser_select",
        "browser_upload",
        "browser_press",
        "browser_scroll",
        "browser_wait",
        "browser_screenshot",
        "browser_evaluate",
        "browser_batch",
        "browser_diagnostics",
        "browser_cdp",
        "browser_tabs",
        "browser_lock",
        "browser_close",
        "browse",
        "extract",
        "screenshot",
    }
)

_LOCAL_HTML = """<!DOCTYPE html>
<html>
<head><title>Browse Regression Fixture</title></head>
<body>
  <h1 id="heading">Hello Fixture</h1>
  <button id="go-btn" type="button">Go</button>
  <input id="name-input" type="text" name="name" placeholder="Your name" />
  <select id="country"><option value="us">United States</option><option value="ca">Canada (+1)</option></select>
  <input id="resume-input" type="file" />
  <p id="status">idle</p>
  <div id="tall" style="height: 2400px; background: linear-gradient(#fff, #ccc);">
    Spacer for scroll
  </div>
  <p id="bottom">bottom-marker</p>
  <script>
    document.getElementById('go-btn').addEventListener('click', () => {
      document.getElementById('status').textContent = 'clicked';
    });
  </script>
</body>
</html>
"""


def _ctx(tmp_path: Path) -> HarnessContext:
    root = tmp_path / "session"
    root.mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(exist_ok=True)
    ws = SessionWorkspace(run_id="test", root=root)
    return HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=SimpleNamespace(),
    )


def _ensure_playwright() -> None:
    pytest.importorskip("playwright")


async def _chromium_or_skip() -> None:
    """Launch Chromium once; skip cleanly if the browser binary is missing."""
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        pytest.skip(f"playwright unavailable: {e}")
    try:
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.launch(headless=True)
            await browser.close()
        finally:
            await pw.stop()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"playwright chromium unavailable: {e}")


def test_browser_tool_registration_names(tmp_path: Path) -> None:
    from kageha.harness.tools.browser import register_browser_tools

    ctx = _ctx(tmp_path)
    reg = register_browser_tools(ctx)
    names = set(reg.names())
    missing = EXPECTED_BROWSER_TOOLS - names
    assert not missing, f"missing browser tools: {sorted(missing)}"


def test_browser_connect_reuses_live_session() -> None:
    from kageha.harness.browser.engine import BrowserEngine, TabHandle

    class Page:
        url = "https://example.test/already-open"

        def is_closed(self):
            return False

    engine = BrowserEngine()
    engine.mode = "headless"
    engine.tabs = [TabHandle(page=Page())]
    engine.active = 0
    out = asyncio.run(engine.connect(target="auto"))
    assert "reused headless" in out
    assert engine.page is not None


def test_web_browse_and_web_research_skills_discoverable() -> None:
    reg = SkillRegistry()
    assert "web_browse" in reg.skills
    assert "web_research" in reg.skills
    catalog = reg.catalog()
    assert "web_browse" in catalog
    assert "web_research" in catalog
    browse = reg.get("web_browse")
    research = reg.get("web_research")
    assert browse is not None
    assert research is not None
    assert "browser_open" in (browse.body or "")
    assert "browser_open" in (research.body or "")
    assert "web_browse" in (research.body or "")


def test_browser_local_page_click_type_scroll_screenshot_close(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_BROWSER_MODE", "headless")
    _ensure_playwright()
    from kageha.harness.tools.browser import register_browser_tools

    html_path = tmp_path / "fixture.html"
    html_path.write_text(_LOCAL_HTML, encoding="utf-8")
    file_url = html_path.resolve().as_uri()

    ctx = _ctx(tmp_path)
    reg = register_browser_tools(ctx)

    async def _run() -> None:
        await _chromium_or_skip()
        try:
            opened = await reg.get("browser_open").call(url=file_url, screenshot=True)
            assert "Browse Regression Fixture" in opened or "title:" in opened
            assert "[e" in opened, f"expected snapshot refs in open output:\n{opened}"

            snap = await reg.get("browser_snapshot").call()
            assert "[e" in snap, f"expected refs in snapshot:\n{snap}"
            assert "button" in snap.lower() or "Go" in snap

            batch_out = await reg.get("browser_batch").call(
                actions_json=(
                    '[{"action":"fill","target":"#name-input","text":"batched"},'
                    '{"action":"click","target":"role=button:Go"}]'
                )
            )
            assert "completed 2 browser actions" in batch_out
            assert "title:" in batch_out
            assert "clicked" in (await reg.get("extract").call(selector="#status"))

            shot_out = await reg.get("browser_screenshot").call(path="artifacts/regression.png")
            assert "Saved" in shot_out
            shot_file = ctx.workspace.root / "artifacts" / "regression.png"
            assert shot_file.is_file(), shot_out
            assert shot_file.stat().st_size > 0

            auto_shot = ctx.workspace.root / "artifacts" / "browse.png"
            assert auto_shot.is_file()
            assert auto_shot.stat().st_size > 0

            lock_out = await reg.get("browser_lock").call(action="lock")
            assert "locked" in lock_out.lower()

            click_out = await reg.get("browser_click").call(target="role=button:Go")
            assert "clicked:" in click_out
            status = await reg.get("extract").call(selector="#status")
            assert "clicked" in status.lower()

            type_out = await reg.get("browser_type").call(target="#name-input", text="kageha")
            assert "typed into #name-input" in type_out

            select_out = await reg.get("browser_select").call(
                target="#country", option="Canada (+1)"
            )
            assert "selected 'Canada (+1)'" in select_out
            # browser_fill also detects native selects instead of calling fill().
            fill_select = await reg.get("browser_fill").call(
                target="#country", text="United States"
            )
            assert "selected 'United States'" in fill_select

            resume = ctx.workspace.root / "artifacts" / "resume.pdf"
            resume.write_bytes(b"%PDF-1.4\n% fixture\n")
            upload_out = await reg.get("browser_upload").call(
                target="#resume-input", paths_json='["artifacts/resume.pdf"]'
            )
            assert "uploaded 1 file(s)" in upload_out

            eval_out = await reg.get("browser_evaluate").call(
                expression="() => document.getElementById('status').textContent"
            )
            assert "clicked" in eval_out.lower()

            diagnostics = await reg.get("browser_diagnostics").call()
            assert '"timing"' in diagnostics
            assert '"performance_metrics"' in diagnostics
            assert file_url in diagnostics

            tabs_out = await reg.get("browser_tabs").call(action="list")
            assert "active:" in tabs_out

            scroll_out = await reg.get("browser_scroll").call(direction="bottom")
            assert "scrolled: bottom" in scroll_out
            scroll_up = await reg.get("browser_scroll").call(direction="top")
            assert "scrolled: top" in scroll_up

            wait_out = await reg.get("browser_wait").call(selector="#bottom", timeout_ms=2000)
            assert "waited for selector" in wait_out

            press_out = await reg.get("browser_press").call(key="Tab")
            assert "pressed: Tab" in press_out
            await reg.get("browser_lock").call(action="unlock")
        finally:
            closed = await reg.get("browser_close").call()
            assert "closed" in closed.lower()

        # After close, a fresh open should work (state cleaned).
        reopened = await reg.get("browser_open").call(url=file_url)
        assert "title:" in reopened
        await reg.get("browser_close").call()

    asyncio.run(_run())


def test_browser_compat_aliases_browse_extract_screenshot(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KAGEHA_BROWSER_MODE", "headless")
    _ensure_playwright()
    from kageha.harness.tools.browser import register_browser_tools

    html_path = tmp_path / "alias.html"
    html_path.write_text(
        "<!DOCTYPE html><html><head><title>Alias</title></head>"
        "<body><main id='m'>alias-body-text</main></body></html>",
        encoding="utf-8",
    )
    ctx = _ctx(tmp_path)
    reg = register_browser_tools(ctx)

    async def _run() -> None:
        await _chromium_or_skip()
        try:
            out = await reg.get("browse").call(url=html_path.resolve().as_uri())
            assert "Alias" in out
            assert "alias-body-text" in out
            text = await reg.get("extract").call(selector="#m")
            assert "alias-body-text" in text
            shot = await reg.get("screenshot").call(path="artifacts/alias.png")
            assert "Saved" in shot
            assert (ctx.workspace.root / "artifacts" / "alias.png").is_file()
        finally:
            await reg.get("browser_close").call()

    asyncio.run(_run())


def test_browser_tools_import_without_playwright_at_import_time() -> None:
    """Registration must not require playwright until a browser tool is invoked."""
    from kageha.harness.tools.browser import register_browser_tools

    assert callable(register_browser_tools)

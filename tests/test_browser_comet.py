"""Unit tests for Comet/CDP browser backend (mocked Playwright)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.browser import resolve_browser_mode, resolve_cdp_endpoint


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


def test_resolve_browser_mode(monkeypatch) -> None:
    monkeypatch.delenv("KAGEHA_BROWSER_MODE", raising=False)
    assert resolve_browser_mode() == "auto"
    assert resolve_browser_mode("comet") == "cdp"
    assert resolve_browser_mode("cdp") == "cdp"
    assert resolve_browser_mode("headless") == "headless"
    monkeypatch.setenv("KAGEHA_BROWSER_MODE", "comet")
    assert resolve_browser_mode() == "cdp"


def test_resolve_cdp_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("KAGEHA_COMET_CDP", raising=False)
    assert resolve_cdp_endpoint() == "http://127.0.0.1:9222"
    monkeypatch.setenv("KAGEHA_COMET_CDP", "http://127.0.0.1:9333")
    assert resolve_cdp_endpoint() == "http://127.0.0.1:9333"


def test_browser_connect_auto_falls_back_to_headless(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("KAGEHA_BROWSER_MODE", raising=False)
    from kageha.harness.tools.browser import register_browser_tools

    page = MagicMock()
    page.url = "about:blank"
    page.is_closed.return_value = False
    page.close = AsyncMock()
    page.bring_to_front = AsyncMock()

    browser = MagicMock()
    browser.contexts = []
    browser.close = AsyncMock()
    browser.new_page = AsyncMock(return_value=page)

    chromium = MagicMock()
    chromium.connect_over_cdp = AsyncMock()
    chromium.launch = AsyncMock(return_value=browser)

    pw = MagicMock()
    pw.chromium = chromium
    pw.stop = AsyncMock()
    pw_cm = MagicMock()
    pw_cm.start = AsyncMock(return_value=pw)

    ctx = _ctx(tmp_path)
    reg = register_browser_tools(ctx)

    async def _run() -> None:
        with (
            patch(
                "kageha.harness.browser.engine._require_playwright",
                return_value=lambda: pw_cm,
            ),
            patch(
                "kageha.harness.browser.engine.cdp_reachable",
                new=AsyncMock(return_value=False),
            ),
        ):
            connected = await reg.get("browser_connect").call(target="auto")
            assert "headless" in connected.lower()
            assert "auto-fallback" in connected.lower()
            chromium.launch.assert_awaited()
            assert chromium.connect_over_cdp.await_count == 0

    asyncio.run(_run())


def test_browser_connect_comet_uses_cdp_and_close_disconnects(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("KAGEHA_BROWSER_MODE", raising=False)
    from kageha.harness.tools.browser import register_browser_tools

    page = MagicMock()
    page.url = "about:blank"
    page.is_closed.return_value = False
    page.close = AsyncMock()
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="LinkedIn")
    page.screenshot = AsyncMock()
    page.inner_text = AsyncMock(return_value="feed content")
    page.evaluate = AsyncMock(return_value=[])
    page.wait_for_timeout = AsyncMock()
    page.bring_to_front = AsyncMock()

    existing = MagicMock()
    existing.url = "https://user-was-here.example/"
    cdp_session = MagicMock()
    cdp_session.send = AsyncMock(return_value={"nodes": []})
    cdp_session.detach = AsyncMock()
    context = MagicMock()
    context.pages = [existing]
    context.new_page = AsyncMock(return_value=page)
    context.new_cdp_session = AsyncMock(return_value=cdp_session)
    page.context = context

    browser = MagicMock()
    browser.contexts = [context]
    browser.close = AsyncMock()

    chromium = MagicMock()
    chromium.connect_over_cdp = AsyncMock(return_value=browser)
    chromium.launch = AsyncMock()

    pw = MagicMock()
    pw.chromium = chromium
    pw.stop = AsyncMock()

    pw_cm = MagicMock()
    pw_cm.start = AsyncMock(return_value=pw)

    ctx = _ctx(tmp_path)
    reg = register_browser_tools(ctx)

    async def _run() -> None:
        with (
            patch(
                "kageha.harness.browser.engine._require_playwright",
                return_value=lambda: pw_cm,
            ),
            patch(
                "kageha.harness.browser.engine.cdp_reachable",
                new=AsyncMock(return_value=True),
            ),
        ):
            connected = await reg.get("browser_connect").call(target="comet")
            assert "comet/cdp" in connected
            chromium.connect_over_cdp.assert_awaited()
            assert chromium.launch.await_count == 0
            # Must open a dedicated tab — never steal context.pages[0].
            context.new_page.assert_awaited()
            page.bring_to_front.assert_awaited()

            opened = await reg.get("browser_open").call(url="https://www.linkedin.com/feed/")
            assert "comet/cdp" in opened or "login cookies" in opened
            page.goto.assert_awaited()

            closed = await reg.get("browser_close").call()
            assert "disconnect" in closed.lower() or "comet" in closed.lower()
            page.close.assert_awaited()
            browser.close.assert_awaited()
            # Must not have launched a headless browser
            assert chromium.launch.await_count == 0

    asyncio.run(_run())

"""Persistent multi-tab browser session engine.

Keeps one Playwright connection warm across agent tool calls. Opens a dedicated
agent tab on CDP (never steals the user's focused tab). Supports lock, tabs,
AX snapshots, evaluate, and raw CDP.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kageha.harness.browser.snapshot import build_ax_items, format_snapshot, resolve_locator

DEFAULT_CDP = "http://127.0.0.1:9222"


def resolve_browser_mode(explicit: str | None = None) -> str:
    """Return 'headless', 'cdp', or 'docker'."""
    raw = (explicit or os.environ.get("KAGEHA_BROWSER_MODE") or "headless").strip().lower()
    if raw in {"comet", "cdp", "logged_in", "logged-in", "chrome"}:
        return "cdp"
    if raw in {"docker", "sandbox", "browser-sandbox", "sandboxed"}:
        return "docker"
    return "headless"


def resolve_cdp_endpoint(explicit: str | None = None) -> str:
    return (explicit or os.environ.get("KAGEHA_COMET_CDP") or DEFAULT_CDP).strip()


def _require_playwright() -> Any:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError as e:
        raise ImportError(
            "Browser extra not installed. Run: uv sync --extra browser && "
            "uv run playwright install chromium"
        ) from e
    return async_playwright


@dataclass
class TabHandle:
    page: Any
    refs: dict[str, Any] = field(default_factory=dict)
    ref_meta: list[dict[str, Any]] = field(default_factory=list)
    last_snapshot: str = ""


class BrowserEngine:
    """One engine per harness registry (agent session)."""

    def __init__(self, artifact_root: Callable[[str], Path] | None = None) -> None:
        self._artifact_root = artifact_root
        self.mode: str = resolve_browser_mode()
        self.cdp: str = resolve_cdp_endpoint()
        self.pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.owned: bool = False
        self.docker_session: Any = None
        self.locked: bool = False
        self.tabs: list[TabHandle] = []
        self.active: int = -1

    @property
    def page(self) -> Any | None:
        if 0 <= self.active < len(self.tabs):
            return self.tabs[self.active].page
        return None

    def _tab(self) -> TabHandle:
        if not (0 <= self.active < len(self.tabs)):
            raise RuntimeError("No active browser tab — call browser_open or browser_tabs(new)")
        return self.tabs[self.active]

    def session_banner(self) -> str:
        if self.mode == "cdp":
            return f"session: comet/cdp ({self.cdp}) — using your login cookies"
        if self.mode == "docker":
            novnc = getattr(self.docker_session, "novnc_url", "") or ""
            extra = f"\nnoVNC observer: {novnc}" if novnc else ""
            return (
                f"session: docker sandbox ({self.cdp}) "
                f"— container Chromium, no host login cookies{extra}"
            )
        return "session: headless chromium — no login cookies"

    async def disconnect(self) -> None:
        from kageha.harness.browser_sandbox import stop_docker_browser

        for tab in self.tabs:
            try:
                if tab.page is not None and not tab.page.is_closed():
                    if self.mode in {"cdp", "docker"}:
                        await tab.page.close()
            except Exception:
                pass
        try:
            if self.browser is not None:
                if self.owned or self.mode in {"cdp", "docker"}:
                    await self.browser.close()
        except Exception:
            pass
        try:
            if self.pw is not None:
                await self.pw.stop()
        except Exception:
            pass
        try:
            await stop_docker_browser(self.docker_session)
        except Exception:
            pass
        self.pw = None
        self.browser = None
        self.context = None
        self.owned = False
        self.docker_session = None
        self.tabs = []
        self.active = -1
        self.locked = False

    async def _attach_cdp(self, endpoint: str) -> TabHandle:
        async_playwright = _require_playwright()
        pw = await async_playwright().start()
        try:
            browser = await pw.chromium.connect_over_cdp(endpoint)
        except Exception as e:  # noqa: BLE001
            await pw.stop()
            raise RuntimeError(
                f"Cannot connect to browser CDP at {endpoint} ({e}). "
                "Start Comet with: open -a Comet --args --remote-debugging-port=9222"
            ) from e
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass
        self.pw = pw
        self.browser = browser
        self.context = context
        self.mode = "cdp"
        self.cdp = endpoint
        self.owned = False
        tab = TabHandle(page=page)
        self.tabs = [tab]
        self.active = 0
        return tab

    async def _launch_headless(self) -> TabHandle:
        async_playwright = _require_playwright()
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        self.pw = pw
        self.browser = browser
        self.context = None
        self.mode = "headless"
        self.owned = True
        self.docker_session = None
        tab = TabHandle(page=page)
        self.tabs = [tab]
        self.active = 0
        return tab

    async def _launch_docker(self) -> TabHandle:
        from kageha.harness.browser_sandbox import start_docker_browser

        session = await start_docker_browser()
        tab = await self._attach_cdp(session.cdp_endpoint)
        self.mode = "docker"
        self.cdp = session.cdp_endpoint
        self.owned = False
        self.docker_session = session
        return tab

    async def ensure_page(self) -> Any:
        page = self.page
        if page is not None:
            try:
                if not page.is_closed():
                    return page
            except Exception:
                pass
            await self.disconnect()

        mode = resolve_browser_mode(self.mode)
        sticky = (self.mode or "").strip().lower()
        if sticky == "cdp" or mode == "cdp":
            return (await self._attach_cdp(resolve_cdp_endpoint(self.cdp))).page
        if sticky == "docker" or mode == "docker":
            return (await self._launch_docker()).page
        return (await self._launch_headless()).page

    async def connect(self, target: str = "comet", endpoint: str = "") -> str:
        t = (target or "comet").strip().lower()
        await self.disconnect()
        if t in {"comet", "cdp", "logged_in", "logged-in", "chrome"}:
            ep = resolve_cdp_endpoint(endpoint or None)
            self.mode = "cdp"
            self.cdp = ep
            page = (await self._attach_cdp(ep)).page
            return (
                f"connected: comet/cdp\nendpoint: {ep}\n"
                f"url: {page.url}\n"
                "Your Comet/Chrome login cookies are available. "
                "Use browser_open / browser_click / browser_type as usual. "
                "browser_close disconnects without quitting Comet."
            )
        if t in {"docker", "sandbox", "browser-sandbox", "sandboxed"}:
            self.mode = "docker"
            page = (await self._launch_docker()).page
            novnc = getattr(self.docker_session, "novnc_url", "") or ""
            novnc_pw = getattr(self.docker_session, "novnc_password", "") or ""
            lines = [
                f"connected: docker sandbox\nendpoint: {self.cdp}",
                f"url: {page.url}",
                "Chromium runs in a container (no host login cookies).",
            ]
            if novnc:
                lines.append(f"noVNC observer: {novnc}")
                if novnc_pw:
                    lines.append(f"noVNC password: {novnc_pw}")
            else:
                lines.append(
                    "Tip: build baked image for noVNC — "
                    "docker build -t kageha-browser:local docker/browser"
                )
            return "\n".join(lines)
        self.mode = "headless"
        page = (await self._launch_headless()).page
        return f"connected: headless\nurl: {page.url}\nFresh Chromium (no login)."

    async def screenshot(self, path: str = "artifacts/browse.png") -> str:
        page = await self.ensure_page()
        if self._artifact_root is None:
            dest = Path(path)
        else:
            dest = self._artifact_root(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(dest))
        return path

    async def snapshot(
        self,
        *,
        limit: int = 60,
        compact: bool = True,
        include_text: bool = False,
        text_chars: int = 2000,
    ) -> str:
        page = await self.ensure_page()
        tab = self._tab()
        items = await build_ax_items(page, limit=limit)
        try:
            title = await page.title()
        except Exception:
            title = ""
        text, refs, meta = format_snapshot(
            items, url=page.url or "", title=title, compact=compact
        )
        tab.refs = refs
        tab.ref_meta = meta
        tab.last_snapshot = text
        out = f"{self.session_banner()}\nlocked: {self.locked}\ntab: {self.active}\n\n{text}"
        if include_text:
            try:
                body = (await page.inner_text("body"))[: max(200, text_chars)]
                out += f"\n\n## Text preview\n{body}"
            except Exception:
                pass
        return out

    async def open(
        self,
        url: str,
        *,
        screenshot: bool = False,
        limit: int = 60,
        include_text: bool = True,
    ) -> str:
        page = await self.ensure_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        shot_line = ""
        if screenshot:
            shot = await self.screenshot("artifacts/browse.png")
            shot_line = f"screenshot: {shot}\n"
        snap = await self.snapshot(limit=limit, include_text=include_text)
        return f"{snap}\n{shot_line}".rstrip()

    async def click(self, target: str, *, resnapshot: bool = True) -> str:
        page = await self.ensure_page()
        tab = self._tab()
        loc = await resolve_locator(page, target, tab.refs)
        await loc.click(timeout=15000)
        await page.wait_for_timeout(150)
        if resnapshot:
            snap = await self.snapshot(include_text=False)
            return f"clicked: {target}\n\n{snap}"
        return f"clicked: {target}\nurl: {page.url}"

    async def type_text(
        self, target: str, text: str, *, clear: bool = True, slowly: bool = False
    ) -> str:
        page = await self.ensure_page()
        tab = self._tab()
        loc = await resolve_locator(page, target, tab.refs)
        if clear and not slowly:
            await loc.fill(text, timeout=15000)
        else:
            await loc.click(timeout=15000)
            if clear:
                await page.keyboard.press("Meta+a")
                await page.keyboard.press("Backspace")
            await page.keyboard.type(text, delay=20 if slowly else 0)
        return f"typed into {target} ({len(text)} chars)\nurl: {page.url}"

    async def fill(self, target: str, text: str) -> str:
        return await self.type_text(target, text, clear=True, slowly=False)

    async def press(self, key: str) -> str:
        page = await self.ensure_page()
        await page.keyboard.press(key)
        await page.wait_for_timeout(100)
        return f"pressed: {key}\nurl: {page.url}"

    async def scroll(self, direction: str = "down", amount: int = 600) -> str:
        page = await self.ensure_page()
        d = direction.lower().strip()
        if d == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif d == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        elif d == "up":
            await page.mouse.wheel(0, -abs(amount))
        else:
            await page.mouse.wheel(0, abs(amount))
        await page.wait_for_timeout(100)
        return f"scrolled: {d}\nurl: {page.url}"

    async def wait(self, selector: str = "", timeout_ms: int = 3000) -> str:
        page = await self.ensure_page()
        if selector.strip():
            await page.wait_for_selector(selector, timeout=max(100, timeout_ms))
            return f"waited for selector: {selector}"
        await page.wait_for_timeout(max(50, min(timeout_ms, 30000)))
        return f"waited: {timeout_ms}ms"

    async def evaluate(self, expression: str) -> str:
        import json

        page = await self.ensure_page()
        expr = (expression or "").strip()
        if not expr:
            return "ERROR: expression is required"
        result: Any
        try:
            if expr.startswith("()") or expr.startswith("function"):
                result = await page.evaluate(expr)
            else:
                result = await page.evaluate(f"(() => ({expr}))()")
        except Exception as e:  # noqa: BLE001
            try:
                result = await page.evaluate(expr)
            except Exception:
                return f"ERROR: evaluate failed: {e}"
        try:
            return json.dumps(result, indent=2, default=str)[:12000]
        except Exception:
            return str(result)[:12000]

    async def cdp(self, method: str, params_json: str = "{}") -> str:
        import json

        page = await self.ensure_page()
        method = (method or "").strip()
        if not method or "." not in method:
            return "ERROR: method must look like Domain.method (e.g. Runtime.evaluate)"
        # Deny focus-sensitive / dangerous domains (Cursor-aligned).
        domain = method.split(".", 1)[0]
        denied = {
            "Input",
            "Browser",
            "Target",
            "Storage",
            "Network.setCookie",
            "Network.deleteCookies",
        }
        if domain in denied or method in denied:
            return f"ERROR: CDP method denied for safety: {method}"
        try:
            params = json.loads(params_json or "{}")
        except json.JSONDecodeError as e:
            return f"ERROR: params_json must be JSON object: {e}"
        if not isinstance(params, dict):
            return "ERROR: params_json must be a JSON object"
        client = await page.context.new_cdp_session(page)
        try:
            result = await client.send(method, params)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: CDP {method} failed: {e}"
        finally:
            try:
                await client.detach()
            except Exception:
                pass
        try:
            return json.dumps(result, indent=2, default=str)[:14000]
        except Exception:
            return str(result)[:14000]

    async def tabs_action(self, action: str, index: int | None = None) -> str:
        action = (action or "list").strip().lower()
        if action == "list":
            lines = [f"active: {self.active}", f"locked: {self.locked}"]
            for i, tab in enumerate(self.tabs):
                try:
                    url = tab.page.url if tab.page and not tab.page.is_closed() else "(closed)"
                    title = await tab.page.title() if tab.page and not tab.page.is_closed() else ""
                except Exception:
                    url, title = "(error)", ""
                mark = "*" if i == self.active else " "
                lines.append(f"{mark} [{i}] {title[:60]} — {url}")
            return "\n".join(lines) if self.tabs else "no tabs (call browser_open first)"
        if action == "new":
            await self.ensure_page()
            if self.context is not None:
                page = await self.context.new_page()
            else:
                page = await self.browser.new_page()
            self.tabs.append(TabHandle(page=page))
            self.active = len(self.tabs) - 1
            return f"new tab index={self.active}\nurl: {page.url}"
        if action == "select":
            if index is None or not (0 <= index < len(self.tabs)):
                return f"ERROR: index required (0..{len(self.tabs) - 1})"
            self.active = index
            try:
                await self.tabs[index].page.bring_to_front()
            except Exception:
                pass
            return f"selected tab {index}\nurl: {self.tabs[index].page.url}"
        if action == "close":
            idx = self.active if index is None else index
            if not (0 <= idx < len(self.tabs)):
                return "ERROR: invalid tab index"
            try:
                await self.tabs[idx].page.close()
            except Exception:
                pass
            self.tabs.pop(idx)
            if not self.tabs:
                self.active = -1
                return "closed last tab"
            self.active = min(self.active, len(self.tabs) - 1)
            return f"closed tab {idx}; active={self.active}"
        return "ERROR: action must be list|new|close|select"

    def lock(self) -> str:
        self.locked = True
        return "browser locked — exclusive agent control until browser_lock(unlock)"

    def unlock(self) -> str:
        self.locked = False
        return "browser unlocked"

    async def extract(self, selector: str = "body", max_chars: int = 8000) -> str:
        page = await self.ensure_page()
        text = await page.inner_text(selector)
        return text[: max(200, max_chars)]

    async def close(self) -> str:
        mode = self.mode
        try:
            await self.disconnect()
        except Exception as e:  # noqa: BLE001
            self.tabs = []
            self.active = -1
            self.pw = None
            self.browser = None
            return f"closed with warning: {e}"
        if mode == "cdp":
            return "disconnected from Comet/CDP (Comet left running)"
        return "browser closed"

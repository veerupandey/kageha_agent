"""Interactive Playwright browser tools (optional extra).

Backends:
  - headless (default): fresh Chromium, no cookies/login
  - comet/cdp: attach to running Comet/Chrome via CDP (uses your login)
  - docker: Chromium in a hardened container with CDP

Env:
  KAGEHA_BROWSER_MODE=headless|comet|cdp|docker
  KAGEHA_COMET_CDP=http://127.0.0.1:9222
  KAGEHA_BROWSER_DOCKER_IMAGE=browserless/chrome:latest

Prefer web_fetch (core) for public static pages; use this pack for JS apps,
logins, and multi-step UI. Snapshot returns AX refs (e0…) — act, then re-snapshot.
Do not screenshot every step unless you need vision evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kageha.harness.browser import BrowserEngine, resolve_browser_mode, resolve_cdp_endpoint
from kageha.harness.tools.base import ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

# Re-export for tests / comet helpers that import from this module.
__all__ = [
    "register_browser_tools",
    "resolve_browser_mode",
    "resolve_cdp_endpoint",
]


def register_browser_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    engine = BrowserEngine(artifact_root=lambda p: ctx.workspace.path(p))

    @tool(
        description=(
            "Attach browser tools to a backend. target=comet|cdp uses your logged-in "
            "Comet/Chrome via CDP; target=docker runs Chromium in a sandbox container; "
            "target=headless launches a fresh host Chromium. "
            "Call this before browsing login-protected sites (use comet)."
        ),
        risk_class="browser",
    )
    async def browser_connect(target: str = "comet", endpoint: str = "") -> str:
        return await engine.connect(target=target, endpoint=endpoint)

    @tool(
        description=(
            "Open a URL in the agent-owned browser tab (new tab on Comet/CDP — never "
            "the user's focused tab). Returns AX snapshot refs + optional screenshot. "
            "For public static pages prefer web_fetch (faster, no Chromium)."
        ),
        risk_class="browser",
    )
    async def browser_open(
        url: str, screenshot: bool = False, include_text: bool = True
    ) -> str:
        return await engine.open(url, screenshot=screenshot, include_text=include_text)

    @tool(
        description=(
            "Refresh accessibility snapshot with stable refs (e0, e1, …) for click/type. "
            "This is the source of truth for page structure — prefer over guessing CSS. "
            "Set include_text=true for a body text preview."
        ),
        risk_class="browser",
    )
    async def browser_snapshot(
        limit: int = 60, compact: bool = True, include_text: bool = False
    ) -> str:
        return await engine.snapshot(
            limit=limit, compact=compact, include_text=include_text
        )

    @tool(
        description="Click an element by snapshot ref (e0) or CSS / text=... / role=button:Name.",
        risk_class="browser",
    )
    async def browser_click(target: str, resnapshot: bool = True) -> str:
        return await engine.click(target, resnapshot=resnapshot)

    @tool(
        description="Type into a field (ref/CSS). Clears existing value first unless clear=false.",
        risk_class="browser",
    )
    async def browser_type(target: str, text: str, clear: bool = True) -> str:
        return await engine.type_text(target, text, clear=clear)

    @tool(
        description="Fill a field atomically (ref/CSS). Prefer over browser_type for forms.",
        risk_class="browser",
    )
    async def browser_fill(target: str, text: str) -> str:
        return await engine.fill(target, text)

    @tool(description="Press a key (Enter, Tab, Escape, ArrowDown, etc.).", risk_class="browser")
    async def browser_press(key: str) -> str:
        return await engine.press(key)

    @tool(
        description="Scroll the page. direction=down|up|top|bottom; amount is pixels for up/down.",
        risk_class="browser",
    )
    async def browser_scroll(direction: str = "down", amount: int = 600) -> str:
        return await engine.scroll(direction=direction, amount=amount)

    @tool(description="Wait for a CSS selector (or timeout_ms if selector empty).", risk_class="browser")
    async def browser_wait(selector: str = "", timeout_ms: int = 3000) -> str:
        return await engine.wait(selector=selector, timeout_ms=timeout_ms)

    @tool(description="Screenshot the current page into session artifacts.")
    async def browser_screenshot(path: str = "artifacts/page.png") -> str:
        saved = await engine.screenshot(path)
        return f"Saved {saved}"

    @tool(
        description=(
            "Run JavaScript in the page. Pass an expression or () => value function body. "
            "Use for DOM queries, SPA state, or when refs are insufficient."
        ),
        risk_class="browser",
    )
    async def browser_evaluate(expression: str) -> str:
        return await engine.evaluate(expression)

    @tool(
        description=(
            "Raw Chrome DevTools Protocol call on the active page "
            "(e.g. method=Runtime.evaluate, params_json='{\"expression\":\"1+1\"}'). "
            "Input/Browser/Target/cookie mutations are denied."
        ),
        risk_class="browser",
    )
    async def browser_cdp(method: str, params_json: str = "{}") -> str:
        return await engine.cdp(method, params_json)

    @tool(
        description=(
            "Manage agent browser tabs. action=list|new|close|select. "
            "index required for select; optional for close (defaults to active)."
        ),
        risk_class="browser",
    )
    async def browser_tabs(action: str = "list", index: int = -1) -> str:
        idx = None if index < 0 else index
        return await engine.tabs_action(action, index=idx)

    @tool(
        description=(
            "Lock or unlock exclusive browser control for a multi-step flow. "
            "action=lock|unlock. Lock before a click/type sequence; unlock when done."
        ),
        risk_class="browser",
    )
    async def browser_lock(action: str = "lock") -> str:
        a = (action or "lock").strip().lower()
        if a == "unlock":
            return engine.unlock()
        return engine.lock()

    @tool(
        description=(
            "Disconnect the browser session. Headless: quits Chromium. "
            "Comet/CDP: closes the agent tab and disconnects — does not quit Comet."
        )
    )
    async def browser_close() -> str:
        return await engine.close()

    @tool(description="Navigate to a URL and return title + text preview.")
    async def browse(url: str) -> str:
        return await engine.open(url, screenshot=True, include_text=True)

    @tool(description="Extract text matching a CSS selector from the current page.")
    async def extract(selector: str = "body") -> str:
        return await engine.extract(selector)

    @tool(description="Take a screenshot of the current page into session artifacts.")
    async def screenshot(path: str = "artifacts/page.png") -> str:
        saved = await engine.screenshot(path)
        return f"Saved {saved}"

    for t in (
        browser_connect,
        browser_open,
        browser_snapshot,
        browser_click,
        browser_type,
        browser_fill,
        browser_press,
        browser_scroll,
        browser_wait,
        browser_screenshot,
        browser_evaluate,
        browser_cdp,
        browser_tabs,
        browser_lock,
        browser_close,
        browse,
        extract,
        screenshot,
    ):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg

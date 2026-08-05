"""Interactive Playwright browser tools (optional extra).

Backends:
  - auto (default): prefer Comet/CDP when reachable, else headless Chromium
  - headless: fresh Chromium, no cookies/login
  - comet/cdp: attach to running Comet/Chrome via CDP (uses your login);
    auto-falls back to headless if CDP is down
  - docker: Chromium in a hardened container with CDP (falls back to headless)

Env:
  KAGEHA_BROWSER_MODE=auto|headless|comet|cdp|docker
  KAGEHA_COMET_CDP=http://127.0.0.1:9222
  KAGEHA_BROWSER_DOCKER_IMAGE=browserless/chrome:latest

Prefer web_fetch (core) for public static pages; use this pack for JS apps,
logins, and multi-step UI. Snapshot returns AX refs (e0…) — act, then re-snapshot.
Do not screenshot every step unless you need vision evidence.
"""

from __future__ import annotations

import json
import time
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
    engine = BrowserEngine(
        artifact_root=lambda p: ctx.workspace.path(p),
        session_key=str(getattr(ctx.workspace, "run_id", "") or ""),
    )

    @tool(
        description=(
            "Attach browser tools to a backend. "
            "target=auto (default) prefers Comet/CDP when reachable, else headless. "
            "target=comet|cdp uses logged-in Comet/Chrome via CDP and auto-falls back "
            "to headless if CDP is down — do not stop to ask for /comet unless login "
            "cookies are required. target=docker runs Chromium in a sandbox; "
            "target=headless launches a fresh host Chromium."
        ),
        risk_class="browser",
    )
    async def browser_connect(target: str = "auto", endpoint: str = "") -> str:
        return await engine.connect(target=target, endpoint=endpoint)

    @tool(
        description=(
            "Open a URL in the agent-owned browser tab (new tab on Comet/CDP — never "
            "the user's focused tab). Returns AX snapshot refs + optional screenshot. "
            "For public static pages prefer web_fetch (faster, no Chromium)."
        ),
        risk_class="browser",
    )
    async def browser_open(url: str, screenshot: bool = False, include_text: bool = False) -> str:
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
        return await engine.snapshot(limit=limit, compact=compact, include_text=include_text)

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

    @tool(
        description="Select a native <select> option by visible label or value.",
        risk_class="browser",
    )
    async def browser_select(target: str, option: str) -> str:
        return await engine.select(target, option)

    @tool(
        description=(
            "Upload one or more workspace files through a file input. "
            "paths_json is a JSON array of session-relative paths."
        ),
        risk_class="browser",
    )
    async def browser_upload(target: str, paths_json: str) -> str:
        try:
            raw_paths = json.loads(paths_json)
        except json.JSONDecodeError as exc:
            return f"ERROR: paths_json must be a JSON array: {exc}"
        if not isinstance(raw_paths, list) or not raw_paths:
            return "ERROR: paths_json must be a non-empty JSON array"
        paths = [str(ctx.workspace.path(str(path))) for path in raw_paths[:8]]
        return await engine.upload(target, paths)

    @tool(description="Press a key (Enter, Tab, Escape, ArrowDown, etc.).", risk_class="browser")
    async def browser_press(key: str) -> str:
        return await engine.press(key)

    @tool(
        description="Scroll the page. direction=down|up|top|bottom; amount is pixels for up/down.",
        risk_class="browser",
    )
    async def browser_scroll(direction: str = "down", amount: int = 600) -> str:
        return await engine.scroll(direction=direction, amount=amount)

    @tool(
        description="Wait for a CSS selector (or timeout_ms if selector empty).",
        risk_class="browser",
    )
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
            "Execute a bounded sequence of browser actions using the current snapshot, "
            "then return one final snapshot. This avoids a model round-trip after every "
            "click/fill/press. actions_json is an array (max 12) of objects with action="
            "click|fill|type|select|press|scroll|wait and the corresponding target/text/option/key/"
            "direction/amount/selector/timeout_ms fields. Stop on the first failure."
        ),
        risk_class="browser",
    )
    async def browser_batch(actions_json: str, final_snapshot: bool = True) -> str:
        try:
            actions = json.loads(actions_json or "[]")
        except json.JSONDecodeError as exc:
            return f"ERROR: actions_json must be a JSON array: {exc}"
        if not isinstance(actions, list) or not actions:
            return "ERROR: actions_json must be a non-empty JSON array"
        if len(actions) > 12:
            return "ERROR: browser_batch accepts at most 12 actions"

        completed: list[str] = []
        timings: list[dict[str, object]] = []
        for idx, raw in enumerate(actions):
            if not isinstance(raw, dict):
                return f"ERROR: action {idx} must be an object"
            action = str(raw.get("action") or "").strip().lower()
            action_started = time.perf_counter()
            try:
                if action == "click":
                    target = str(raw.get("target") or "")
                    if not target:
                        return f"ERROR: action {idx} click requires target"
                    await engine.click(target, resnapshot=False)
                elif action in {"fill", "type"}:
                    target = str(raw.get("target") or "")
                    if not target:
                        return f"ERROR: action {idx} {action} requires target"
                    text = str(raw.get("text") or "")
                    if action == "fill":
                        await engine.fill(target, text)
                    else:
                        await engine.type_text(
                            target,
                            text,
                            clear=bool(raw.get("clear", True)),
                        )
                elif action == "select":
                    target = str(raw.get("target") or "")
                    option = str(raw.get("option") or raw.get("text") or "")
                    if not target or not option:
                        return f"ERROR: action {idx} select requires target and option"
                    await engine.select(target, option)
                elif action == "press":
                    key = str(raw.get("key") or "")
                    if not key:
                        return f"ERROR: action {idx} press requires key"
                    await engine.press(key)
                elif action == "scroll":
                    await engine.scroll(
                        direction=str(raw.get("direction") or "down"),
                        amount=int(raw.get("amount") or 600),
                    )
                elif action == "wait":
                    await engine.wait(
                        selector=str(raw.get("selector") or ""),
                        timeout_ms=int(raw.get("timeout_ms") or 1000),
                    )
                else:
                    return f"ERROR: action {idx} has unsupported action={action!r}"
            except Exception as exc:  # noqa: BLE001
                return (
                    f"ERROR: browser_batch stopped at action {idx} ({action}): {exc}\n"
                    f"completed: {completed}"
                )
            completed.append(action)
            timings.append(
                {
                    "index": idx,
                    "action": action,
                    "duration_ms": round((time.perf_counter() - action_started) * 1000.0, 1),
                }
            )

        summary = (
            f"completed {len(completed)} browser actions: {', '.join(completed)}\n"
            f"timings: {json.dumps(timings, separators=(',', ':'))}"
        )
        if final_snapshot:
            return summary + "\n\n" + await engine.snapshot(include_text=False)
        page = await engine.ensure_page()
        return summary + f"\nurl: {page.url}"

    @tool(
        description=(
            "Raw Chrome DevTools Protocol call on the active page "
            '(e.g. method=Runtime.evaluate, params_json=\'{"expression":"1+1"}\'). '
            "Input/Browser/Target/cookie mutations are denied."
        ),
        risk_class="browser",
    )
    async def browser_cdp(method: str, params_json: str = "{}") -> str:
        return await engine.cdp(method, params_json)

    @tool(
        description=(
            "Inspect the current page's console events, failed network requests, "
            "navigation/resource timings, DOM size, and CDP performance metrics. "
            "Use for live web-app diagnosis instead of ad-hoc JavaScript probes."
        ),
        risk_class="browser",
    )
    async def browser_diagnostics(clear: bool = False) -> str:
        return await engine.diagnostics(clear=clear)

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
        browser_select,
        browser_upload,
        browser_press,
        browser_scroll,
        browser_wait,
        browser_screenshot,
        browser_evaluate,
        browser_batch,
        browser_cdp,
        browser_diagnostics,
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

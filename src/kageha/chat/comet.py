"""Codex-style `/comet` command for launching and checking Comet CDP."""

from __future__ import annotations

import asyncio
import os
import platform
from urllib.parse import urlparse

import httpx

from kageha.harness.tools.browser import resolve_cdp_endpoint

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


async def _probe_cdp(endpoint: str) -> str | None:
    """Return the browser description when a Chrome DevTools endpoint is ready."""
    url = endpoint.rstrip("/") + "/json/version"
    try:
        async with httpx.AsyncClient(timeout=1.0, trust_env=False) as client:
            response = await client.get(url)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not payload.get("webSocketDebuggerUrl"):
        return None
    return str(payload.get("Browser") or "Chrome DevTools")


def _launch_args(endpoint: str) -> tuple[str, ...] | None:
    parsed = urlparse(endpoint)
    host = parsed.hostname or ""
    if host not in _LOOPBACK_HOSTS:
        return None
    port = parsed.port or 9222
    return (
        "open",
        "-na",
        "Comet",
        "--args",
        f"--remote-debugging-address={host}",
        f"--remote-debugging-port={port}",
    )


async def ensure_comet(*, launch: bool = True, timeout_s: float = 15.0) -> str:
    """Check Comet CDP and optionally launch a connectable Comet instance."""
    endpoint = resolve_cdp_endpoint()
    os.environ["KAGEHA_BROWSER_MODE"] = "comet"
    os.environ["KAGEHA_COMET_CDP"] = endpoint

    browser = await _probe_cdp(endpoint)
    if browser:
        return f"Comet ready · {browser} · CDP {endpoint}"
    if not launch:
        return f"Comet is not reachable at {endpoint}. Run /comet to start it."
    if platform.system() != "Darwin":
        return "Comet launch is currently supported only on macOS."

    args = _launch_args(endpoint)
    if args is None:
        return (
            f"Comet CDP is configured at non-local endpoint {endpoint}; "
            "Kageha will check it but will not launch a local app for it."
        )
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
    except FileNotFoundError:
        return "Could not launch Comet: macOS `open` is unavailable."
    if process.returncode:
        detail = (stderr or b"").decode(errors="replace").strip()
        return f"Could not launch Comet: {detail or f'open exited {process.returncode}'}"

    attempts = max(1, int(max(timeout_s, 0.25) / 0.25))
    for _ in range(attempts):
        await asyncio.sleep(0.25)
        browser = await _probe_cdp(endpoint)
        if browser:
            return (
                f"Comet started · {browser} · CDP {endpoint}\n"
                "Browser tools will use this Comet session and its login cookies."
            )
    return (
        f"Comet opened, but CDP did not become reachable at {endpoint}. "
        "Check the Comet window and run /comet status."
    )


async def handle_comet_command(line: str) -> tuple[bool, str]:
    """Handle `/comet` and `/comet status`."""
    text = (line or "").strip()
    if not text.lower().startswith("/comet"):
        return False, ""
    parts = text.split()
    action = parts[1].lower() if len(parts) > 1 else "start"
    if len(parts) > 2 or action not in {"start", "status"}:
        return True, "Usage: /comet [start|status]"
    return True, await ensure_comet(launch=action == "start")

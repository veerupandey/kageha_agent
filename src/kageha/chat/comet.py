"""`/comet` command for launching and checking Comet CDP."""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path
from urllib.parse import urlparse

import httpx

from kageha.harness.tools.browser import resolve_cdp_endpoint

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_COMET_BINARIES = (
    Path("/Applications/Comet.app/Contents/MacOS/Comet"),
    Path.home() / "Applications/Comet.app/Contents/MacOS/Comet",
)


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


def _comet_binary() -> Path | None:
    for path in _COMET_BINARIES:
        if path.is_file():
            return path
    return None


async def _comet_process_running() -> bool:
    """True when a main Comet browser process is alive."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "pgrep",
            "-x",
            "Comet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False
    out, _ = await proc.communicate()
    return proc.returncode == 0 and bool((out or b"").strip())


async def _quit_comet(*, timeout_s: float = 12.0) -> bool:
    """Quit Comet so a debug-enabled relaunch can take the profile lock."""
    if not await _comet_process_running():
        return True
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e",
            'tell application "Comet" to quit',
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
    except FileNotFoundError:
        pass

    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if not await _comet_process_running():
            return True
        await asyncio.sleep(0.25)

    # Last resort — profile lock must be released for CDP flags to apply.
    try:
        proc = await asyncio.create_subprocess_exec(
            "killall",
            "Comet",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except (FileNotFoundError, asyncio.TimeoutError):
        pass
    await asyncio.sleep(0.5)
    return not await _comet_process_running()


def _port_from_endpoint(endpoint: str) -> int:
    parsed = urlparse(endpoint)
    return parsed.port or 9222


def _host_from_endpoint(endpoint: str) -> str | None:
    parsed = urlparse(endpoint)
    host = parsed.hostname or ""
    if host not in _LOOPBACK_HOSTS:
        return None
    return "127.0.0.1" if host in {"localhost", "::1"} else host


def _open_launch_args(endpoint: str) -> tuple[str, ...] | None:
    """Fallback LaunchServices launch (less reliable for debug flags)."""
    host = _host_from_endpoint(endpoint)
    if host is None:
        return None
    port = _port_from_endpoint(endpoint)
    return (
        "open",
        "-n",
        "-g",
        "-a",
        "Comet",
        "--args",
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={host}",
        "--remote-allow-origins=*",
    )


def _binary_launch_args(endpoint: str) -> tuple[str, ...] | None:
    host = _host_from_endpoint(endpoint)
    binary = _comet_binary()
    if host is None or binary is None:
        return None
    port = _port_from_endpoint(endpoint)
    return (
        str(binary),
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={host}",
        "--remote-allow-origins=*",
    )


async def _launch_comet_detached(endpoint: str) -> str | None:
    """Start Comet with CDP enabled, detached from the chat TTY.

    Prefer launching the app binary directly — ``open --args`` is ignored when
    an existing Comet instance already owns the profile.
    """
    args = _binary_launch_args(endpoint) or _open_launch_args(endpoint)
    if args is None:
        return (
            f"Comet CDP is configured at non-local endpoint {endpoint}; "
            "Kageha will check it but will not launch a local app for it."
        )

    via_binary = not args[0].endswith("open") and args[0] != "open"
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except FileNotFoundError:
        return "Could not launch Comet: launcher unavailable."

    if via_binary:
        # Browser process stays running — only fail-fast if it dies immediately.
        await asyncio.sleep(0.6)
        if process.returncode not in (0, None):
            return f"Comet exited immediately (code {process.returncode})."
        return None

    try:
        await asyncio.wait_for(process.wait(), timeout=5.0)
    except asyncio.TimeoutError:
        return None
    if process.returncode not in (0, None):
        return f"Could not launch Comet: open exited {process.returncode}"
    return None


async def ensure_comet(*, launch: bool = True, timeout_s: float = 15.0) -> str:
    """Check Comet CDP and optionally (re)launch a connectable Comet instance."""
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
    if _host_from_endpoint(endpoint) is None:
        return (
            f"Comet CDP is configured at non-local endpoint {endpoint}; "
            "Kageha will check it but will not launch a local app for it."
        )

    restarted = False
    if await _comet_process_running():
        # Chromium ignores --remote-debugging-port when another instance already
        # holds the default profile — must quit first, then relaunch with flags.
        restarted = True
        if not await _quit_comet():
            return (
                "Could not quit the existing Comet process to enable CDP.\n"
                "Quit Comet fully (Cmd+Q) and run /comet again."
            )

    err = await _launch_comet_detached(endpoint)
    if err:
        return err

    attempts = max(1, int(max(timeout_s, 0.25) / 0.25))
    for _ in range(attempts):
        await asyncio.sleep(0.25)
        browser = await _probe_cdp(endpoint)
        if browser:
            prefix = "Comet restarted" if restarted else "Comet started"
            return (
                f"{prefix} · {browser} · CDP {endpoint}\n"
                "Browser tools will use this Comet session and its login cookies.\n"
                "Chat stays in the foreground — click the Comet window if you need to log in."
            )
    return (
        f"Comet launch was requested, but CDP is not reachable at {endpoint}.\n"
        "Quit Comet fully (Cmd+Q), then run /comet again.\n"
        "Check with /comet status."
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

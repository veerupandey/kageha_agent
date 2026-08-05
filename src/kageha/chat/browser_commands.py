"""Native slash commands: ``/browser`` and ``/research``."""

from __future__ import annotations

from kageha.harness.browser.backends import (
    format_backend_list,
    resolve_backend_spec,
)
from kageha.harness.browser.prefs import (
    apply_browser_prefs,
    load_browser_prefs,
    save_browser_prefs,
    set_backend,
    status_text,
)

USAGE_BROWSER = """\
Usage:
  /browser                 Status (current backend + CDP)
  /browser list            List backends
  /browser use <backend>   Select backend (persists to ~/.kageha/browser.json)
  /browser <backend>       Shorthand for use
  /browser cdp <url>       Custom CDP endpoint + backend=cdp
  /browser comet [start|status]
  /browser lightpanda      Use Lightpanda CDP (start server separately)
  /browser pack on|off     Force-enable/disable browser tool pack
  /browser depth flash|standard|deep
  /browser diagnose <url>  Open URL and return console/network/performance diagnostics

Backends: http · chromium · lightpanda · comet · chrome · cdp · docker · headless
"""

USAGE_RESEARCH = """\
Usage:
  /research <query>
  /research flash|standard|deep <query>
  /research depth flash|standard|deep
"""

BROWSER_ACTIONS = frozenset(
    {
        "help",
        "-h",
        "--help",
        "?",
        "list",
        "ls",
        "backends",
        "status",
        "diagnose",
        "diagnostics",
        "profile",
        "pack",
        "depth",
        "cdp",
        "comet",
        "use",
        # Backend shorthands.
        "http",
        "chromium",
        "lightpanda",
        "chrome",
        "docker",
        "headless",
    }
)


def _parts(line: str) -> list[str]:
    return (line or "").strip().split()


async def handle_browser_command(line: str) -> tuple[bool, str]:
    """Handle ``/browser …``. Returns (handled, message)."""
    text = (line or "").strip()
    low = text.lower()
    if low != "/browser" and not low.startswith("/browser "):
        return False, ""

    parts = _parts(text)
    if len(parts) == 1:
        return True, status_text()

    action = parts[1].lower()

    if action in {"help", "-h", "--help", "?"}:
        return True, USAGE_BROWSER.strip()

    if action in {"list", "ls", "backends"}:
        prefs = load_browser_prefs()
        return True, format_backend_list(current=prefs.backend)

    if action == "status":
        return True, status_text()

    if action in {"diagnose", "diagnostics", "profile"}:
        if len(parts) < 3:
            return True, "Usage: /browser diagnose <url>"
        return True, await diagnose_url(parts[2])

    if action == "pack":
        if len(parts) < 3 or parts[2].lower() not in {"on", "off", "1", "0"}:
            return True, "Usage: /browser pack on|off"
        on = parts[2].lower() in {"on", "1"}
        prefs = load_browser_prefs()
        prefs.enable_browser_pack = on
        save_browser_prefs(prefs)
        apply_browser_prefs(prefs)
        return True, status_text()

    if action == "depth":
        if len(parts) < 3:
            return True, "Usage: /browser depth flash|standard|deep"
        depth = parts[2].lower()
        if depth not in {"flash", "standard", "deep", "fast", "std"}:
            return True, "Usage: /browser depth flash|standard|deep"
        if depth == "fast":
            depth = "flash"
        if depth == "std":
            depth = "standard"
        prefs = load_browser_prefs()
        prefs.research_depth = depth
        save_browser_prefs(prefs)
        apply_browser_prefs(prefs)
        return True, status_text()

    if action == "cdp":
        if len(parts) < 3:
            return True, "Usage: /browser cdp http://127.0.0.1:9222"
        url = parts[2]
        try:
            set_backend("cdp", cdp=url, enable_pack=True)
        except ValueError as e:
            return True, str(e)
        return True, status_text() + "\n\nBrowser pack enabled for next agent turn."

    if action == "comet":
        sub = parts[2].lower() if len(parts) > 2 else "start"
        try:
            set_backend("comet", enable_pack=True)
        except ValueError as e:
            return True, str(e)
        from kageha.chat.comet import ensure_comet

        if sub == "status":
            msg = await ensure_comet(launch=False)
        elif sub in {"start", "on"}:
            msg = await ensure_comet(launch=True)
        else:
            return True, "Usage: /browser comet [start|status]"
        return True, status_text() + "\n\n" + msg

    if action == "use":
        if len(parts) < 3:
            return True, "Usage: /browser use <backend>\n\n" + format_backend_list(
                current=load_browser_prefs().backend
            )
        name = parts[2]
        cdp = parts[3] if len(parts) > 3 else None
        return True, await _select(name, cdp=cdp)

    # Shorthand: /browser lightpanda | chromium | headless | docker | http | chrome
    if resolve_backend_spec(action):
        cdp = parts[2] if len(parts) > 2 else None
        return True, await _select(action, cdp=cdp)

    return True, f"Unknown /browser action {action!r}.\n\n{USAGE_BROWSER}"


async def diagnose_url(url: str) -> str:
    """One-shot browser diagnostics shared by chat, CLI, and WebUI."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "file"}:
        return "ERROR: URL must use http://, https://, or file://"
    apply_browser_prefs()
    from kageha.harness.browser import BrowserEngine

    engine = BrowserEngine()
    try:
        await engine.open(url, include_text=False)
        return await engine.diagnostics(clear=False)
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: browser diagnostics failed: {exc}"
    finally:
        await engine.close()


async def _select(name: str, *, cdp: str | None = None) -> str:
    try:
        prefs = set_backend(name, cdp=cdp)
    except ValueError as e:
        return str(e) + "\n\n" + format_backend_list(current=load_browser_prefs().backend)

    spec = resolve_backend_spec(prefs.backend)
    lines = [status_text(), ""]
    if spec and spec.id == "lightpanda":
        lines.append(
            "Tip: start Lightpanda if needed:\n  lightpanda serve --host 127.0.0.1 --port 9222"
        )
    if spec and spec.needs_pack:
        lines.append("Browser pack enabled for the next agent turn.")
    if spec and spec.id == "comet":
        from kageha.chat.comet import ensure_comet

        lines.append(await ensure_comet(launch=True))
    return "\n".join(lines)


async def handle_research_command(line: str) -> tuple[bool, str]:
    """Handle ``/research …`` — runs blink research_run natively (no LLM loop)."""
    text = (line or "").strip()
    low = text.lower()
    if low != "/research" and not low.startswith("/research "):
        return False, ""

    parts = _parts(text)
    if len(parts) == 1 or parts[1].lower() in {"help", "-h", "--help", "?"}:
        return True, USAGE_RESEARCH.strip()

    if parts[1].lower() == "depth":
        if len(parts) < 3:
            return True, "Usage: /research depth flash|standard|deep"
        depth = parts[2].lower()
        if depth not in {"flash", "standard", "deep"}:
            return True, "Usage: /research depth flash|standard|deep"
        prefs = load_browser_prefs()
        prefs.research_depth = depth
        save_browser_prefs(prefs)
        apply_browser_prefs(prefs)
        return True, status_text()

    depth = load_browser_prefs().research_depth or "flash"
    query_parts = parts[1:]
    if parts[1].lower() in {"flash", "standard", "deep", "fast", "std"}:
        depth = parts[1].lower()
        if depth == "fast":
            depth = "flash"
        if depth == "std":
            depth = "standard"
        query_parts = parts[2:]
    query = " ".join(query_parts).strip()
    if not query:
        return True, USAGE_RESEARCH.strip()

    from kageha.research.backend import research_run
    from kageha.research.citations import strip_sources_marker

    apply_browser_prefs()
    out = await research_run(query, depth=depth)
    return True, strip_sources_marker(out)


async def handle_browser_or_research(line: str) -> tuple[bool, str]:
    """Dispatch ``/browser`` or ``/research``."""
    handled, msg = await handle_browser_command(line)
    if handled:
        return True, msg
    return await handle_research_command(line)

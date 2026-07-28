"""Canonical browser / headless backend catalog for Kageha."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendSpec:
    id: str
    kind: str  # interactive | headless | http
    label: str
    description: str
    aliases: tuple[str, ...] = ()
    needs_pack: bool = False
    env_browser_mode: str = ""
    env_headless: str = ""


BACKENDS: tuple[BackendSpec, ...] = (
    BackendSpec(
        id="auto",
        kind="interactive",
        label="Auto (Comet → headless)",
        description="Prefer Comet/CDP when reachable; otherwise headless Chromium.",
        aliases=("prefer-comet", "prefer_comet", "default"),
        needs_pack=True,
        env_browser_mode="auto",
        env_headless="chromium",
    ),
    BackendSpec(
        id="http",
        kind="http",
        label="HTTP extract",
        description="No browser — web_fetch / research_run flash only (fastest reads).",
        aliases=("fetch", "none", "off"),
        env_headless="http",
    ),
    BackendSpec(
        id="chromium",
        kind="headless",
        label="Chromium (warm headless)",
        description="Warm Playwright Chromium pool for JS page extract.",
        aliases=("chrome-headless", "pw", "playwright"),
        needs_pack=False,  # pool uses playwright extra; interactive pack separate
        env_browser_mode="headless",
        env_headless="chromium",
    ),
    BackendSpec(
        id="lightpanda",
        kind="headless",
        label="Lightpanda",
        description="Zig CDP headless (~10× lighter). Run: lightpanda serve --port 9222",
        aliases=("lp", "panda"),
        env_browser_mode="cdp",
        env_headless="lightpanda",
    ),
    BackendSpec(
        id="comet",
        kind="interactive",
        label="Comet (logged-in)",
        description="Your Comet browser via CDP — cookies/login. /browser comet start",
        aliases=("logged_in", "logged-in"),
        needs_pack=True,
        env_browser_mode="comet",
        env_headless="cdp",
    ),
    BackendSpec(
        id="chrome",
        kind="interactive",
        label="Chrome (CDP)",
        description="Attach to Chrome/Chromium with remote debugging.",
        aliases=("google-chrome",),
        needs_pack=True,
        env_browser_mode="cdp",
        env_headless="cdp",
    ),
    BackendSpec(
        id="cdp",
        kind="interactive",
        label="Custom CDP",
        description="Any CDP endpoint (set with /browser cdp <url>).",
        aliases=("endpoint", "devtools"),
        needs_pack=True,
        env_browser_mode="cdp",
        env_headless="cdp",
    ),
    BackendSpec(
        id="docker",
        kind="interactive",
        label="Docker sandbox",
        description="Hardened Chromium in Docker (+ optional noVNC).",
        aliases=("sandbox",),
        needs_pack=True,
        env_browser_mode="docker",
        env_headless="cdp",
    ),
    BackendSpec(
        id="headless",
        kind="interactive",
        label="Headless Chromium (interactive)",
        description="Full browser_* tools against a fresh headless Chromium.",
        aliases=("agent",),
        needs_pack=True,
        env_browser_mode="headless",
        env_headless="chromium",
    ),
)


def all_backend_ids() -> list[str]:
    return [b.id for b in BACKENDS]


def resolve_backend_spec(name: str) -> BackendSpec | None:
    key = (name or "").strip().lower()
    if not key:
        return None
    for b in BACKENDS:
        if key == b.id or key in b.aliases:
            return b
    return None


def format_backend_list(*, current: str = "") -> str:
    lines = ["Available backends:"]
    for b in BACKENDS:
        mark = "*" if b.id == current else " "
        lines.append(f"{mark} {b.id:12} [{b.kind:11}] {b.description}")
    lines.append("")
    lines.append("Usage: /browser use <backend>   /browser list   /browser status")
    lines.append("       /browser comet start     /browser cdp http://127.0.0.1:9222")
    lines.append("       /research <query>        /research standard <query>")
    return "\n".join(lines)

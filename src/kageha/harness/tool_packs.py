"""Core vs optional tool-pack selection (trimmed harness).

Default loads CORE packs only. Optional packs opt in via:

1. ``KAGEHA_TOOL_PACKS=browser,computer,media`` or ``all`` (highest precedence)
2. ``tools.yaml`` ``packs: [browser, ...]`` or ``packs: all``
3. otherwise core only

macOS computer-use is auto-enabled when ``cua-driver`` is installed, unless
explicitly opted out with ``-computer`` / ``nocomputer`` in the pack list or
``KAGEHA_COMPUTER=0``.
"""

from __future__ import annotations

import os
import platform
from typing import Any

# label -> "module:attr"
CORE_PACK_IMPORTS: list[tuple[str, str]] = [
    ("forge", "kageha.harness.tools.forge:register_forge_tools"),
    ("skills", "kageha.harness.tools.skills_tools:register_skills_tools"),
    ("mcp", "kageha.harness.tools.mcp_tools:register_mcp_tools"),
    ("memory", "kageha.harness.tools.memory_tools:register_memory_tools"),
    ("subagent", "kageha.agents.subagent:register_subagent_tools"),
    ("research", "kageha.harness.tools.research:register_research_tools"),
]

# Optional native packs (explicit opt-in via KAGEHA_TOOL_PACKS / tools.yaml).
OPTIONAL_PACK_IMPORTS: list[tuple[str, str]] = [
    ("browser", "kageha.harness.tools.browser:register_browser_tools"),
    ("computer", "kageha.harness.tools.computer:register_computer_tools"),
    ("media", "kageha.harness.tools.media:register_media_tools"),
]

CORE_PACK_NAMES: frozenset[str] = frozenset(n for n, _ in CORE_PACK_IMPORTS)
OPTIONAL_PACK_NAMES: frozenset[str] = frozenset(n for n, _ in OPTIONAL_PACK_IMPORTS)
ALL_PACK_NAMES: frozenset[str] = CORE_PACK_NAMES | OPTIONAL_PACK_NAMES

_DISABLE_COMPUTER = frozenset({"nocomputer", "-computer", "no-computer", "no_computer"})
_DISABLE_BROWSER = frozenset({"nobrowser", "-browser", "no-browser", "no_browser"})


def _parse_pack_tokens(raw: str | list[Any] | None) -> list[str] | None:
    """Return normalized pack tokens, or None if unset."""
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.lower() in {"all", "*"}:
            return ["all"]
        parts = [
            p.strip().lower() for p in text.replace(";", ",").split(",") if p.strip()
        ]
        return parts or None
    if isinstance(raw, list):
        parts = [str(p).strip().lower() for p in raw if str(p).strip()]
        return parts or None
    return None


def _computer_opted_out(tokens: list[str] | None, environ: dict[str, str]) -> bool:
    flag = (environ.get("KAGEHA_COMPUTER") or "").strip().lower()
    if flag in {"0", "false", "off", "no"}:
        return True
    if tokens and any(t in _DISABLE_COMPUTER for t in tokens):
        return True
    return False


def _should_auto_enable_computer(
    tokens: list[str] | None, environ: dict[str, str]
) -> bool:
    if platform.system() != "Darwin":
        return False
    # /computer pack on|off|auto → ~/.kageha/computer.json
    try:
        from kageha.harness.tools.computer_prefs import (
            apply_computer_prefs,
            load_computer_prefs,
        )

        apply_computer_prefs()
        prefs = load_computer_prefs()
        if prefs.pack == "off":
            return False
        if prefs.pack == "on":
            return True
    except Exception:  # noqa: BLE001
        pass
    if _computer_opted_out(tokens, environ):
        return False
    # Driver presence alone must not enlarge the default process.
    return False


def _browser_opted_out(tokens: list[str] | None, environ: dict[str, str]) -> bool:
    flag = (environ.get("KAGEHA_BROWSER_PACK") or "").strip().lower()
    if flag in {"0", "false", "off", "no"}:
        return True
    if tokens and any(t in _DISABLE_BROWSER for t in tokens):
        return True
    return False


def _should_auto_enable_browser(
    tokens: list[str] | None, environ: dict[str, str]
) -> bool:
    """Enable browser pack when explicitly requested.

    Triggers:
      - ``KAGEHA_BROWSER_PACK=1``
      - ``~/.kageha/browser.json`` with ``enable_browser_pack: true``
        (set by ``/browser use comet|headless|…``)

    ``KAGEHA_BROWSER_MODE`` alone does **not** pull the pack (avoids .env
    surprise); use ``/browser comet`` or ``KAGEHA_TOOL_PACKS=browser``.
    """
    if _browser_opted_out(tokens, environ):
        return False
    flag = (environ.get("KAGEHA_BROWSER_PACK") or "").strip().lower()
    if flag in {"1", "true", "on", "yes"}:
        return True
    try:
        from kageha.harness.browser.prefs import load_browser_prefs

        prefs = load_browser_prefs()
        if prefs.enable_browser_pack and prefs.auto_pack:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def resolve_enabled_packs(
    *,
    policy: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> list[str]:
    """Return ordered pack labels to load (core first, then enabled optionals)."""
    environ = env if env is not None else os.environ
    env_tokens = _parse_pack_tokens(environ.get("KAGEHA_TOOL_PACKS"))
    pol = policy if policy is not None else {}
    yaml_tokens = _parse_pack_tokens(pol.get("packs"))

    tokens = env_tokens if env_tokens is not None else yaml_tokens
    want_all = False
    optional: list[str] = []
    if tokens is not None:
        if any(t in {"all", "*"} for t in tokens):
            want_all = True
        else:
            for t in tokens:
                if t in _DISABLE_COMPUTER or t in _DISABLE_BROWSER:
                    continue
                if t in OPTIONAL_PACK_NAMES and t not in optional:
                    optional.append(t)
                elif t in CORE_PACK_NAMES:
                    continue  # core always on
                # unknown tokens ignored

    enabled: list[str] = [n for n, _ in CORE_PACK_IMPORTS]
    if want_all:
        for n, _ in OPTIONAL_PACK_IMPORTS:
            if n not in enabled:
                enabled.append(n)
    else:
        for n in optional:
            if n not in enabled:
                enabled.append(n)

    if "computer" not in enabled and _should_auto_enable_computer(tokens, environ):
        enabled.append("computer")
    if "browser" not in enabled and _should_auto_enable_browser(tokens, environ):
        enabled.append("browser")
    # /computer pack off (or KAGEHA_COMPUTER=0) wins over yaml/env pack lists.
    if _computer_force_disabled(environ):
        enabled = [n for n in enabled if n != "computer"]
    return enabled


def _computer_force_disabled(environ: dict[str, str]) -> bool:
    """True when prefs/env explicitly disable computer pack."""
    flag = (environ.get("KAGEHA_COMPUTER") or "").strip().lower()
    if flag in {"0", "false", "off", "no"}:
        return True
    try:
        from kageha.harness.tools.computer_prefs import load_computer_prefs

        return load_computer_prefs().pack == "off"
    except Exception:  # noqa: BLE001
        return False


def pack_imports_for(enabled: list[str] | set[str]) -> list[tuple[str, str]]:
    """Import paths for enabled packs, stable order (core then optional)."""
    want = set(enabled)
    out: list[tuple[str, str]] = []
    for label, path in CORE_PACK_IMPORTS + OPTIONAL_PACK_IMPORTS:
        if label in want:
            out.append((label, path))
    return out


def summarize_packs(enabled: list[str]) -> str:
    opt = [p for p in enabled if p in OPTIONAL_PACK_NAMES]
    return (
        f"core={','.join(n for n, _ in CORE_PACK_IMPORTS)} "
        f"optional={','.join(opt) if opt else '(none)'} "
        f"total={len(enabled)}"
    )

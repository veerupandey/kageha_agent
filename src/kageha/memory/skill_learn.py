"""Closest Hermes skill learning — soft/unattended observe/refine + mid-run nudges.

Env ``KAGEHA_SKILL_LEARN``:
  hitl / unset — approvals required; mid-run nudges on (working-agent default)
  soft         — auto-approve observe/refine when interactive TTY; nudges on
  unattended   — auto-approve observe/refine/create/edit/patch/write_file on TTY
                 (delete always HITL); post-run distill auto-applies with evidence
  off          — no soft path; no mid-run nudges

Env ``KAGEHA_SKILL_LEARN_CHANNELS=1`` — allow soft/unattended when a channel
asker is set (Telegram/WhatsApp/…). Default off (channels stay HITL).
"""

from __future__ import annotations

import os
import sys
from typing import Any


def skill_learn_mode() -> str:
    # Default hitl for a reliable working agent; Closest Hermes via soft/unattended.
    raw = (os.environ.get("KAGEHA_SKILL_LEARN") or "hitl").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return "off"
    if raw in {"soft"}:
        return "soft"
    if raw in {"unattended", "auto"}:
        return "unattended"
    return "hitl"


def skill_learn_channels_enabled() -> bool:
    return os.environ.get("KAGEHA_SKILL_LEARN_CHANNELS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def is_interactive_skill_learn(*, interactive: bool | None = None) -> bool:
    """True for local TTY chat without a channel asker (WhatsApp/etc.).

    Channel asker always wins (non-interactive for soft learn), unless
    ``KAGEHA_SKILL_LEARN_CHANNELS=1``.
    """
    try:
        from kageha.harness.approvals import _channel_asker

        if _channel_asker.get() is not None and not skill_learn_channels_enabled():
            return False
    except Exception:  # noqa: BLE001
        pass
    if interactive is not None:
        return bool(interactive)
    return bool(sys.stdin.isatty())


def skill_learn_soft_enabled(*, interactive: bool | None = None) -> bool:
    """Auto-approve observe/refine when mode is soft or unattended + interactive."""
    if skill_learn_mode() not in {"soft", "unattended"}:
        return False
    return is_interactive_skill_learn(interactive=interactive)


def skill_learn_unattended_enabled(*, interactive: bool | None = None) -> bool:
    """Auto-approve create/edit/patch/write_file (not delete) when unattended."""
    if skill_learn_mode() != "unattended":
        return False
    return is_interactive_skill_learn(interactive=interactive)


def skill_learn_nudges_enabled() -> bool:
    return skill_learn_mode() != "off"


def learning_nudge(active_skills: list[str], *, pitfalls: list[str]) -> str:
    """Next-turn instruction to close the Hermes observe/refine loop."""
    names = [n for n in active_skills if n][:4]
    if not names:
        return ""
    target = names[0]
    notes = "; ".join(p.strip() for p in pitfalls if p.strip())[:400]
    pit = f" Pitfalls seen: {notes}." if notes else ""
    return (
        "## Skill learning (Hermes)\n"
        f"Active skill(s): {', '.join(names)}.{pit}\n"
        f"Call `skill_manage` with action=`observe` on `{target}` to record "
        "the pitfall, then action=`refine` to fix the procedure steps "
        "(or OLD<<<>>>NEW surgical edit). Prefer improving the existing skill "
        "over inventing a new one."
    )


def collect_tool_pitfalls(results: list[Any], *, limit: int = 4) -> list[str]:
    """Extract short pitfall lines from tool result messages."""
    out: list[str] = []
    for r in results:
        content = getattr(r, "content", None) or ""
        name = getattr(r, "name", None) or "tool"
        text = str(content).strip()
        if not text:
            continue
        head = text.splitlines()[0][:160]
        low = head.lower()
        if head.startswith("ERROR:") or head.startswith("DENIED:") or "traceback" in low:
            out.append(f"{name}: {head}")
        if len(out) >= limit:
            break
    return out


def stamp_unattended_provenance(content: str) -> str:
    """Append a provenance footer for unattended skill creates/edits."""
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = f"learned: unattended ({stamp})"
    body = (content or "").rstrip()
    if "learned: unattended" in body:
        return body + ("\n" if not body.endswith("\n") else "")
    return body + f"\n\n<!-- {marker} -->\n"

"""Native slash command: ``/computer`` (pack, doctor, allowlist).

Bare ``/computer`` returns a short tip to type a task (primary slash is the
``computer_use`` skill). Admin subcommands (``status``, ``doctor``, ``pack``, …)
are handled here. ``/computer <natural language task>`` falls through to the
agent loop and activates ``computer_use`` (same as ``/computer_use``).
"""

from __future__ import annotations

USAGE = """\
Usage:
  /computer <task>          Activate computer_use skill and run the task
                            (same as /computer_use <task>)
  /computer status          Pack + driver + allowlist status
  /computer doctor          Full readiness probe (driver, TCC, tool model)
  /computer pack on|off|auto
                            Force-enable, disable, or driver-gated auto
  /computer allowlist       List per-app decisions
  /computer allow <bundle_id> always|once|deny
  /computer deny <bundle_id>
  /computer clear <bundle_id>

Tip: /computer open Calculator and compute 8+9
  or: computer_click_sequence(app=\"Calculator\", text=\"8+9=\")
"""

_BARE_COMPUTER_TIP = """\
Computer-use skill — type a task after `/computer`, then send.

Examples:
  /computer open Calculator and compute 8+9
  /computer open Chrome and go to https://kageha.ca

Pack / driver status: `/computer status`
Help: `/computer help`
"""

_ADMIN_ACTIONS = frozenset(
    {
        "status",
        "doctor",
        "pack",
        "allowlist",
        "apps",
        "list",
        "allow",
        "deny",
        "clear",
        "help",
        "-h",
        "--help",
        "?",
    }
)


def _parts(line: str) -> list[str]:
    return (line or "").strip().split()


def is_computer_admin_command(line: str) -> bool:
    """True for bare ``/computer`` or known admin subcommands (not skill tasks).

    Bare ``/computer`` is handled here only to show an activation tip — it does
    not run the agent. ``/computer <task>`` is *not* admin.
    """
    text = (line or "").strip()
    low = text.lower()
    if low != "/computer" and not low.startswith("/computer "):
        return False
    parts = _parts(text)
    if len(parts) <= 1:
        return True
    return parts[1].lower() in _ADMIN_ACTIONS


async def handle_computer_command(line: str) -> tuple[bool, str]:
    """Handle ``/computer …``. Returns (handled, message).

    Returns ``(False, \"\")`` for ``/computer <task>`` so the agent +
    ``computer_use`` skill can run the turn.
    """
    text = (line or "").strip()
    low = text.lower()
    if low != "/computer" and not low.startswith("/computer "):
        return False, ""

    if not is_computer_admin_command(text):
        return False, ""

    from kageha.harness.tools.computer_prefs import (
        apply_computer_prefs,
        set_pack_mode,
        status_text,
    )

    parts = _parts(text)
    if len(parts) == 1:
        # Primary slash is the skill — don't dump pack status on bare /computer.
        return True, _BARE_COMPUTER_TIP.strip()

    action = parts[1].lower()

    if action in {"help", "-h", "--help", "?"}:
        return True, USAGE.strip()

    if action == "status":
        apply_computer_prefs()
        return True, status_text()

    if action == "doctor":
        return True, await _doctor()

    if action == "pack":
        if len(parts) < 3:
            return True, "Usage: /computer pack on|off|auto"
        try:
            set_pack_mode(parts[2])
        except ValueError as exc:
            return True, str(exc)
        return True, status_text() + "\n\nPack mode applied for this process and saved."

    if action in {"allowlist", "apps", "list"}:
        return True, _format_allowlist()

    if action == "allow":
        if len(parts) < 4:
            return True, "Usage: /computer allow <bundle_id> always|once|deny"
        bundle = parts[2]
        decision = parts[3].lower()
        if decision not in {"always", "once", "deny"}:
            return True, "Usage: /computer allow <bundle_id> always|once|deny"
        from kageha.harness.tools.computer_allowlist import set_decision

        set_decision(bundle, decision)  # type: ignore[arg-type]
        return True, f"Allowlisted {bundle} → {decision}\n\n" + _format_allowlist()

    if action == "deny":
        if len(parts) < 3:
            return True, "Usage: /computer deny <bundle_id>"
        from kageha.harness.tools.computer_allowlist import set_decision

        set_decision(parts[2], "deny")
        return True, f"Denied {parts[2]}\n\n" + _format_allowlist()

    if action == "clear":
        if len(parts) < 3:
            return True, "Usage: /computer clear <bundle_id>"
        from kageha.harness.tools.computer_allowlist import clear_decision

        clear_decision(parts[2])
        return True, f"Cleared {parts[2]}\n\n" + _format_allowlist()

    return True, f"Unknown /computer action {action!r}.\n\n{USAGE}"


async def _doctor() -> str:
    from kageha.harness.tool_packs import resolve_enabled_packs
    from kageha.harness.tools.computer_prefs import apply_computer_prefs, status_text
    from kageha.harness.tools.computer_ready import ensure_computer_ready

    apply_computer_prefs()
    packs = resolve_enabled_packs()
    ready = await ensure_computer_ready(pack_enabled="computer" in packs)
    lines = [
        status_text(),
        "",
        "Doctor",
        f"  ok: {ready.ok}",
        f"  driver_ok: {ready.driver_ok}",
        f"  perms_ok: {ready.perms_ok}",
        f"  tool_model_ok: {ready.tool_model_ok}",
        f"  tool_model: {ready.tool_model_id or '(none)'}",
        f"  message: {ready.message}",
    ]
    for hint in ready.hints or []:
        lines.append(f"  hint: {hint}")
    return "\n".join(lines)


def _format_allowlist() -> str:
    from kageha.harness.tools.computer_allowlist import (
        BLOCKED_BUNDLE_IDS,
        allowlist_path,
        load_allowlist,
    )

    apps = load_allowlist()
    lines = [f"Allowlist ({allowlist_path()})", ""]
    if not apps:
        lines.append("(empty — HITL ask on first input per app)")
    else:
        for bid, meta in sorted(apps.items()):
            dec = meta.get("decision") or "?"
            name = meta.get("name") or ""
            lines.append(f"  {bid}: {dec}" + (f"  ({name})" if name else ""))
    lines.append("")
    lines.append("Hard-blocked (cannot allow):")
    for bid in sorted(BLOCKED_BUNDLE_IDS):
        lines.append(f"  {bid}")
    return "\n".join(lines)

"""Persistent computer-use prefs (~/.kageha/computer.json)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kageha.config import kageha_home


@dataclass
class ComputerPrefs:
    """``pack``: auto (driver-gated) | on (force) | off (disable)."""

    pack: str = "auto"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prefs_path() -> Path:
    return kageha_home() / "computer.json"


def load_computer_prefs() -> ComputerPrefs:
    path = prefs_path()
    if not path.is_file():
        return ComputerPrefs()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return ComputerPrefs()
    if not isinstance(raw, dict):
        return ComputerPrefs()
    pack = str(raw.get("pack") or "auto").strip().lower()
    if pack not in {"auto", "on", "off"}:
        pack = "auto"
    return ComputerPrefs(pack=pack)


def save_computer_prefs(prefs: ComputerPrefs) -> Path:
    path = prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prefs.to_dict(), indent=2) + "\n", encoding="utf-8")
    return path


def apply_computer_prefs(prefs: ComputerPrefs | None = None) -> ComputerPrefs:
    """Push pack mode into process env for ``resolve_enabled_packs``."""
    p = prefs or load_computer_prefs()
    if p.pack == "off":
        os.environ["KAGEHA_COMPUTER"] = "0"
        os.environ["KAGEHA_COMPUTER_FROM_PREFS"] = "1"
    elif p.pack == "on":
        os.environ["KAGEHA_COMPUTER"] = "1"
        os.environ["KAGEHA_COMPUTER_FROM_PREFS"] = "1"
    elif os.environ.get("KAGEHA_COMPUTER_FROM_PREFS") == "1":
        os.environ.pop("KAGEHA_COMPUTER", None)
        os.environ.pop("KAGEHA_COMPUTER_FROM_PREFS", None)
    return p


def set_pack_mode(mode: str) -> ComputerPrefs:
    mode = (mode or "").strip().lower()
    if mode in {"1", "true", "yes", "enable", "enabled"}:
        mode = "on"
    if mode in {"0", "false", "no", "disable", "disabled"}:
        mode = "off"
    if mode not in {"auto", "on", "off"}:
        raise ValueError("pack mode must be auto|on|off")
    prefs = ComputerPrefs(pack=mode)
    save_computer_prefs(prefs)
    return apply_computer_prefs(prefs)


def status_text() -> str:
    """Human-readable computer-use status (no async doctor)."""
    import platform

    from kageha.harness.tool_packs import resolve_enabled_packs
    from kageha.harness.tools import computer_driver as driver
    from kageha.harness.tools.computer_allowlist import allowlist_path, load_allowlist

    prefs = apply_computer_prefs()
    packs = resolve_enabled_packs()
    pack_on = "computer" in packs
    lines = [
        "Computer-use",
        f"  pack mode: {prefs.pack}  (prefs: {prefs_path()})",
        f"  pack loaded: {'yes' if pack_on else 'no'}",
        f"  platform: {platform.system()}",
        f"  driver: {driver.driver_bin() or '(missing)'}",
        f"  socket: {driver.socket_path() or '(none)'}",
        f"  transport: {driver.transport_mode()}",
        f"  allowlist: {allowlist_path()} ({len(load_allowlist())} apps)",
    ]
    if platform.system() != "Darwin":
        lines.append("  note: computer-use v1 is macOS-only")
    return "\n".join(lines)

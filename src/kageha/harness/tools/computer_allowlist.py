"""Per-app allowlist for computer-use (bundle_id → always|once|deny)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from kageha.config import kageha_home

Decision = Literal["always", "once", "deny"]

# Bundle ids / name substrings the agent must never drive.
BLOCKED_BUNDLE_IDS = frozenset(
    {
        "com.apple.Terminal",
        "com.googlecode.iterm2",
        "dev.warp.Warp-Stable",
        "com.apple.dt.Xcode",
        "com.trycua.driver",
        "ai.kageha.app",
        "com.kageha.app",
    }
)
BLOCKED_NAME_SUBSTR = (
    "terminal",
    "iterm",
    "warp",
    "kageha",
    "cuadriver",
    "cua driver",
)


def allowlist_path() -> Path:
    return kageha_home() / "computer_apps.json"


def load_allowlist() -> dict[str, dict[str, Any]]:
    path = allowlist_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    apps = data.get("apps") if isinstance(data, dict) else None
    if not isinstance(apps, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, val in apps.items():
        if isinstance(val, dict):
            out[str(key)] = val
        elif isinstance(val, str):
            out[str(key)] = {"decision": val}
    return out


def save_allowlist(apps: dict[str, dict[str, Any]]) -> None:
    path = allowlist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"apps": apps}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def set_decision(
    bundle_id: str,
    decision: Decision,
    *,
    name: str = "",
) -> None:
    apps = load_allowlist()
    apps[bundle_id] = {
        "decision": decision,
        "name": name or apps.get(bundle_id, {}).get("name") or bundle_id,
    }
    save_allowlist(apps)


def clear_decision(bundle_id: str) -> None:
    apps = load_allowlist()
    if bundle_id in apps:
        apps.pop(bundle_id, None)
        save_allowlist(apps)


def get_decision(bundle_id: str) -> Decision | None:
    if not bundle_id:
        return None
    entry = load_allowlist().get(bundle_id) or {}
    raw = str(entry.get("decision") or "").strip().lower()
    if raw in {"always", "once", "deny"}:
        return raw  # type: ignore[return-value]
    return None


def consume_once(bundle_id: str) -> None:
    """Clear a one-shot allow after it has been used."""
    if get_decision(bundle_id) == "once":
        clear_decision(bundle_id)


def is_blocked_app(*, bundle_id: str = "", name: str = "") -> str | None:
    """Return a human reason if the app is hard-blocked, else None."""
    bid = (bundle_id or "").strip()
    nm = (name or "").strip().lower()
    if bid in BLOCKED_BUNDLE_IDS:
        return f"blocked app bundle_id={bid}"
    for frag in BLOCKED_NAME_SUBSTR:
        if frag in nm or frag in bid.lower():
            return f"blocked app name/bundle matching {frag!r}"
    return None

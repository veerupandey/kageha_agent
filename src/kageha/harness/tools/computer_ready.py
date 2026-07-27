"""Single readiness gate for macOS computer-use (pack + driver + tool model)."""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComputerReady:
    ok: bool
    pack_enabled: bool = False
    driver_ok: bool = False
    perms_ok: bool | None = None
    tool_model_ok: bool = False
    tool_model_id: str = ""
    message: str = ""
    hints: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "pack_enabled": self.pack_enabled,
            "driver_ok": self.driver_ok,
            "perms_ok": self.perms_ok,
            "tool_model_ok": self.tool_model_ok,
            "tool_model_id": self.tool_model_id,
            "message": self.message,
            "hints": list(self.hints),
            "detail": dict(self.detail),
        }


# Reuse a fresh OK result so we don't re-probe TCC/daemon every chat turn.
_READY_CACHE: ComputerReady | None = None
_READY_CACHE_AT: float = 0.0
_READY_TTL_S = 90.0


_DESKTOP_APPS = (
    "calculator",
    "textedit",
    "finder",
    "slack",
    "messages",
    "notes",
    "system settings",
    "system preferences",
    "keynote",
    "pages",
    "numbers",
    "preview",
    "reminders",
    "calendar",
    "photos",
    "music",
)


def task_wants_computer(task: str) -> bool:
    """Heuristic: native desktop GUI intent (not a website / not generic coding)."""
    q = (task or "").lower()
    if not q.strip():
        return False
    web_markers = (
        "http://",
        "https://",
        "www.",
        "website",
        "web page",
        "webpage",
        "browser_",
        "in the browser",
        "chrome://",
    )
    if any(m in q for m in web_markers):
        return False
    desktop_markers = (
        "computer_use",
        "computer_get_state",
        "computer_click",
        "computer_doctor",
        "cua-driver",
        "not browser",
        "native app",
        "desktop app",
        "macos app",
        "mac os app",
        "ax tree",
        "accessibility snapshot",
    )
    if any(m in q for m in desktop_markers):
        return True
    for app in _DESKTOP_APPS:
        if app in q and any(
            verb in q for verb in ("open ", "launch ", "use ", "click", "type ")
        ):
            return True
        if f"open {app}" in q or f"launch {app}" in q:
            return True
    return False


def first_tool_calling_model() -> tuple[str, str]:
    """Return (model_id, hint) for the first available native tool-calling model."""
    from kageha.models.registry import ModelRegistry

    reg = ModelRegistry.load()
    ladder = list(reg.roles.get("tool_calling") or reg.roles.get("default") or [])
    available = {m.id: m for m in reg.available_models()}
    ordered: list[str] = []
    for mid in (*ladder, *available):
        if mid and mid not in ordered:
            ordered.append(mid)
    for mid in ordered:
        mc = available.get(mid) or reg.models.get(mid)
        if mc is None or mid not in available:
            continue
        if "tool_calling" not in set(mc.capabilities or []):
            continue
        pc = reg.providers.get(mc.provider)
        if pc is not None and pc.protocol == "gemini_cli":
            continue
        return mid, ""
    return "", (
        "No native tool-calling model is available. "
        "Set GEMINI_API_KEY and use /model gemini-flash (or gemini-pro). "
        "Antigravity CLI cannot call computer_* tools."
    )


async def ensure_computer_ready(*, pack_enabled: bool) -> ComputerReady:
    """Fail-closed readiness for a computer-use turn."""
    global _READY_CACHE, _READY_CACHE_AT
    now = time.time()
    if (
        pack_enabled
        and _READY_CACHE is not None
        and _READY_CACHE.ok
        and (now - _READY_CACHE_AT) < _READY_TTL_S
    ):
        cached = _READY_CACHE
        cached.message = (cached.message or "Computer-use ready.") + " (cached)"
        return cached

    hints: list[str] = []
    detail: dict[str, Any] = {}

    if platform.system() != "Darwin":
        return ComputerReady(
            ok=False,
            pack_enabled=pack_enabled,
            message="Computer-use v1 is macOS-only.",
            hints=["Run Kageha on macOS with cua-driver installed."],
        )

    if not pack_enabled:
        hints.append(
            "Enable the computer pack: tools.yaml packs: [computer] "
            "or export KAGEHA_TOOL_PACKS=computer"
        )
        return ComputerReady(
            ok=False,
            pack_enabled=False,
            message="Computer tool pack is not enabled.",
            hints=hints,
        )

    model_id, model_hint = first_tool_calling_model()
    tool_model_ok = bool(model_id)
    if not tool_model_ok and model_hint:
        hints.append(model_hint)

    from kageha.harness.tools import computer_driver as driver

    binary = driver.driver_bin()
    detail["driver_bin"] = binary
    driver_ok = bool(binary)
    perms_ok: bool | None = None
    if not binary:
        hints.append("Run ./scripts/install_computer_driver.sh")
    else:
        try:
            await driver.ensure_daemon(timeout=6.0)
            driver_ok = True
            detail["daemon"] = "running"
        except driver.ComputerDriverError as exc:
            driver_ok = False
            detail["daemon"] = str(exc)
            hints.append(str(exc))
        perms = await driver.permissions_status()
        detail["permissions"] = perms
        perms_ok = bool(
            perms.get("accessibility") and perms.get("screen_recording")
        )
        if not perms_ok:
            hints.append("Run: cua-driver permissions grant")

    ok = bool(pack_enabled and driver_ok and tool_model_ok and perms_ok)
    if ok:
        message = f"Computer-use ready (model={model_id})."
    else:
        parts = []
        if not tool_model_ok:
            parts.append("no tool-calling API model")
        if not driver_ok:
            parts.append("cua-driver not ready")
        if perms_ok is False:
            parts.append("Accessibility/Screen Recording missing")
        message = "Computer-use not ready: " + "; ".join(parts or ["unknown"]) + "."
        if hints:
            message += " " + hints[0]

    result = ComputerReady(
        ok=ok,
        pack_enabled=pack_enabled,
        driver_ok=driver_ok,
        perms_ok=perms_ok,
        tool_model_ok=tool_model_ok,
        tool_model_id=model_id,
        message=message,
        hints=hints,
        detail=detail,
    )
    if result.ok:
        _READY_CACHE = result
        _READY_CACHE_AT = now
    return result

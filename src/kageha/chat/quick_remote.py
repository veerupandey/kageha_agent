"""One-shot device remote commands — skip plan/verify/checkpoint loops.

Primary source: skill ``fast-path`` / ``fast-path-when`` frontmatter
(e.g. ``sony_bravia``). Built-in maps remain as a fallback.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kageha.chat.turn_manager import TurnContext

# Fallback when skills are unavailable / not yet declaring fast-path.
_BUILTIN_REMOTE: dict[str, str] = {
    "pause": "Pause",
    "play": "Play",
    "start": "Play",
    "unpause": "Play",
    "resume playback": "Play",
    "resume playing": "Play",
    "stop": "Stop",
    "mute": "Mute",
    "unmute": "Mute",
    "volume up": "VolumeUp",
    "vol up": "VolumeUp",
    "vol+": "VolumeUp",
    "louder": "VolumeUp",
    "volume down": "VolumeDown",
    "vol down": "VolumeDown",
    "vol-": "VolumeDown",
    "quieter": "VolumeDown",
    "home": "Home",
    "back": "Back",
    "ok": "Confirm",
    "enter": "Confirm",
    "select": "Confirm",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "power off": "PowerOff",
    "turn off": "PowerOff",
    "power on": "TvPower",
    "turn on": "TvPower",
}

_BUILTIN_LAUNCH: dict[str, str] = {
    "youtube": "youtube",
    "open youtube": "youtube",
    "launch youtube": "youtube",
    "netflix": "netflix",
    "open netflix": "netflix",
    "launch netflix": "netflix",
    "sony liv": "sonyliv",
    "sonyliv": "sonyliv",
    "open sony liv": "sonyliv",
    "launch sony liv": "sonyliv",
    "prime": "prime",
    "prime video": "prime",
    "open prime": "prime",
}

_BUILTIN_STATUS = frozenset(
    {
        "tv status",
        "status of tv",
        "what's on the tv",
        "whats on the tv",
        "tv power",
        "volume?",
        "what's the volume",
        "whats the volume",
        "tv?",
        "bravia status",
    }
)

_BUILTIN_WHEN = (
    "tv_control",
    "network_tvs",
    "bravia",
    "sony liv",
    "android_tv",
    "remote",
    "netflix",
    "youtube",
)


def normalize_remote_phrase(message: str) -> str:
    text = (message or "").strip().lower()
    text = re.sub(r"[.!?]+$", "", text).strip()
    text = re.sub(
        r"^(please|pls|just|can you|could you|hey)\s+",
        "",
        text,
        flags=re.I,
    ).strip()
    return text


@lru_cache(maxsize=1)
def _skill_fast_path_tables() -> tuple[dict[str, dict[str, str]], tuple[str, ...]]:
    try:
        from kageha.memory.skills import collect_skill_fast_paths

        phrases, when = collect_skill_fast_paths()
        return phrases, tuple(when)
    except Exception:  # noqa: BLE001
        return {}, ()


def reload_skill_fast_paths() -> None:
    """Clear cache after skill install/edit (tests / curator)."""
    _skill_fast_path_tables.cache_clear()


def _merged_actions() -> dict[str, dict[str, str]]:
    """Skill fast-paths win; builtins fill gaps."""
    skill_phrases, _ = _skill_fast_path_tables()
    out: dict[str, dict[str, str]] = {}
    for phrase, key in _BUILTIN_REMOTE.items():
        out[phrase] = {"kind": "key", "key": key}
    for phrase, app in _BUILTIN_LAUNCH.items():
        out[phrase] = {"kind": "launch", "app": app}
    for phrase in _BUILTIN_STATUS:
        out[phrase] = {"kind": "status"}
    out.update(skill_phrases)
    return out


def match_remote_key(message: str) -> str | None:
    text = normalize_remote_phrase(message)
    if not text or len(text) > 40:
        return None
    action = _merged_actions().get(text)
    if action and action.get("kind") == "key":
        return action.get("key")
    text2 = re.sub(r"\s+on\s+(the\s+)?tv$", "", text).strip()
    action = _merged_actions().get(text2)
    if action and action.get("kind") == "key":
        return action.get("key")
    return None


def match_app_launch(message: str) -> str | None:
    text = normalize_remote_phrase(message)
    if not text or len(text) > 48:
        return None
    action = _merged_actions().get(text)
    if action and action.get("kind") == "launch":
        return action.get("app")
    return None


def match_tv_status(message: str) -> bool:
    text = normalize_remote_phrase(message)
    action = _merged_actions().get(text)
    return bool(action and action.get("kind") == "status")


def session_has_device_remote(ctx: "TurnContext") -> bool:
    blob = " ".join(
        [
            ctx.objective or "",
            " ".join(ctx.artifacts or []),
            " ".join((ctx.recent_user_messages or [])[-6:]),
            " ".join(ctx.recent_artifacts or []),
        ]
    ).lower()
    _, skill_when = _skill_fast_path_tables()
    markers = list(_BUILTIN_WHEN) + list(skill_when)
    for marker in markers:
        m = (marker or "").lower().strip()
        if not m:
            continue
        if m.startswith("\\b") or "[" in m:
            try:
                if re.search(m, blob, re.I):
                    return True
            except re.error:
                continue
        elif m in blob:
            return True
    # Word-boundary fallback for bare "tv"
    if re.search(r"\btv\b", blob):
        return True
    return False


def device_remote_ready() -> bool:
    try:
        from kageha.devices.bravia import load_profile, resolve_host
    except Exception:  # noqa: BLE001
        return False
    host = resolve_host("")
    if not host:
        return False
    profile = load_profile(host) or {}
    if profile.get("paired") or profile.get("cookies") or profile.get("psk"):
        return True
    return bool((os.environ.get("KAGEHA_BRAVIA_PSK") or "").strip())


def should_quick_remote(message: str, ctx: "TurnContext") -> dict[str, str] | None:
    """Return ``{kind, key|app}`` when this message should bypass the agent loop."""
    if not (session_has_device_remote(ctx) or device_remote_ready()):
        return None
    text = normalize_remote_phrase(message)
    if not text or len(text) > 48:
        return None
    action = _merged_actions().get(text)
    if action:
        return dict(action)
    text2 = re.sub(r"\s+on\s+(the\s+)?tv$", "", text).strip()
    action = _merged_actions().get(text2)
    if action:
        return dict(action)
    return None


def _persist_cookies(host: str, client: Any) -> None:
    from kageha.devices.bravia import _CLIENT_NICKNAME, load_profile, save_profile

    profile = load_profile(host) or {
        "host": host,
        "client_id": "",
        "nickname": _CLIENT_NICKNAME,
    }
    profile["cookies"] = client.cookies
    profile["paired"] = True
    save_profile(host, profile)


async def execute_quick_remote(
    message: str,
    *,
    action: dict[str, str] | None = None,
    auto_approve: bool = False,
) -> str:
    """Run one device action; return a short user-facing reply."""
    act = action
    if act is None:
        from kageha.chat.turn_manager import TurnContext

        act = should_quick_remote(message, TurnContext(run_id="quick"))
    if not act and device_remote_ready():
        text = normalize_remote_phrase(message)
        act = _merged_actions().get(text)
    if not act:
        return "I couldn't map that to a TV remote action."

    from kageha.devices.bravia import client_from_env, resolve_host

    host = resolve_host("")
    if not host:
        return (
            "No Bravia host configured. Set KAGEHA_BRAVIA_HOST or pair a TV first."
        )
    if not auto_approve and act.get("kind") != "status":
        return (
            "TV remote actions need approval. "
            "Re-run chat with --approve for one-shot remotes."
        )
    client = client_from_env(host)
    if client is None:
        return f"ERROR: could not build Bravia client for {host}"

    kind = act.get("kind")
    if kind == "status":
        code, power, _ = client.rpc("system", "getPowerStatus")
        vol_code, vol, _ = client.rpc("audio", "getVolumeInformation")
        out: dict[str, Any] = {"host": host}
        if code < 400:
            result = power.get("result") or [{}]
            out["power"] = result[0] if isinstance(result, list) else result
        if vol_code < 400:
            result = vol.get("result") or [[]]
            out["volume"] = result[0] if isinstance(result, list) else result
        return f"TV `{host}`:\n```json\n{json.dumps(out, indent=2)[:1200]}\n```"

    if kind == "launch":
        app_q = act.get("app") or ""
        code, data, _ = client.rpc("appControl", "getApplicationList")
        if code >= 400 or "error" in data:
            return f"ERROR: cannot list apps: {data.get('error')}"
        apps = data.get("result") or []
        if isinstance(apps, list) and apps and isinstance(apps[0], list):
            apps = apps[0]
        target = ""
        title_hit = ""
        q = app_q.lower()
        for a in apps:
            if not isinstance(a, dict):
                continue
            title = str(a.get("title") or "")
            uri = str(a.get("uri") or "")
            if q in title.lower() or q in uri.lower():
                target = uri
                title_hit = title or uri
                break
        if not target:
            return f"ERROR: no app matched {app_q!r}"
        code, data, _ = client.rpc(
            "appControl", "setActiveApp", [{"uri": target}]
        )
        if code >= 400 or "error" in data:
            return f"ERROR: launch failed: {data.get('error') or data}"
        _persist_cookies(host, client)
        return f"Launched **{title_hit or app_q}** on `{host}`."

    lookup = act.get("key") or ""
    codes = client.remote_codes()
    ircc = codes.get(lookup)
    resolved = lookup
    if not ircc:
        for name, value in codes.items():
            if name.lower() == lookup.lower():
                ircc = value
                resolved = name
                break
    if not ircc and lookup == "Stop":
        ircc = codes.get("Pause")
        resolved = "Pause" if ircc else lookup
    if not ircc:
        sample = ", ".join(sorted(codes)[:24])
        return f"Unknown remote key {lookup!r}. Examples: {sample}"
    status, text = client.ircc(ircc)
    if status >= 400:
        return (
            f"TV rejected {resolved} ({status}): {text}. "
            "Re-pair with `kageha bravia pair` if needed."
        )
    _persist_cookies(host, client)
    return f"Sent **{resolved}** to Sony Bravia at `{host}`."

"""One-shot device remote commands — stubbed after devices pack removal.

Primary source was skill ``fast-path`` / ``fast-path-when`` frontmatter
(e.g. ``sony_bravia``). Device modules are gone; matching remains so routes
degrade cleanly instead of importing deleted packages.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import TYPE_CHECKING

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

_DEVICES_GONE = (
    "TV / device remote control was removed from this build "
    "(no `kageha.devices` package)."
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
    """Always false — Bravia/device stack was deleted."""
    return False


def should_quick_remote(message: str, ctx: "TurnContext") -> dict[str, str] | None:
    """Return ``{kind, key|app}`` when this message matches a remote phrase.

    Execution is stubbed; callers still get a mapped action so the route can
    reply with a clear removal message instead of falling into the agent loop.
    """
    if not session_has_device_remote(ctx):
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


async def execute_quick_remote(
    message: str,
    *,
    action: dict[str, str] | None = None,
    auto_approve: bool = False,
) -> str:
    """Device remotes are unavailable after the devices pack trim."""
    _ = (message, action, auto_approve)
    return _DEVICES_GONE

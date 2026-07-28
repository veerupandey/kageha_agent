"""Shared helpers for ``kageha models auth`` (import / status / logout).

Guided OAuth UX lives in ``kageha setup`` (``setup_wizard`` + ``oauth_setup``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kageha.models.auth_store import (
    AuthProfile,
    delete_profile,
    import_chatgpt_codex,
    import_gemini_cli,
    list_profiles,
    load_profile,
    probe_local_logins,
)


IMPORT_ALIASES = {
    "chatgpt": "chatgpt",
    "openai-codex": "chatgpt",
    "codex": "chatgpt",
    "openai": "chatgpt",
    "gemini-cli": "gemini-cli",
    "gemini": "gemini-cli",
    "antigravity": "gemini-cli",
    "agy": "gemini-cli",
}


def normalize_import_target(name: str) -> str:
    key = (name or "").strip().lower()
    if key not in IMPORT_ALIASES:
        raise KeyError(
            f"Unknown auth import target: {name!r}. "
            "Use: chatgpt | gemini-cli | antigravity"
        )
    return IMPORT_ALIASES[key]


def run_import(name: str, *, path: Path | None = None) -> AuthProfile:
    target = normalize_import_target(name)
    if target == "chatgpt":
        return import_chatgpt_codex(path)
    return import_gemini_cli(path)


def auth_status_payload(provider: str | None = None) -> dict[str, Any]:
    probe = probe_local_logins()
    if provider:
        pid = provider.strip().lower()
        for cand in (pid, IMPORT_ALIASES.get(pid, pid)):
            prof = load_profile(cand)
            if prof:
                return {
                    "probe": probe,
                    "profile": prof.as_public_dict(),
                }
        return {"probe": probe, "profile": None, "provider": pid}
    return {
        "probe": probe,
        "profiles": [p.as_public_dict() for p in list_profiles()],
        "cursor_oauth": False,
        "cursor_note": probe["note_cursor"],
    }


def run_logout(provider: str) -> bool:
    pid = provider.strip().lower()
    deleted = delete_profile(pid)
    if pid in {"chatgpt", "codex", "openai-codex"}:
        delete_profile("chatgpt")
        delete_profile("openai-codex")
        return True
    if pid in {"gemini-cli", "gemini", "antigravity", "agy"}:
        delete_profile("gemini-cli")
        delete_profile("antigravity")
        return True
    return deleted

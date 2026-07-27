"""Shared helpers for ``kageha models auth`` and public setup wizards."""

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
        # Map aliases for status lookup
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
    # Also clear aliases when logging out primary
    if pid in {"chatgpt", "codex", "openai-codex"}:
        delete_profile("chatgpt")
        delete_profile("openai-codex")
        return True
    if pid in {"gemini-cli", "gemini", "antigravity", "agy"}:
        delete_profile("gemini-cli")
        delete_profile("antigravity")
        return True
    return deleted


def run_model_auth_setup_step(*, interactive: bool = True) -> dict[str, Any]:
    """First-run / public setup step: detect CLI logins → import or API key.

    Never prints token values. Safe for ``kageha setup`` and ``models setup``.
    """
    probe = probe_local_logins()
    result: dict[str, Any] = {
        "probe": {
            "chatgpt_codex_cli": probe["chatgpt_codex_cli"],
            "gemini_cli_oauth": probe["gemini_cli_oauth"],
            "antigravity_data_dir": probe["antigravity_data_dir"],
            "cursor_oauth": False,
        },
        "imported": [],
        "skipped": False,
        "cursor_note": probe["note_cursor"],
    }

    print(
        "\nModel auth (public setup)\n"
        "-------------------------\n"
        "Choose how Kageha talks to models:\n"
        "  • Import ChatGPT/Codex login  (from `codex login` → ~/.codex/auth.json)\n"
        "  • Import Gemini CLI / Antigravity  (~/.gemini/oauth_creds.json)\n"
        "  • Paste an API key later (GEMINI_API_KEY / OPENAI_API_KEY)\n"
        "  • Cursor / Kiro IDE subscription OAuth is NOT available to third-party apps\n",
        flush=True,
    )
    print(
        f"Detected: chatgpt/codex={'yes' if probe['chatgpt_codex_cli'] else 'no'}  "
        f"gemini-cli={'yes' if probe['gemini_cli_oauth'] else 'no'}  "
        f"antigravity_dir={'yes' if probe['antigravity_data_dir'] else 'no'}",
        flush=True,
    )

    if not interactive:
        # Non-interactive: auto-import whatever is present
        if probe["chatgpt_codex_cli"]:
            run_import("chatgpt")
            result["imported"].append("chatgpt")
        if probe["gemini_cli_oauth"]:
            run_import("gemini-cli")
            result["imported"].append("gemini-cli")
        return result

    options = ["[1] Skip (API keys only / configure later)"]
    mapping: dict[str, str] = {"1": "skip"}
    n = 2
    if probe["chatgpt_codex_cli"]:
        options.append(f"[{n}] Import ChatGPT / Codex CLI login")
        mapping[str(n)] = "chatgpt"
        n += 1
    else:
        options.append(f"[{n}] ChatGPT/Codex not found — run `codex login` first")
        mapping[str(n)] = "hint-codex"
        n += 1
    if probe["gemini_cli_oauth"]:
        options.append(f"[{n}] Import Gemini CLI / Antigravity OAuth")
        mapping[str(n)] = "gemini-cli"
        n += 1
    else:
        options.append(f"[{n}] Gemini CLI OAuth not found — sign in via `gemini` first")
        mapping[str(n)] = "hint-gemini"
        n += 1
    options.append(f"[{n}] Import both (when available)")
    mapping[str(n)] = "both"
    n += 1
    options.append(f"[{n}] Paste GEMINI_API_KEY now")
    mapping[str(n)] = "api-gemini"

    for line in options:
        print(f"  {line}", flush=True)
    choice = input("Choice [1]: ").strip() or "1"
    action = mapping.get(choice, "skip")

    if action == "skip":
        result["skipped"] = True
        return result
    if action == "hint-codex":
        print("Run: codex login\nThen: kageha models auth import chatgpt", flush=True)
        result["skipped"] = True
        return result
    if action == "hint-gemini":
        print(
            "Run Gemini CLI or Antigravity sign-in, then:\n"
            "  kageha models auth import gemini-cli",
            flush=True,
        )
        result["skipped"] = True
        return result
    if action == "api-gemini":
        from kageha.channels.whatsapp_setup import upsert_env_key
        import os

        key = input("GEMINI_API_KEY: ").strip()
        if key:
            upsert_env_key("GEMINI_API_KEY", key)
            os.environ["GEMINI_API_KEY"] = key
            result["imported"].append("GEMINI_API_KEY")
        else:
            result["skipped"] = True
        return result

    targets: list[str] = []
    if action == "both":
        if probe["chatgpt_codex_cli"]:
            targets.append("chatgpt")
        if probe["gemini_cli_oauth"]:
            targets.append("gemini-cli")
    else:
        targets.append(action)

    for t in targets:
        try:
            prof = run_import(t)
            result["imported"].append(prof.provider)
            print(f"Imported {prof.provider} (tokens stored under ~/.kageha/auth/)", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"Import {t} failed: {exc}", flush=True)
    return result

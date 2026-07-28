"""OpenAI Codex + Antigravity / Gemini CLI OAuth helpers (library, no wizard UX).

Launches browser login when requested, then imports tokens into
``~/.kageha/auth/``. Never prints token values.

Guided UX lives in ``kageha.setup_wizard`` (`kageha setup`).

References:
- Codex: ``codex login`` → ``~/.codex/auth.json``
  https://developers.openai.com/codex/auth
- Antigravity CLI: ``agy`` or legacy ``gemini``
  https://antigravity.google/docs/cli/install
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Literal

from kageha.models.auth_cli import run_import
from kageha.models.auth_store import probe_local_logins

Target = Literal["codex", "antigravity", "both"]

_CODEX_INSTALL = (
    "Install Codex CLI, then re-run `kageha setup`:\n"
    "  npm install -g @openai/codex\n"
    "  # or: brew install codex\n"
    "  codex login"
)

_AGY_INSTALL = (
    "Install Antigravity CLI, then re-run `kageha setup`:\n"
    "  curl -fsSL https://antigravity.google/cli/install.sh | bash\n"
    "  agy   # first run opens Google OAuth\n"
    "Or use Gemini CLI: gemini  (sign-in), then import."
)


def _yn(label: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{label} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def detect_tools() -> dict[str, Any]:
    """Binaries + login-file probe (no token bodies)."""
    probe = probe_local_logins()
    codex = shutil.which("codex")
    agy = shutil.which("agy")
    gemini = shutil.which("gemini")
    return {
        **probe,
        "codex_bin": codex or "",
        "agy_bin": agy or "",
        "gemini_bin": gemini or "",
        "has_codex_cli": bool(codex),
        "has_agy_cli": bool(agy),
        "has_gemini_cli": bool(gemini),
    }


def _public_tools(tools: dict[str, Any]) -> dict[str, Any]:
    return {
        "has_codex_cli": tools["has_codex_cli"],
        "has_agy_cli": tools["has_agy_cli"],
        "has_gemini_cli": tools["has_gemini_cli"],
        "chatgpt_codex_cli": tools["chatgpt_codex_cli"],
        "gemini_cli_oauth": tools["gemini_cli_oauth"],
        "antigravity_data_dir": tools["antigravity_data_dir"],
    }


def _run_cmd(cmd: list[str], *, label: str) -> dict[str, Any]:
    print(f"\n→ {label}: {' '.join(cmd)}\n", flush=True)
    try:
        proc = subprocess.run(cmd, check=False)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "cmd": cmd}
    return {"ok": proc.returncode == 0, "returncode": proc.returncode, "cmd": cmd}


def _codex_status_ok() -> bool:
    codex = shutil.which("codex")
    if not codex:
        return False
    try:
        proc = subprocess.run(
            [codex, "login", "status"],
            check=False,
            capture_output=True,
            text=True,
        )
        return proc.returncode == 0
    except OSError:
        return False


def setup_codex_oauth(*, launch_login: bool = True) -> dict[str, Any]:
    """Run ``codex login`` (optional) and import into ``~/.kageha/auth/``."""
    tools = detect_tools()
    result: dict[str, Any] = {
        "provider": "chatgpt",
        "launched": False,
        "imported": False,
        "ok": False,
    }

    if not tools["has_codex_cli"]:
        print(_CODEX_INSTALL, flush=True)
        result["error"] = "codex_cli_missing"
        return result

    auth_path = Path.home() / ".codex" / "auth.json"
    need_login = False
    if launch_login:
        if not auth_path.is_file() or not _codex_status_ok():
            need_login = True
        elif _yn("Codex already logged in. Re-run `codex login`?", False):
            need_login = True

    if need_login:
        launched = _run_cmd(["codex", "login"], label="OpenAI Codex OAuth")
        result["launched"] = True
        result["launch"] = launched
        if not launched.get("ok"):
            print(
                "Codex login exited non-zero. If the browser finished, "
                "import may still work.",
                flush=True,
            )

    if not auth_path.is_file():
        print(
            f"Codex auth file not found at {auth_path}.\n"
            "Complete `codex login` in a browser, then re-run `kageha setup`.",
            flush=True,
        )
        result["error"] = "codex_auth_missing"
        return result

    try:
        prof = run_import("chatgpt")
        result["imported"] = True
        result["ok"] = True
        result["profile"] = prof.as_public_dict()
        print(
            "Imported ChatGPT/Codex OAuth → ~/.kageha/auth/ "
            f"(provider={prof.provider})",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        print(f"Import failed: {exc}", flush=True)
    return result


def setup_antigravity_oauth(*, launch_login: bool = True) -> dict[str, Any]:
    """Guide Antigravity / Gemini Google OAuth, then import shared creds."""
    tools = detect_tools()
    result: dict[str, Any] = {
        "provider": "antigravity",
        "launched": False,
        "imported": False,
        "ok": False,
    }

    oauth_path = Path.home() / ".gemini" / "oauth_creds.json"
    preferred = ""
    if tools["has_agy_cli"]:
        preferred = "agy"
    elif tools["has_gemini_cli"]:
        preferred = "gemini"

    if not preferred:
        print(_AGY_INSTALL, flush=True)
        result["error"] = "antigravity_cli_missing"
        return result

    need_login = False
    if launch_login:
        if not oauth_path.is_file():
            need_login = True
        elif _yn(
            f"Gemini/Antigravity OAuth file exists. Launch `{preferred}` "
            "to refresh login?",
            False,
        ):
            need_login = True

    if need_login:
        print(
            "\nGoogle OAuth will open in your browser.\n"
            "Sign in, then return here when the CLI finishes.\n",
            flush=True,
        )
        if preferred == "agy":
            launched = _run_cmd(["agy"], label="Antigravity (agy) Google OAuth")
        else:
            launched = _run_cmd(
                ["gemini"],
                label="Gemini CLI Google OAuth (Antigravity-shared)",
            )
        result["launched"] = True
        result["launch"] = launched
        result["cli"] = preferred

    if not oauth_path.is_file():
        anti = Path.home() / ".gemini" / "antigravity"
        if anti.is_dir():
            print(
                f"Antigravity data present at {anti}, but "
                f"{oauth_path} is missing.\n"
                "Open Antigravity IDE or run `gemini` / `agy` once, then:\n"
                "  kageha models auth import antigravity\n"
                "  # or re-run: kageha setup",
                flush=True,
            )
            result["error"] = "oauth_creds_missing"
            result["antigravity_data_dir"] = True
            return result
        print(
            f"OAuth creds not found at {oauth_path}.\n"
            f"Complete Google sign-in via `{preferred}`, then re-run `kageha setup`.",
            flush=True,
        )
        result["error"] = "oauth_creds_missing"
        return result

    try:
        prof = run_import("antigravity")
        result["imported"] = True
        result["ok"] = True
        result["profile"] = prof.as_public_dict()
        print(
            "Imported Antigravity / Gemini CLI OAuth → ~/.kageha/auth/ "
            f"(provider={prof.provider})",
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
        print(f"Import failed: {exc}", flush=True)
    return result


def run_oauth_setup(
    *,
    target: Target = "both",
    launch_login: bool = False,
) -> dict[str, Any]:
    """Non-interactive orchestrator: import (and optionally launch) for ``target``.

    UX menus belong in ``kageha setup``. Defaults to import-only.
    """
    tools = detect_tools()
    results: dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "target": target,
        "launch_login": launch_login,
        "codex": None,
        "antigravity": None,
        "imported": [],
        "tools": _public_tools(tools),
    }

    if target in {"codex", "both"}:
        results["codex"] = setup_codex_oauth(launch_login=launch_login)
        if results["codex"].get("imported"):
            results["imported"].append("chatgpt")

    if target in {"antigravity", "both"}:
        results["antigravity"] = setup_antigravity_oauth(launch_login=launch_login)
        if results["antigravity"].get("imported"):
            results["imported"].append("antigravity")

    if results["imported"]:
        results["ok"] = True
    elif not launch_login:
        results["ok"] = True
        results["note"] = "No local OAuth files imported."
    else:
        results["ok"] = False
        results["note"] = "OAuth setup did not import any profiles."

    return results

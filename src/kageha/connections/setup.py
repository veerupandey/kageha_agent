"""Google OAuth client setup — gog-style client_secret.json or paste into .env."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from kageha.channels.whatsapp_setup import resolve_env_file, upsert_env_key
from kageha.connections.google_oauth import (
    google_client_json_path,
    google_oauth_client_configured,
    parse_google_client_json,
)
from kageha.connections.store import connections_dir

_CONSOLE = "https://console.cloud.google.com/apis/credentials"


def install_google_client_json(
    source: str | Path,
    *,
    also_env: bool = False,
) -> dict[str, Any]:
    """Register a Desktop OAuth client JSON (like ``gog auth credentials``).

    Copies to ``~/.kageha/connections/google-client.json`` (0600). Optionally
    also writes Client ID/Secret into ``.env``.
    """
    src = Path(source).expanduser().resolve()
    if not src.is_file():
        raise RuntimeError(f"Credentials file not found: {src}")
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid credentials JSON: {exc}") from exc
    client_id, client_secret, normalized = parse_google_client_json(raw)

    dest = google_client_json_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Prefer normalized installed shape for InstalledAppFlow.from_client_secrets_file
    text = json.dumps(normalized, indent=2) + "\n"
    dest.write_text(text, encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass

    os.environ["GOOGLE_OAUTH_CLIENT_ID"] = client_id
    os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = client_secret
    # Prefer file on subsequent runs
    os.environ["GOOGLE_OAUTH_CLIENT_JSON"] = str(dest)

    env_path = ""
    if also_env:
        env_path = str(upsert_env_key("GOOGLE_OAUTH_CLIENT_ID", client_id))
        upsert_env_key("GOOGLE_OAUTH_CLIENT_SECRET", client_secret, path=Path(env_path))
        upsert_env_key("GOOGLE_OAUTH_CLIENT_JSON", str(dest), path=Path(env_path))

    return {
        "client_id": client_id,
        "path": str(dest),
        "source": str(src),
        "env_file": env_path,
        "next": "kageha connect login gmail",
    }


def run_google_oauth_setup(
    *,
    yes: bool = False,
    credentials: str | Path | None = None,
    also_env: bool = False,
) -> dict[str, Any]:
    """gog-like setup: import client_secret.json, or paste id/secret."""
    if credentials:
        result = install_google_client_json(credentials, also_env=also_env)
        print(f"\nRegistered Google OAuth client → {result['path']}")
        print(f"Next: {result['next']}\n")
        return result

    print(
        "\nKageha Google OAuth setup (same as OpenClaw / gog)\n"
        "----------------------------------------------------\n"
        "1. Open: " + _CONSOLE + "\n"
        "2. Enable Gmail API (Calendar/Drive if needed)\n"
        "3. OAuth consent screen → External (add yourself as test user) or Internal\n"
        "4. Create credentials → OAuth client ID → Desktop app\n"
        "5. Download the JSON (client_secret_….json)\n\n"
        "Then either:\n"
        "  kageha connect credentials ~/Downloads/client_secret_….json\n"
        "or paste Client ID / Secret below.\n"
        f"\nStored client file: {google_client_json_path()}\n"
        f".env (optional): {resolve_env_file()}\n"
    )

    if google_oauth_client_configured() and not yes:
        cid = (
            os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
            or "(from google-client.json)"
        )
        tail = cid[-12:] if len(cid) > 12 else cid
        print(f"Already configured (client id ends with …{tail}).")
        ans = input("Replace? [y/N]: ").strip().lower()
        if ans not in {"y", "yes"}:
            return {"configured": True, "path": str(google_client_json_path())}

    if not sys.stdin.isatty():
        raise RuntimeError(
            "No Google OAuth client configured and stdin is not a TTY.\n"
            "Download Desktop client JSON from Cloud Console, then:\n"
            "  kageha connect credentials ~/Downloads/client_secret_….json\n"
            "See docs/USAGE.md (connections)."
        )

    path_in = input(
        "Path to client_secret JSON (Enter to paste id/secret instead): "
    ).strip()
    if path_in:
        # Allow drag-drop quotes
        path_in = path_in.strip("'\"")
        result = install_google_client_json(path_in, also_env=also_env)
        print(f"\nRegistered Google OAuth client → {result['path']}")
        print(f"Next: {result['next']}\n")
        return result

    client_id = input("Client ID: ").strip()
    if not client_id:
        raise RuntimeError("Aborted: empty Client ID.")
    client_secret = input("Client Secret: ").strip()
    if not client_secret:
        raise RuntimeError("Aborted: empty Client Secret.")

    # Persist as google-client.json (gog-style) + .env for convenience
    payload = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    dest = google_client_json_path()
    connections_dir()
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass

    env_path = upsert_env_key("GOOGLE_OAUTH_CLIENT_ID", client_id)
    upsert_env_key("GOOGLE_OAUTH_CLIENT_SECRET", client_secret, path=env_path)
    upsert_env_key("GOOGLE_OAUTH_CLIENT_JSON", str(dest), path=env_path)
    os.environ["GOOGLE_OAUTH_CLIENT_ID"] = client_id
    os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = client_secret
    os.environ["GOOGLE_OAUTH_CLIENT_JSON"] = str(dest)

    print(f"\nSaved client → {dest}")
    print(f"Also wrote .env → {env_path}")
    print("Next: kageha connect login gmail\n")
    return {
        "client_id": client_id,
        "path": str(dest),
        "env_file": str(env_path),
        "next": "kageha connect login gmail",
    }


def ensure_google_oauth_client(*, interactive: bool = True) -> None:
    """No-op if configured; otherwise run interactive setup (or raise)."""
    if google_oauth_client_configured():
        return
    if not interactive:
        raise RuntimeError(
            "Google OAuth client missing. Same as OpenClaw/gog:\n"
            "  1. Download Desktop client JSON from Google Cloud Console\n"
            "  2. kageha connect credentials ~/Downloads/client_secret_….json\n"
            "  3. kageha connect login gmail\n"
            "Or: kageha connect setup google"
        )
    run_google_oauth_setup()
    if not google_oauth_client_configured():
        raise RuntimeError("Google OAuth client still not configured.")

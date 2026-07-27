"""Shared Google OAuth helpers (installed-app / localhost redirect)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kageha.connections.store import connections_dir

# Minimal scopes used by built-in providers
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
GCAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]
GDRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def _require_google_auth() -> None:
    try:
        import google.oauth2.credentials  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
    except ImportError as exc:  # pragma: no cover - env dependent
        raise RuntimeError(
            "Google OAuth extras missing. Install with:\n"
            "  uv sync --extra connections\n"
            "or: pip install 'kageha[connections]'"
        ) from exc


def google_client_json_path() -> Path:
    """Canonical path for Desktop client JSON (gog-style)."""
    override = os.environ.get("GOOGLE_OAUTH_CLIENT_JSON", "").strip()
    if override:
        return Path(override).expanduser()
    return connections_dir() / "google-client.json"


def parse_google_client_json(raw: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Accept Google Cloud download shapes: ``installed`` or ``web``.

    Returns (client_id, client_secret, normalized_installed_config).
    """
    block: dict[str, Any] | None = None
    if isinstance(raw.get("installed"), dict):
        block = raw["installed"]
    elif isinstance(raw.get("web"), dict):
        block = raw["web"]
    elif raw.get("client_id") and raw.get("client_secret"):
        block = raw
    if not block:
        raise RuntimeError(
            "Unrecognized Google client JSON. Expected Desktop download with "
            "top-level 'installed' (or 'web') object."
        )
    client_id = str(block.get("client_id") or "").strip()
    client_secret = str(block.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("Google client JSON missing client_id / client_secret.")
    normalized = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": block.get("auth_uri")
            or "https://accounts.google.com/o/oauth2/auth",
            "token_uri": block.get("token_uri") or "https://oauth2.googleapis.com/token",
            "redirect_uris": list(block.get("redirect_uris") or ["http://localhost"]),
            "project_id": block.get("project_id") or "",
        }
    }
    return client_id, client_secret, normalized


def _load_client_from_json_file() -> tuple[str, str] | None:
    path = google_client_json_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        client_id, client_secret, _ = parse_google_client_json(raw)
    except RuntimeError:
        return None
    return client_id, client_secret


def google_oauth_client_id() -> str:
    env = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
        or os.environ.get("GMAIL_OAUTH_CLIENT_ID", "").strip()
    )
    if env:
        return env
    loaded = _load_client_from_json_file()
    return loaded[0] if loaded else ""


def google_oauth_client_secret() -> str:
    env = (
        os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
        or os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "").strip()
    )
    if env:
        return env
    loaded = _load_client_from_json_file()
    return loaded[1] if loaded else ""


def google_oauth_client_configured() -> bool:
    return bool(google_oauth_client_id() and google_oauth_client_secret())


def google_client_config() -> dict[str, Any]:
    """Build InstalledApp client config from JSON file or env vars."""
    path = google_client_json_path()
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                _, _, normalized = parse_google_client_json(raw)
                return normalized
        except (OSError, json.JSONDecodeError, RuntimeError):
            pass
    client_id = google_oauth_client_id()
    client_secret = google_oauth_client_secret()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Google OAuth client missing. Same as OpenClaw/gog:\n"
            "  kageha connect credentials ~/Downloads/client_secret_….json\n"
            "  kageha connect login gmail\n"
            "Or: kageha connect setup google  (see docs/USAGE.md)"
        )
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def credentials_to_dict(creds: Any) -> dict[str, Any]:
    return {
        "token": getattr(creds, "token", None),
        "refresh_token": getattr(creds, "refresh_token", None),
        "token_uri": getattr(creds, "token_uri", None)
        or "https://oauth2.googleapis.com/token",
        "client_id": getattr(creds, "client_id", None),
        "client_secret": getattr(creds, "client_secret", None),
        "scopes": list(getattr(creds, "scopes", None) or []),
        "expiry": creds.expiry.isoformat() if getattr(creds, "expiry", None) else None,
    }


def credentials_from_dict(data: dict[str, Any]) -> Any:
    _require_google_auth()
    from google.oauth2.credentials import Credentials

    expiry = None
    raw_expiry = data.get("expiry")
    if raw_expiry:
        from datetime import datetime

        try:
            expiry = datetime.fromisoformat(str(raw_expiry))
            if expiry.tzinfo is not None:
                expiry = expiry.replace(tzinfo=None)
        except ValueError:
            expiry = None
    return Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=data.get("scopes"),
        expiry=expiry,
    )


def run_installed_app_login(
    scopes: list[str],
    *,
    open_browser: bool = True,
) -> tuple[Any, str]:
    """Interactive localhost OAuth; returns (credentials, account_email)."""
    _require_google_auth()
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(google_client_config(), scopes=scopes)
    # Local server is the best CLI UX; falls back message if browser blocked.
    creds = flow.run_local_server(
        port=0,
        open_browser=open_browser,
        prompt="consent",
        access_type="offline",
    )
    email = fetch_google_email(creds.token) or ""
    return creds, email


def ensure_fresh_credentials(token_blob: dict[str, Any]) -> Any:
    """Load + refresh Google credentials; raises if unusable."""
    _require_google_auth()
    from google.auth.transport.requests import Request

    creds = credentials_from_dict(token_blob)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        return creds
    raise RuntimeError(
        "Google token expired or invalid. Re-run: kageha connect login <provider>"
    )


def fetch_google_email(access_token: str | None) -> str:
    if not access_token:
        return ""
    try:
        import httpx

        resp = httpx.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        if resp.status_code >= 400:
            return ""
        data = resp.json()
        return str(data.get("email") or "").strip().lower()
    except Exception:  # noqa: BLE001
        return ""


def persist_google_token(
    store: Any,
    provider_id: str,
    creds: Any,
    *,
    account: str = "",
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    account = account or fetch_google_email(getattr(creds, "token", None)) or ""
    payload = {
        "provider": provider_id,
        "account": account,
        "scopes": list(scopes or getattr(creds, "scopes", None) or []),
        "token": credentials_to_dict(creds),
    }
    store.save(provider_id, payload)
    return payload

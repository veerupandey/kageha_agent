"""Google Drive (read-only) connection via Google OAuth2."""

from __future__ import annotations

from typing import Any

from kageha.connections.base import ConnectionProvider, ConnectionStatus
from kageha.connections.google_oauth import (
    GDRIVE_SCOPES,
    ensure_fresh_credentials,
    persist_google_token,
    run_installed_app_login,
)


class GoogleDriveProvider(ConnectionProvider):
    id = "gdrive"
    label = "Google Drive"
    scopes = list(GDRIVE_SCOPES)
    description = "List and read Google Drive files (read-only)"

    def login(self, *, store: Any, open_browser: bool = True) -> ConnectionStatus:
        creds, email = run_installed_app_login(self.scopes, open_browser=open_browser)
        persist_google_token(
            store, self.id, creds, account=email, scopes=self.scopes
        )
        return ConnectionStatus(
            provider=self.id,
            connected=True,
            account=email,
            scopes=list(self.scopes),
            detail="OAuth login complete",
        )

    def status(self, *, store: Any) -> ConnectionStatus:
        data = store.load(self.id)
        if not data or not data.get("token"):
            return ConnectionStatus(
                provider=self.id,
                connected=False,
                detail="not connected",
            )
        try:
            creds = ensure_fresh_credentials(data["token"])
            account = str(data.get("account") or "")
            persist_google_token(
                store,
                self.id,
                creds,
                account=account,
                scopes=list(data.get("scopes") or self.scopes),
            )
            return ConnectionStatus(
                provider=self.id,
                connected=True,
                account=account,
                scopes=list(data.get("scopes") or self.scopes),
                detail="ok",
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectionStatus(
                provider=self.id,
                connected=False,
                account=str(data.get("account") or ""),
                error=str(exc),
                detail="token invalid",
            )

    def access_token(self, *, store: Any) -> str:
        self.require_connected(store=store)
        data = store.load(self.id) or {}
        creds = ensure_fresh_credentials(data["token"])
        persist_google_token(
            store,
            self.id,
            creds,
            account=str(data.get("account") or ""),
            scopes=list(data.get("scopes") or self.scopes),
        )
        token = getattr(creds, "token", None)
        if not token:
            raise RuntimeError("Drive access token missing after refresh")
        return str(token)

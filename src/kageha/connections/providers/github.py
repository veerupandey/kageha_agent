"""GitHub connection via OAuth device flow (CLI-friendly)."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from kageha.connections.base import ConnectionProvider, ConnectionStatus

GITHUB_SCOPES = ["repo", "read:user", "user:email"]


def github_client_id() -> str:
    cid = (
        os.environ.get("GITHUB_OAUTH_CLIENT_ID", "").strip()
        or os.environ.get("GH_OAUTH_CLIENT_ID", "").strip()
    )
    if not cid:
        raise RuntimeError(
            "Set GITHUB_OAUTH_CLIENT_ID (GitHub OAuth App → Client ID). "
            "Enable Device Flow on the app. See docs/USAGE.md (connections)."
        )
    return cid


class GitHubProvider(ConnectionProvider):
    id = "github"
    label = "GitHub"
    scopes = list(GITHUB_SCOPES)
    description = "GitHub API via device-code OAuth (repos, user)"

    def login(self, *, store: Any, open_browser: bool = True) -> ConnectionStatus:
        client_id = github_client_id()
        scope = " ".join(self.scopes)
        with httpx.Client(timeout=30.0) as client:
            start = client.post(
                "https://github.com/login/device/code",
                headers={"Accept": "application/json"},
                data={"client_id": client_id, "scope": scope},
            )
            if start.status_code >= 400:
                raise RuntimeError(
                    f"GitHub device code request failed: {start.status_code} {start.text}"
                )
            payload = start.json()
            device_code = payload["device_code"]
            user_code = payload["user_code"]
            verify_uri = payload.get("verification_uri") or "https://github.com/login/device"
            interval = int(payload.get("interval") or 5)
            expires_in = int(payload.get("expires_in") or 900)

            print("\nGitHub device login")
            print(f"  1. Open: {verify_uri}")
            print(f"  2. Enter code: {user_code}\n")
            if open_browser:
                try:
                    import webbrowser

                    webbrowser.open(verify_uri)
                except Exception:  # noqa: BLE001
                    pass

            deadline = time.time() + expires_in
            access_token = ""
            token_type = "bearer"
            granted_scope = scope
            while time.time() < deadline:
                time.sleep(max(1, interval))
                poll = client.post(
                    "https://github.com/login/oauth/access_token",
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": client_id,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
                body = poll.json()
                if body.get("access_token"):
                    access_token = str(body["access_token"])
                    token_type = str(body.get("token_type") or "bearer")
                    granted_scope = str(body.get("scope") or scope)
                    break
                err = body.get("error")
                if err == "authorization_pending":
                    continue
                if err == "slow_down":
                    interval += 5
                    continue
                if err in {"expired_token", "access_denied"}:
                    raise RuntimeError(f"GitHub login failed: {err}")
                if err:
                    raise RuntimeError(f"GitHub login failed: {err}")
            if not access_token:
                raise RuntimeError("GitHub login timed out")

            login = ""
            me = client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            if me.status_code < 400:
                login = str(me.json().get("login") or "")

        store.save(
            self.id,
            {
                "provider": self.id,
                "account": login,
                "scopes": granted_scope.split() if granted_scope else list(self.scopes),
                "token": {
                    "access_token": access_token,
                    "token_type": token_type,
                    "client_id": client_id,
                },
            },
        )
        return ConnectionStatus(
            provider=self.id,
            connected=True,
            account=login,
            scopes=granted_scope.split() if granted_scope else list(self.scopes),
            detail="device login complete",
        )

    def status(self, *, store: Any) -> ConnectionStatus:
        data = store.load(self.id)
        if not data or not (data.get("token") or {}).get("access_token"):
            return ConnectionStatus(
                provider=self.id,
                connected=False,
                detail="not connected",
            )
        token = data["token"]["access_token"]
        try:
            with httpx.Client(timeout=20.0) as client:
                me = client.get(
                    "https://api.github.com/user",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            if me.status_code >= 400:
                return ConnectionStatus(
                    provider=self.id,
                    connected=False,
                    account=str(data.get("account") or ""),
                    error=f"HTTP {me.status_code}",
                    detail="token invalid",
                )
            login = str(me.json().get("login") or data.get("account") or "")
            if login and login != data.get("account"):
                data["account"] = login
                store.save(self.id, data)
            return ConnectionStatus(
                provider=self.id,
                connected=True,
                account=login,
                scopes=list(data.get("scopes") or self.scopes),
                detail="ok",
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectionStatus(
                provider=self.id,
                connected=False,
                account=str(data.get("account") or ""),
                error=str(exc),
                detail="status check failed",
            )

    def access_token(self, *, store: Any) -> str:
        self.require_connected(store=store)
        data = store.load(self.id) or {}
        token = (data.get("token") or {}).get("access_token")
        if not token:
            raise RuntimeError(
                "github is not connected. Run: kageha connect login github"
            )
        return str(token)

"""Thin Gmail API client using stored OAuth tokens (httpx)."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from typing import Any

import httpx

from kageha.connections.providers.gmail import GmailProvider
from kageha.connections.store import ConnectionStore

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


def _not_connected_msg() -> str:
    return "gmail is not connected. Run: kageha connect login gmail"


def get_gmail_token(store: ConnectionStore | None = None) -> tuple[str, str]:
    """Return (access_token, account_email). Raises with clear CLI hint."""
    store = store or ConnectionStore()
    provider = GmailProvider()
    st = provider.status(store=store)
    if not st.connected:
        raise RuntimeError(_not_connected_msg())
    return provider.access_token(store=store), provider.account_email(store=store)


def list_messages(
    *,
    query: str = "",
    max_results: int = 10,
    store: ConnectionStore | None = None,
) -> list[dict[str, Any]]:
    token, _account = get_gmail_token(store)
    params: dict[str, Any] = {"maxResults": max(1, min(50, int(max_results)))}
    if query.strip():
        params["q"] = query.strip()
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            f"{GMAIL_API}/users/me/messages",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail list failed: HTTP {resp.status_code} {resp.text[:300]}")
        ids = [m["id"] for m in (resp.json().get("messages") or []) if m.get("id")]
        out: list[dict[str, Any]] = []
        for mid in ids:
            meta = client.get(
                f"{GMAIL_API}/users/me/messages/{mid}",
                headers={"Authorization": f"Bearer {token}"},
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
            )
            if meta.status_code >= 400:
                continue
            body = meta.json()
            headers = {
                h["name"]: h["value"]
                for h in (body.get("payload") or {}).get("headers") or []
                if h.get("name") and h.get("value")
            }
            out.append(
                {
                    "id": mid,
                    "threadId": body.get("threadId"),
                    "snippet": body.get("snippet") or "",
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "subject": headers.get("Subject", ""),
                    "date": headers.get("Date", ""),
                }
            )
        return out


def send_message(
    *,
    to: str,
    subject: str,
    body: str,
    store: ConnectionStore | None = None,
) -> dict[str, Any]:
    token, account = get_gmail_token(store)
    msg = EmailMessage()
    msg["To"] = to.strip()
    msg["From"] = account
    msg["Subject"] = subject.strip() or "(no subject)"
    msg.set_content(body or "")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{GMAIL_API}/users/me/messages/send",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"raw": raw},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Gmail send failed: HTTP {resp.status_code} {resp.text[:300]}")
        data = resp.json()
        return {"ok": True, "id": data.get("id"), "threadId": data.get("threadId")}


def xoauth2_string(user: str, access_token: str) -> str:
    """SASL XOAUTH2 initial client response (IMAP/SMTP)."""
    return f"user={user}\x01auth=Bearer {access_token}\x01\x01"

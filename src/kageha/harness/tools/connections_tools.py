"""Agent tools for OAuth-backed connections (Gmail, Calendar, Drive, GitHub)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

from kageha.harness.approvals import ApprovalDecision, ApprovalRequest
from kageha.harness.tools.base import ToolRegistry, tool

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext


def register_connections_tools(ctx: "HarnessContext") -> ToolRegistry:
    reg = ToolRegistry()
    gate = ctx.approvals

    @tool(
        description=(
            "List configured OAuth connections and whether each is logged in "
            "(gmail, gcal, gdrive, github). If disconnected, tell the user to run "
            "`kageha connect login <id>`."
        ),
        risk_class="safe",
    )
    async def connections_status() -> str:
        from kageha.connections.registry import list_providers
        from kageha.connections.store import ConnectionStore

        store = ConnectionStore()
        rows = []
        for p in list_providers():
            st = p.status(store=store)
            rows.append(st.as_dict())
        return json.dumps(rows, indent=2)

    @tool(
        description=(
            "List recent Gmail messages via the connected Gmail OAuth account. "
            "Requires `kageha connect login gmail`. Optional Gmail search query."
        ),
        risk_class="network",
    )
    async def gmail_list(query: str = "", max_results: int = 10) -> str:
        from kageha.connections import gmail_api

        try:
            msgs = gmail_api.list_messages(query=query, max_results=max_results)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
        return json.dumps(msgs, indent=2)

    @tool(
        description=(
            "Send an email via the connected Gmail OAuth account. "
            "Requires `kageha connect login gmail`. HITL approval required."
        ),
        risk_class="messaging",
    )
    async def gmail_send(to: str, subject: str, body: str) -> str:
        from kageha.connections import gmail_api

        ok = await gate.require(
            ApprovalRequest(
                action="gmail_send",
                detail=f"to={to}\nsubject={subject}\n{body[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return "ERROR: DENIED"
        try:
            result = gmail_api.send_message(to=to, subject=subject, body=body)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
        return json.dumps(result, indent=2)

    @tool(
        description=(
            "List upcoming Google Calendar events (primary calendar). "
            "Requires `kageha connect login gcal`."
        ),
        risk_class="network",
    )
    async def gcal_list_events(max_results: int = 10) -> str:
        from kageha.connections.providers.gcal import GoogleCalendarProvider
        from kageha.connections.store import ConnectionStore

        store = ConnectionStore()
        provider = GoogleCalendarProvider()
        try:
            token = provider.access_token(store=store)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
        params = {
            "maxResults": max(1, min(50, int(max_results))),
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        from datetime import datetime, timezone

        params["timeMin"] = datetime.now(timezone.utc).isoformat()
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    "https://www.googleapis.com/calendar/v3/calendars/primary/events",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
            if resp.status_code >= 400:
                return f"ERROR: Calendar API HTTP {resp.status_code}: {resp.text[:300]}"
            items = []
            for ev in resp.json().get("items") or []:
                start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get(
                    "date"
                )
                items.append(
                    {
                        "id": ev.get("id"),
                        "summary": ev.get("summary"),
                        "start": start,
                        "htmlLink": ev.get("htmlLink"),
                    }
                )
            return json.dumps(items, indent=2)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    @tool(
        description=(
            "List files in Google Drive (read-only). "
            "Requires `kageha connect login gdrive`."
        ),
        risk_class="network",
    )
    async def gdrive_list_files(query: str = "", max_results: int = 10) -> str:
        from kageha.connections.providers.gdrive import GoogleDriveProvider
        from kageha.connections.store import ConnectionStore

        store = ConnectionStore()
        provider = GoogleDriveProvider()
        try:
            token = provider.access_token(store=store)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
        params: dict[str, str | int] = {
            "pageSize": max(1, min(50, int(max_results))),
            "fields": "files(id,name,mimeType,modifiedTime,webViewLink)",
        }
        if query.strip():
            params["q"] = query.strip()
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    "https://www.googleapis.com/drive/v3/files",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
            if resp.status_code >= 400:
                return f"ERROR: Drive API HTTP {resp.status_code}: {resp.text[:300]}"
            return json.dumps(resp.json().get("files") or [], indent=2)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    @tool(
        description=(
            "Call the GitHub REST API as the connected user (GET by default). "
            "Requires `kageha connect login github`. path like /user or /repos/owner/repo."
        ),
        risk_class="network",
    )
    async def github_api(path: str, method: str = "GET") -> str:
        from kageha.connections.providers.github import GitHubProvider
        from kageha.connections.store import ConnectionStore

        store = ConnectionStore()
        provider = GitHubProvider()
        try:
            token = provider.access_token(store=store)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
        method_u = (method or "GET").strip().upper()
        if method_u not in {"GET", "HEAD"}:
            ok = await gate.require(
                ApprovalRequest(
                    action="github_api",
                    detail=f"{method_u} {path}",
                    risk_class="network",
                    default=ApprovalDecision.ASK,
                )
            )
            if not ok:
                return "ERROR: DENIED"
        url_path = path if path.startswith("/") else f"/{path}"
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.request(
                    method_u,
                    f"https://api.github.com{url_path}",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            text = resp.text
            if len(text) > 8000:
                text = text[:8000] + "\n…(truncated)"
            return json.dumps(
                {"status": resp.status_code, "body": text},
                indent=2,
            )
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    return reg

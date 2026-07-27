"""Run a chat turn against a long-lived App Server (unix/ws attach)."""

from __future__ import annotations

import uuid
from typing import Any

from kageha.app_server_client import RemoteAppServer, resolve_attach_url


async def remote_ping(attach: str) -> dict[str, Any]:
    url = resolve_attach_url(attach)
    if not url:
        raise ValueError("attach URL required")
    client = RemoteAppServer(url)
    try:
        resp = await client.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}
        )
    finally:
        client.close()
    if "error" in resp:
        raise RuntimeError(str(resp["error"]))
    return resp.get("result") or {}


async def remote_turn(
    *,
    attach: str,
    message: str,
    thread_id: str | None = None,
    session_id: str | None = None,
    project_root: str = "",
    auto_approve: bool = True,
    auto_build: bool = False,
    agent_mode: str = "normal",
    loop_mode: str = "followup",
    max_steps: int = 24,
) -> dict[str, Any]:
    """Start/continue a thread on the remote App Server and return the turn result."""
    url = resolve_attach_url(attach)
    if not url:
        raise ValueError("attach URL required")
    tid = thread_id or f"cli-{uuid.uuid4().hex[:10]}"
    client = RemoteAppServer(url)
    try:
        start = await client.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "thread/start",
                "params": {"thread_id": tid},
            }
        )
        if "error" in start:
            raise RuntimeError(str(start["error"]))
        params: dict[str, Any] = {
            "thread_id": tid,
            "message": message,
            "auto_approve": auto_approve,
            "auto_build": auto_build,
            "agent_mode": agent_mode,
            "loop_mode": loop_mode,
            "max_steps": max_steps,
            "platform": "cli-attach",
        }
        if session_id:
            params["run_id"] = session_id
            params["session_id"] = session_id
        if project_root:
            params["project_root"] = project_root
        turn = await client.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "thread/turn",
                "params": params,
            }
        )
        if "error" in turn:
            raise RuntimeError(str(turn["error"]))
        result = turn.get("result") or {}
        if isinstance(result, dict):
            result.setdefault("thread_id", tid)
        return result if isinstance(result, dict) else {"result": result, "thread_id": tid}
    finally:
        client.close()

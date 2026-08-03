"""Run a chat turn against a long-lived App Server (unix/ws attach)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable

from kageha.app_server_client import RemoteAppServer, resolve_attach_url

StatusHandler = Callable[[str], None]


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


def _event_to_status(kind: str, payload: dict[str, Any] | None) -> str:
    """Map a runtime event into controller-style telemetry for TransientProgress."""
    data = payload if isinstance(payload, dict) else {}
    if kind == "accepted":
        return "[kageha] starting…"
    if kind == "planning_started":
        return "[kageha] planning…"
    if kind == "planned":
        return "[kageha] plan ready"
    if kind == "tool_started":
        tool = str(data.get("tool") or data.get("tool_name") or "tool")
        preview = str(data.get("args_preview") or "").strip()
        if preview:
            return f"[kageha]   action: {tool} {preview}"
        return f"[kageha]   tools: {tool}"
    if kind == "tool_completed":
        tool = str(data.get("tool") or data.get("tool_name") or "tool")
        state = str(data.get("status") or data.get("state") or "ok")
        return f"[kageha]   ← {tool}: {state}"
    if kind in {"verification_started", "verification"}:
        return "[kageha] verify=checking"
    if kind == "progress":
        label = str(data.get("label") or data.get("message") or "").strip()
        if label:
            return f"[kageha] {label}"
        return "[kageha] step 1/1 — thinking…"
    if kind == "todo_board":
        done = data.get("done")
        total = data.get("total")
        if done is not None and total is not None:
            return f"[kageha] todos: {done}/{total}"
        return "[kageha] todos: 0/0"
    if kind == "approval_required":
        return "[kageha] tools: ask_human"
    if kind == "failed":
        err = str(data.get("error") or data.get("reason") or "failed")
        return f"[kageha] model error: {err[:160]}"
    if kind == "completed":
        return "[kageha] Checking the result…"
    if kind == "checkpoint":
        return "[kageha] step 1/1 — thinking…"
    return ""


async def _poll_remote_events(
    client: RemoteAppServer,
    *,
    thread_id: str,
    on_status: StatusHandler,
    stop: asyncio.Event,
    poll_interval: float = 0.15,
) -> None:
    """Poll thread/events until ``stop`` while a blocking thread/turn runs."""
    after = 0
    req_id = 100
    while not stop.is_set():
        req_id += 1
        try:
            resp = await client.handle(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "thread/events",
                    "params": {
                        "thread_id": thread_id,
                        "after_sequence": after,
                    },
                }
            )
        except Exception:  # noqa: BLE001
            await asyncio.sleep(poll_interval)
            continue
        if "error" in resp:
            await asyncio.sleep(poll_interval)
            continue
        events = resp.get("result") or []
        if not isinstance(events, list):
            await asyncio.sleep(poll_interval)
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            seq = int(event.get("sequence") or 0)
            if seq > after:
                after = seq
            kind = str(event.get("kind") or "")
            payload = event.get("payload")
            status = _event_to_status(
                kind, payload if isinstance(payload, dict) else {}
            )
            if status:
                try:
                    on_status(status)
                except Exception:  # noqa: BLE001
                    pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass


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
    on_status: StatusHandler | None = None,
) -> dict[str, Any]:
    """Start/continue a thread on the remote App Server and return the turn result.

    When ``on_status`` is provided, polls ``thread/events`` concurrently so the
    CLI can show Cursor-style progress while ``thread/turn`` blocks.
    """
    url = resolve_attach_url(attach)
    if not url:
        raise ValueError("attach URL required")
    tid = thread_id or f"cli-{uuid.uuid4().hex[:10]}"
    client = RemoteAppServer(url)
    try:
        if on_status is not None:
            try:
                on_status("[kageha] starting…")
            except Exception:  # noqa: BLE001
                pass
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

        turn_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "thread/turn",
            "params": params,
        }

        if on_status is None:
            turn = await client.handle(turn_req)
        else:
            try:
                on_status("[kageha] step 1/40 — thinking…")
            except Exception:  # noqa: BLE001
                pass
            stop = asyncio.Event()
            poll_task = asyncio.create_task(
                _poll_remote_events(
                    client,
                    thread_id=tid,
                    on_status=on_status,
                    stop=stop,
                )
            )
            try:
                turn = await client.handle(turn_req)
            finally:
                stop.set()
                try:
                    await asyncio.wait_for(poll_task, timeout=1.0)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    poll_task.cancel()
                    try:
                        await poll_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass

        if "error" in turn:
            raise RuntimeError(str(turn["error"]))
        result = turn.get("result") or {}
        if isinstance(result, dict):
            result.setdefault("thread_id", tid)
        return result if isinstance(result, dict) else {"result": result, "thread_id": tid}
    finally:
        client.close()

"""Matrix channel via Client-Server sync REST.

Set ``MATRIX_E2EE=1`` (and ``uv sync --extra matrix-e2ee``) to use Olm/Megolm
via matrix-nio instead of plain REST.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    cli_approver,
)

log = logging.getLogger(__name__)


def parse_matrix_allowlist(raw: str | None = None) -> set[str] | None:
    """Return allowed Matrix user IDs, or None for allow-all.

    Fail-closed when unset. MATRIX_ALLOW_ALL_USERS=1 or MATRIX_ALLOWED_USERS=* opens.
    """
    if os.environ.get("MATRIX_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("MATRIX_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


def _matrix_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_MATRIX_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _homeserver(url: str) -> str:
    return (url or "").strip().rstrip("/")


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class MatrixChannel:
    name = "matrix"

    def __init__(
        self,
        homeserver: str | None = None,
        *,
        access_token: str | None = None,
        user_id: str | None = None,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.homeserver = _homeserver(
            homeserver or os.environ.get("MATRIX_HOMESERVER", "")
        )
        self.access_token = (
            access_token
            if access_token is not None
            else os.environ.get("MATRIX_ACCESS_TOKEN", "")
        )
        self.user_id = (
            user_id if user_id is not None else os.environ.get("MATRIX_USER_ID", "")
        ).strip()
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_matrix_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._durable_queue: Any = None
        self._since: str | None = os.environ.get("MATRIX_SYNC_TOKEN") or None
        self._seen_ids: set[str] = set()

    @property
    def available(self) -> bool:
        return bool(self.homeserver and self.access_token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def _api(
        self, method: str, path: str, *, json_body: dict | None = None, params: dict | None = None
    ) -> dict[str, Any]:
        import httpx

        url = f"{self.homeserver}{path}"
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers(),
                json=json_body,
                params=params,
            )
            resp.raise_for_status()
            if not resp.content:
                return {}
            return resp.json()

    async def send_raw(self, room_id: str, text: str) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "Matrix not configured"}
        txn = str(uuid.uuid4())
        path = (
            f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}"
            f"/send/m.room.message/{txn}"
        )
        try:
            data = await self._api(
                "PUT",
                path,
                json_body={"msgtype": "m.text", "body": (text or "")[:4000]},
            )
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send(self, room_id: str, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="matrix_send",
                detail=f"room={room_id}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(room_id, text)

    def make_approver(
        self, room_id: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        key = str(room_id)

        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f"🔐 Kageha needs approval\n"
                f"action: {req.action}\n"
                f"risk: {req.risk_class}\n"
                f"{req.detail[:1500]}\n\n"
                "Reply with: y / n  (or A / B)"
            )
            answer = await self.wait_for_human(key, prompt)
            if answer is None:
                return False
            a = answer.strip().lower()
            if a in {"y", "yes", "a", "approve", "ok"}:
                return True
            if a in {"n", "no", "b", "deny", "denied"}:
                return False
            await self.send_raw(
                key,
                f"Didn't understand '{answer[:40]}' — treating as deny. Use y or n.",
            )
            return False

        return approver

    async def wait_for_human(self, room_id: str, prompt: str) -> str | None:
        key = str(room_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("matrix HITL timeout for %s", key)
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, room_id: str, text: str) -> bool:
        key = str(room_id)
        pending = self._pending_human.get(key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    def _process_sync(self, data: dict[str, Any]) -> None:
        rooms = ((data.get("rooms") or {}).get("join") or {})
        for room_id, room in rooms.items():
            timeline = (room.get("timeline") or {}).get("events") or []
            for ev in timeline:
                if ev.get("type") != "m.room.message":
                    continue
                sender = str(ev.get("sender") or "")
                if self.user_id and sender == self.user_id:
                    continue
                if self.allowed_users is not None and sender not in self.allowed_users:
                    continue
                content = ev.get("content") or {}
                if content.get("msgtype") not in (None, "m.text", "m.notice"):
                    continue
                text = (content.get("body") or "").strip()
                if not text:
                    continue
                event_id = str(ev.get("event_id") or "")
                if event_id and event_id in self._seen_ids:
                    continue
                if event_id:
                    self._seen_ids.add(event_id)
                    if len(self._seen_ids) > 5000:
                        self._seen_ids = set(list(self._seen_ids)[-2000:])
                # Schedule onto inbound from sync pump via put_nowait after filter
                self._enqueue_inbound(room_id, sender, text, event_id)

    def _enqueue_inbound(
        self, room_id: str, sender: str, text: str, event_id: str
    ) -> None:
        if self._durable_queue is not None and event_id:
            receipt = self._durable_queue.register_inbound(
                identity=sender,
                external_id=event_id,
                text=text,
            )
            if not receipt.accepted:
                return
        if self.consume_if_pending_human(room_id, text):
            return
        self._inbound.put_nowait((room_id, sender, text))

    async def _pump_sync(self) -> None:
        while True:
            try:
                params: dict[str, Any] = {"timeout": 30000}
                if self._since:
                    params["since"] = self._since
                data = await self._api("GET", "/_matrix/client/v3/sync", params=params)
                if data.get("next_batch"):
                    self._since = data["next_batch"]
                self._process_sync(data)
            except Exception as exc:  # noqa: BLE001
                log.warning("matrix sync error: %s", exc)
                await asyncio.sleep(2.0)

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        """Long-poll Matrix /sync and run the agent on each text message."""
        e2ee = os.environ.get("MATRIX_E2EE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if e2ee:
            from kageha.channels.matrix_e2ee import MatrixE2EEChannel

            ch = MatrixE2EEChannel(
                homeserver=self.homeserver,
                access_token=self.access_token,
                user_id=self.user_id,
                allowed_users=self.allowed_users,
                approval_timeout_s=self.approval_timeout_s,
            )
            await ch.poll_and_run(auto_approve_tasks=auto_approve_tasks)
            return
        if not self.available:
            raise RuntimeError("MATRIX_HOMESERVER and MATRIX_ACCESS_TOKEN required")
        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("matrix")
        self._durable_queue = DurableChannelQueue("matrix")
        pump = asyncio.create_task(self._pump_sync(), name="matrix-sync")
        try:
            while True:
                room_id, sender, text = await self._inbound.get()
                use_chat_hitl = _matrix_hitl_enabled() and not auto_approve_tasks
                approver = (
                    self.make_approver(room_id) if use_chat_hitl else cli_approver
                )
                turn = await run_channel_agent_turn(
                    phone=sender,
                    text=text,
                    store=sessions,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                    channel_note=(
                        "You are answering via Matrix. Keep replies concise and "
                        "chat-friendly. Preserve this sender's session context."
                    ),
                )
                if not turn.ok:
                    log.error("matrix turn failed for %s: %s", sender, turn.error)
                    await self.send_raw(
                        room_id,
                        "I couldn't complete that task. Ask me to retry or run "
                        "`kageha doctor --deep` for diagnostics.",
                    )
                    continue
                from kageha.loop.artifacts import format_artifacts_compact

                reply = (turn.reply or turn.status or "").strip()
                if turn.artifacts:
                    reply = (
                        f"{reply}\n\n"
                        + format_artifacts_compact(
                            run_id=turn.run_id, artifacts=turn.artifacts
                        )
                    )
                await self.send_raw(room_id, reply)
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            self._durable_queue.close()
            self._durable_queue = None

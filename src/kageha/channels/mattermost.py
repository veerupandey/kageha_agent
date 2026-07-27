"""Mattermost channel — websocket/events via REST + webhook inbound + HITL."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    cli_approver,
)

log = logging.getLogger(__name__)


def parse_mattermost_allowlist(raw: str | None = None) -> set[str] | None:
    if os.environ.get("MATTERMOST_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("MATTERMOST_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


def _mm_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_MM_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class MattermostChannel:
    """Poll Mattermost posts via REST (bot token) with chat HITL."""

    name = "mattermost"

    def __init__(
        self,
        url: str | None = None,
        *,
        token: str | None = None,
        team_id: str | None = None,
        channel_ids: str | None = None,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.base = (url or os.environ.get("MATTERMOST_URL", "")).rstrip("/")
        self.token = token or os.environ.get("MATTERMOST_TOKEN", "")
        self.team_id = team_id or os.environ.get("MATTERMOST_TEAM_ID", "")
        raw_chans = channel_ids or os.environ.get("MATTERMOST_CHANNEL_IDS", "")
        self.channel_ids = [c.strip() for c in raw_chans.split(",") if c.strip()]
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_mattermost_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._seen: set[str] = set()
        self._durable_queue: Any = None

    @property
    def available(self) -> bool:
        return bool(self.base and self.token and self.channel_ids)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def _api(
        self, method: str, path: str, *, json_body: dict | None = None
    ) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.request(
                method,
                f"{self.base}/api/v4{path}",
                headers=self._headers(),
                json=json_body,
            )
            resp.raise_for_status()
            if not resp.content:
                return {}
            return resp.json()

    async def send_raw(self, channel_id: str, text: str) -> dict[str, Any]:
        try:
            data = await self._api(
                "POST",
                "/posts",
                json_body={"channel_id": channel_id, "message": (text or "")[:4000]},
            )
            return {"ok": True, **(data if isinstance(data, dict) else {})}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send(self, channel_id: str, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="mattermost_send",
                detail=f"channel={channel_id}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(channel_id, text)

    def make_approver(
        self, channel_id: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f"🔐 Kageha needs approval\naction: {req.action}\n"
                f"{req.detail[:800]}\n\nReply: y / n"
            )
            answer = await self.wait_for_human(channel_id, prompt)
            if answer is None:
                return False
            return answer.strip().lower() in {"y", "yes", "a", "approve", "ok"}

        return approver

    async def wait_for_human(self, channel_id: str, prompt: str) -> str | None:
        key = str(channel_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, channel_id: str, text: str) -> bool:
        pending = self._pending_human.get(str(channel_id))
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def _pump(self) -> None:
        while True:
            for cid in self.channel_ids:
                try:
                    posts = await self._api("GET", f"/channels/{cid}/posts?per_page=20")
                    order = (posts or {}).get("order") or []
                    by_id = (posts or {}).get("posts") or {}
                    for pid in reversed(order):
                        if pid in self._seen:
                            continue
                        self._seen.add(pid)
                        post = by_id.get(pid) or {}
                        if post.get("type"):
                            continue
                        uid = str(post.get("user_id") or "")
                        text = (post.get("message") or "").strip()
                        if not text or not uid:
                            continue
                        if self.allowed_users is not None and uid not in self.allowed_users:
                            continue
                        if self.consume_if_pending_human(cid, text):
                            continue
                        if self._durable_queue is not None:
                            receipt = self._durable_queue.register_inbound(
                                identity=uid, external_id=pid, text=text
                            )
                            if not receipt.accepted:
                                continue
                        await self._inbound.put((cid, uid, text))
                    if len(self._seen) > 5000:
                        self._seen = set(list(self._seen)[-2000:])
                except Exception as exc:  # noqa: BLE001
                    log.warning("mattermost poll error: %s", exc)
            await asyncio.sleep(2.0)

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        if not self.available:
            raise RuntimeError(
                "MATTERMOST_URL, MATTERMOST_TOKEN, MATTERMOST_CHANNEL_IDS required"
            )
        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("mattermost")
        self._durable_queue = DurableChannelQueue("mattermost")
        pump = asyncio.create_task(self._pump(), name="mm-pump")
        try:
            while True:
                cid, uid, text = await self._inbound.get()
                use_hitl = _mm_hitl_enabled() and not auto_approve_tasks
                approver = self.make_approver(cid) if use_hitl else cli_approver
                turn = await run_channel_agent_turn(
                    phone=uid,
                    text=text,
                    store=sessions,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                    channel_note="You are answering via Mattermost. Keep replies concise.",
                )
                reply = (
                    (turn.reply or turn.status or "").strip()
                    if turn.ok
                    else "Task failed — ask me to retry."
                )
                await self.send_raw(cid, reply or "Done.")
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            self._durable_queue.close()
            self._durable_queue = None

"""Microsoft Teams channel — Incoming Webhook outbound + HTTP inbound webhook."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kageha.harness.approvals import ApprovalRequest, cli_approver

log = logging.getLogger(__name__)


def parse_teams_allowlist(raw: str | None = None) -> set[str] | None:
    if os.environ.get("TEAMS_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("TEAMS_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _teams_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_TEAMS_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class TeamsChannel:
    """Teams via Incoming Webhook URL + local inbound webhook (Bot Framework lite).

    Inbound: POST JSON ``{"from": "user@contoso.com", "text": "...", "id": "..."}``
    Outbound: Teams Incoming Webhook (MessageCard / Adaptive Text text).
    """

    name = "teams"

    def __init__(
        self,
        webhook_url: str | None = None,
        *,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.webhook_url = (
            webhook_url or os.environ.get("TEAMS_WEBHOOK_URL", "")
        ).strip()
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_teams_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._durable_queue: Any = None

    @property
    def available(self) -> bool:
        return bool(self.webhook_url)

    async def send_raw(self, text: str, *, identity: str = "") -> dict[str, Any]:
        import httpx

        del identity
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    self.webhook_url,
                    json={"text": (text or "")[:4000]},
                )
                if resp.status_code >= 400:
                    return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def make_approver(
        self, identity: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f"🔐 Kageha needs approval\naction: {req.action}\n"
                f"{req.detail[:800]}\n\nReply to the inbound webhook with: y / n"
            )
            answer = await self.wait_for_human(identity, prompt)
            if answer is None:
                return False
            return answer.strip().lower() in {"y", "yes", "a", "approve", "ok"}

        return approver

    async def wait_for_human(self, identity: str, prompt: str) -> str | None:
        key = str(identity)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(prompt, identity=key)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, identity: str, text: str) -> bool:
        pending = self._pending_human.get(str(identity))
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def handle_inbound(
        self, identity: str, text: str, *, external_id: str = ""
    ) -> None:
        identity = (identity or "").strip().lower()
        text = (text or "").strip()
        if not identity or not text:
            return
        if self.allowed_users is not None and identity not in self.allowed_users:
            return
        if self.consume_if_pending_human(identity, text):
            return
        if self._durable_queue is not None and external_id:
            receipt = self._durable_queue.register_inbound(
                identity=identity, external_id=external_id, text=text
            )
            if not receipt.accepted:
                return
        await self._inbound.put((identity, text))

    async def serve(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8790,
        path: str = "/webhook/teams",
        auto_approve_tasks: bool = False,
    ) -> None:
        if not self.available:
            raise RuntimeError("TEAMS_WEBHOOK_URL required")
        try:
            from aiohttp import web
        except ImportError as e:
            raise ImportError(
                "Teams inbound webhook needs aiohttp. Run: uv sync --extra channels"
            ) from e

        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("teams")
        self._durable_queue = DurableChannelQueue("teams")

        async def post_handler(request: web.Request) -> web.Response:
            try:
                body = await request.json()
            except Exception:  # noqa: BLE001
                return web.Response(status=400, text="invalid json")
            # Support simple JSON + Bot Framework-ish
            from_field = body.get("from")
            if isinstance(from_field, dict):
                identity = str(
                    from_field.get("aadObjectId")
                    or from_field.get("id")
                    or from_field.get("name")
                    or ""
                )
            else:
                identity = str(from_field or body.get("user") or "")
            msg_field = body.get("message")
            if isinstance(msg_field, dict):
                text = str(msg_field.get("text") or body.get("text") or "")
            else:
                text = str(body.get("text") or msg_field or "")
            mid = str(body.get("id") or body.get("message_id") or "")
            await self.handle_inbound(identity, text, external_id=mid)
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_post(path, post_handler)

        async def worker() -> None:
            while True:
                identity, text = await self._inbound.get()
                use_hitl = _teams_hitl_enabled() and not auto_approve_tasks
                approver = (
                    self.make_approver(identity) if use_hitl else cli_approver
                )
                turn = await run_channel_agent_turn(
                    phone=identity,
                    text=text,
                    store=sessions,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                    channel_note="You are answering via Microsoft Teams. Keep replies concise.",
                )
                reply = (
                    (turn.reply or turn.status or "").strip()
                    if turn.ok
                    else "Task failed — ask me to retry."
                )
                await self.send_raw(reply or "Done.", identity=identity)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        log.info("Teams webhook listening on http://%s:%s%s", host, port, path)
        task = asyncio.create_task(worker(), name="teams-worker")
        try:
            await asyncio.Future()
        finally:
            task.cancel()
            await runner.cleanup()
            self._durable_queue.close()
            self._durable_queue = None

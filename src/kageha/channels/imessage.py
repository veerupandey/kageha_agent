"""iMessage channel via BlueBubbles Server REST + webhook (OpenClaw-recommended path)."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    cli_approver,
)

log = logging.getLogger(__name__)


def parse_imessage_allowlist(raw: str | None = None) -> set[str] | None:
    """Return allowed handles (phone/email), or None for allow-all.

    Fail-closed when unset. IMESSAGE_ALLOW_ALL_USERS=1 or *=* opens.
    Also reads BLUEBUBBLES_ALLOWED_USERS as alias.
    """
    if os.environ.get("IMESSAGE_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    } or os.environ.get("BLUEBUBBLES_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = (
        raw
        if raw is not None
        else (
            os.environ.get("IMESSAGE_ALLOWED_USERS")
            or os.environ.get("BLUEBUBBLES_ALLOWED_USERS")
            or ""
        )
    )
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _imessage_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_IMESSAGE_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _api_url(base: str, path: str, password: str) -> str:
    base = base.rstrip("/")
    qs = urlencode({"password": password})
    return f"{base}{path}?{qs}"


def extract_bluebubbles_inbound(
    payload: dict[str, Any],
) -> list[tuple[str, str, str, str]]:
    """Parse BlueBubbles webhook → list of (handle, text, message_guid, chat_guid)."""
    out: list[tuple[str, str, str, str]] = []
    event_type = str(payload.get("type") or payload.get("event") or "").lower()
    if event_type and event_type not in {
        "new-message",
        "message",
        "updated-message",
        "",
    }:
        # Still try data-shaped payloads without type.
        if "data" not in payload and "message" not in payload:
            return out

    data = payload.get("data") or payload.get("message") or payload
    if not isinstance(data, dict):
        return out
    # Ignore outbound / from-me echoes.
    if data.get("isFromMe") or data.get("is_from_me"):
        return out
    text = (data.get("text") or data.get("message") or "").strip()
    if not text:
        return out
    handle = ""
    handle_obj = data.get("handle") or {}
    if isinstance(handle_obj, dict):
        handle = str(handle_obj.get("address") or handle_obj.get("id") or "").strip()
    if not handle:
        handle = str(
            data.get("address")
            or data.get("sender")
            or data.get("handleId")
            or ""
        ).strip()
    chat_guid = ""
    chats = data.get("chats")
    if isinstance(chats, list) and chats and isinstance(chats[0], dict):
        chat_guid = str(chats[0].get("guid") or "").strip()
    if not chat_guid:
        chat_guid = str(
            data.get("chatGuid") or data.get("chat_guid") or ""
        ).strip()
    msg_guid = str(
        data.get("guid") or data.get("tempGuid") or data.get("id") or ""
    ).strip()
    if handle and text:
        out.append((handle.lower(), text, msg_guid or f"{handle}:{text[:24]}", chat_guid))
    return out


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class iMessageChannel:
    """BlueBubbles-backed iMessage adapter."""

    name = "imessage"

    def __init__(
        self,
        server_url: str | None = None,
        *,
        password: str | None = None,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.server_url = (
            server_url
            or os.environ.get("BLUEBUBBLES_URL")
            or os.environ.get("IMESSAGE_BLUEBUBBLES_URL")
            or ""
        ).strip().rstrip("/")
        self.password = (
            password
            if password is not None
            else (
                os.environ.get("BLUEBUBBLES_PASSWORD")
                or os.environ.get("IMESSAGE_BLUEBUBBLES_PASSWORD")
                or ""
            )
        )
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_imessage_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._durable_queue: Any = None
        self._chat_by_handle: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return bool(self.server_url and self.password)

    async def send_raw(
        self, handle: str, text: str, *, chat_guid: str | None = None
    ) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "BlueBubbles not configured"}
        import httpx

        key = handle.lower()
        guid = chat_guid or self._chat_by_handle.get(key)
        payload: dict[str, Any]
        path: str
        if guid:
            path = "/api/v1/message/text"
            payload = {
                "chatGuid": guid,
                "tempGuid": str(uuid.uuid4()),
                "message": (text or "")[:4000],
            }
        else:
            # Create new chat with first message
            path = "/api/v1/chat/new"
            payload = {
                "addresses": [handle],
                "message": (text or "")[:4000],
            }
        url = _api_url(self.server_url, path, self.password)
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                if not resp.is_success:
                    return {
                        "ok": False,
                        "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
                    }
                return {"ok": True, "body": resp.text}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send(self, handle: str, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="imessage_send",
                detail=f"to={handle}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(handle, text)

    def make_approver(
        self, handle: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        key = handle.lower()

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

    async def wait_for_human(self, handle: str, prompt: str) -> str | None:
        key = handle.lower()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("imessage HITL timeout for %s", key)
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, handle: str, text: str) -> bool:
        key = handle.lower()
        pending = self._pending_human.get(key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def handle_webhook_payload(self, payload: dict[str, Any]) -> int:
        """Process one BlueBubbles webhook JSON body. Returns accepted count."""
        accepted = 0
        for handle, text, msg_guid, chat_guid in extract_bluebubbles_inbound(payload):
            if self.allowed_users is not None and handle not in self.allowed_users:
                continue
            if chat_guid:
                self._chat_by_handle[handle] = chat_guid
            if self._durable_queue is not None:
                receipt = self._durable_queue.register_inbound(
                    identity=handle,
                    external_id=msg_guid,
                    text=text,
                )
                if not receipt.accepted:
                    continue
            if self.consume_if_pending_human(handle, text):
                accepted += 1
                continue
            await self._inbound.put((handle, text, chat_guid))
            accepted += 1
        return accepted

    async def serve(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8790,
        path: str = "/webhook/imessage",
        auto_approve_tasks: bool = False,
    ) -> None:
        """HTTP webhook for BlueBubbles + agent turn loop."""
        if not self.available:
            raise RuntimeError("BLUEBUBBLES_URL and BLUEBUBBLES_PASSWORD required")
        try:
            from aiohttp import web
        except ImportError as e:
            raise ImportError(
                "channels extra not installed. Run: uv sync --extra channels"
            ) from e

        from kageha.channels.session_memory import (
            ChannelSessionStore,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("imessage")
        self._durable_queue = DurableChannelQueue("imessage")

        async def webhook(request: web.Request) -> web.Response:
            # Optional shared-secret via query ?password= or ?guid=
            q_pass = request.query.get("password") or request.query.get("guid") or ""
            expected = self.password
            if expected and q_pass and q_pass != expected:
                return web.Response(status=401, text="unauthorized")
            try:
                payload = await request.json()
            except Exception:  # noqa: BLE001
                return web.Response(status=400, text="invalid json")
            if not isinstance(payload, dict):
                return web.Response(status=400, text="expected object")
            n = await self.handle_webhook_payload(payload)
            return web.json_response({"ok": True, "accepted": n})

        app = web.Application()
        app.router.add_post(path, webhook)

        async def health(_request: Any):
            return web.Response(text="kageha imessage ok")

        app.router.add_get(path, health)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        log.info("iMessage/BlueBubbles webhook on http://%s:%s%s", host, port, path)

        worker = asyncio.create_task(
            self._run_turns(sessions, auto_approve_tasks=auto_approve_tasks),
            name="imessage-turns",
        )
        try:
            await asyncio.Future()  # run forever
        finally:
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
            await runner.cleanup()
            self._durable_queue.close()
            self._durable_queue = None

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        """Alias: start default webhook server."""
        await self.serve(auto_approve_tasks=auto_approve_tasks)

    async def _run_turns(
        self, sessions: Any, *, auto_approve_tasks: bool
    ) -> None:
        from kageha.channels.session_memory import run_channel_agent_turn

        while True:
            handle, text, chat_guid = await self._inbound.get()
            if chat_guid:
                self._chat_by_handle[handle] = chat_guid
            use_chat_hitl = _imessage_hitl_enabled() and not auto_approve_tasks
            approver = self.make_approver(handle) if use_chat_hitl else cli_approver
            turn = await run_channel_agent_turn(
                phone=handle,
                text=text,
                store=sessions,
                auto_approve=auto_approve_tasks,
                approver=approver,
                channel_note=(
                    "You are answering via iMessage. Keep replies concise and "
                    "chat-friendly. Preserve this sender's session context."
                ),
            )
            if not turn.ok:
                log.error("imessage turn failed for %s: %s", handle, turn.error)
                await self.send_raw(
                    handle,
                    "I couldn't complete that task. Ask me to retry or run "
                    "`kageha doctor --deep` for diagnostics.",
                    chat_guid=chat_guid or None,
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
            await self.send_raw(handle, reply, chat_guid=chat_guid or None)

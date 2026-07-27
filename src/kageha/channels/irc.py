"""IRC channel — asyncio TCP with chat-native HITL."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
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


def parse_irc_allowlist(raw: str | None = None) -> set[str] | None:
    if os.environ.get("IRC_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("IRC_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def _irc_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_IRC_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class IRCChannel:
    name = "irc"

    def __init__(
        self,
        host: str | None = None,
        *,
        port: int | None = None,
        nick: str | None = None,
        channels: str | None = None,
        password: str | None = None,
        use_tls: bool | None = None,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.host = (host or os.environ.get("IRC_HOST", "")).strip()
        self.port = int(port or os.environ.get("IRC_PORT") or "6697")
        self.nick = (nick or os.environ.get("IRC_NICK", "kageha")).strip()
        self.channels = [
            c.strip()
            for c in (channels or os.environ.get("IRC_CHANNELS", "")).split(",")
            if c.strip()
        ]
        self.password = password if password is not None else os.environ.get("IRC_PASSWORD", "")
        tls_raw = os.environ.get("IRC_TLS", "1").strip().lower()
        self.use_tls = use_tls if use_tls is not None else tls_raw not in {
            "0",
            "false",
            "no",
            "off",
        }
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_irc_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._durable_queue: Any = None

    @property
    def available(self) -> bool:
        return bool(self.host and self.nick and self.channels)

    async def _send_line(self, line: str) -> None:
        if self._writer is None:
            return
        self._writer.write((line + "\r\n").encode("utf-8", errors="replace"))
        await self._writer.drain()

    async def send_raw(self, target: str, text: str) -> dict[str, Any]:
        if not self.available or self._writer is None:
            return {"ok": False, "error": "IRC not connected"}
        # IRC PRIVMSG line length ~512; chunk body
        body = (text or "").replace("\n", " ").strip()
        try:
            for i in range(0, max(1, len(body)), 350):
                await self._send_line(f"PRIVMSG {target} :{body[i : i + 350]}")
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send(self, target: str, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="irc_send",
                detail=f"target={target}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(target, text)

    def make_approver(
        self, target: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        key = str(target)

        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f"Kageha needs approval | action={req.action} | "
                f"{req.detail[:200]} | Reply: y / n"
            )
            answer = await self.wait_for_human(key, prompt)
            if answer is None:
                return False
            a = answer.strip().lower()
            return a in {"y", "yes", "a", "approve", "ok"}

        return approver

    async def wait_for_human(self, target: str, prompt: str) -> str | None:
        key = str(target)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("irc HITL timeout for %s", key)
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, target: str, text: str) -> bool:
        key = str(target)
        pending = self._pending_human.get(key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def _pump(self) -> None:
        assert self._reader is not None
        while True:
            line_b = await self._reader.readline()
            if not line_b:
                await asyncio.sleep(1.0)
                continue
            line = line_b.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("PING "):
                await self._send_line("PONG " + line[5:])
                continue
            # :nick!user@host PRIVMSG #chan :text
            if " PRIVMSG " not in line:
                continue
            try:
                prefix, rest = line[1:].split(" PRIVMSG ", 1)
                nick = prefix.split("!", 1)[0].lower()
                target, text = rest.split(" :", 1)
            except ValueError:
                continue
            if self.allowed_users is not None and nick not in self.allowed_users:
                continue
            # Prefer replying in-channel; for PMs target is our nick → reply to sender
            reply_to = target if target.startswith("#") else nick
            if self.consume_if_pending_human(reply_to, text):
                continue
            if self._durable_queue is not None:
                receipt = self._durable_queue.register_inbound(
                    identity=nick,
                    external_id=f"{nick}:{hash(text) & 0xFFFFFFFF:x}",
                    text=text,
                )
                if not receipt.accepted:
                    continue
            await self._inbound.put((reply_to, nick, text))

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        if not self.available:
            raise RuntimeError("IRC_HOST, IRC_NICK, and IRC_CHANNELS required")
        ssl_ctx = ssl.create_default_context() if self.use_tls else None
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port, ssl=ssl_ctx
        )
        if self.password:
            await self._send_line(f"PASS {self.password}")
        await self._send_line(f"NICK {self.nick}")
        await self._send_line(f"USER {self.nick} 0 * :kageha")
        await asyncio.sleep(1.0)
        for ch in self.channels:
            await self._send_line(f"JOIN {ch}")

        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("irc")
        self._durable_queue = DurableChannelQueue("irc")
        pump = asyncio.create_task(self._pump(), name="irc-pump")
        log.info("IRC connected to %s as %s channels=%s", self.host, self.nick, self.channels)
        try:
            while True:
                target, nick, text = await self._inbound.get()
                use_hitl = _irc_hitl_enabled() and not auto_approve_tasks
                approver = self.make_approver(target) if use_hitl else cli_approver
                turn = await run_channel_agent_turn(
                    phone=nick,
                    text=text,
                    store=sessions,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                    channel_note="You are answering via IRC. Keep replies short.",
                )
                if not turn.ok:
                    await self.send_raw(target, "Task failed — ask me to retry.")
                    continue
                reply = (turn.reply or turn.status or "").strip() or "Done."
                await self.send_raw(target, reply)
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            if self._writer is not None:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
            if self._durable_queue is not None:
                self._durable_queue.close()
                self._durable_queue = None

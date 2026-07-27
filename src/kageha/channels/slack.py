"""Slack bot channel — Socket Mode text turns with chat-native HITL."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kageha.channels.session_memory import ChannelSessionStore, run_channel_agent_turn
from kageha.harness.approvals import ApprovalRequest

log = logging.getLogger(__name__)


def parse_slack_allowlist(raw: str | None = None) -> set[str] | None:
    if os.environ.get("SLACK_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("SLACK_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


def _slack_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_SLACK_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str
    channel: str
    user: str


class SlackChannel:
    name = "slack"

    def __init__(
        self,
        bot_token: str | None = None,
        app_token: str | None = None,
        *,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 120.0,
    ) -> None:
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self.app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self.allowed_users = (
            allowed_users
            if allowed_users is not None
            else parse_slack_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self.store = ChannelSessionStore(channel="slack")
        self.queue: Any = None
        self._pending_human: dict[str, _PendingHuman] = {}
        self._say: Any = None

    @property
    def available(self) -> bool:
        return bool(self.bot_token and self.app_token)

    def _pending_key(self, channel: str, user: str) -> str:
        return f"{channel}:{user}"

    def make_approver(
        self, channel: str, user: str, say: Any
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f":lock: *Kageha needs approval*\n"
                f"action: `{req.action}`\n"
                f"risk: {req.risk_class}\n"
                f"{req.detail[:1500]}\n\n"
                "Reply with: `y` / `n`"
            )
            answer = await self.wait_for_human(channel, user, prompt, say)
            if answer is None:
                return False
            a = answer.strip().lower()
            if a in {"y", "yes", "a", "approve", "ok"}:
                return True
            if a in {"n", "no", "b", "deny", "denied"}:
                return False
            await say(
                f"Didn't understand `{answer[:40]}` — treating as deny. Use y or n."
            )
            return False

        return approver

    async def wait_for_human(
        self, channel: str, user: str, prompt: str, say: Any
    ) -> str | None:
        key = self._pending_key(channel, user)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(
            future=fut, prompt=prompt, channel=channel, user=user
        )
        try:
            await say(prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("slack HITL timeout for %s", key)
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, channel: str, user: str, text: str) -> bool:
        key = self._pending_key(channel, user)
        pending = self._pending_human.get(key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        """Run Slack Bolt Socket Mode app (blocks)."""
        if not self.available:
            raise RuntimeError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN required")
        try:
            from slack_bolt.adapter.socket_mode.async_handler import (  # type: ignore
                AsyncSocketModeHandler,
            )
            from slack_bolt.async_app import AsyncApp  # type: ignore
        except ImportError as e:
            raise ImportError(
                "Slack extra not installed. Run: uv sync --extra slack "
                "(or pip install slack-bolt)"
            ) from e

        app = AsyncApp(token=self.bot_token)

        @app.event("message")
        async def handle_message(event: dict[str, Any], say: Any) -> None:  # noqa: ANN401
            if event.get("subtype") or event.get("bot_id"):
                return
            uid = str(event.get("user") or "")
            channel = str(event.get("channel") or "")
            text = (event.get("text") or "").strip()
            if not uid or not text or not channel:
                return
            if self.allowed_users is not None and uid not in self.allowed_users:
                return

            # HITL reply while a turn is waiting
            if self.consume_if_pending_human(channel, uid, text):
                return

            if self.queue is None:
                from kageha.runtime.channels import DurableChannelQueue

                self.queue = DurableChannelQueue(self.name)
            receipt = self.queue.register_inbound(
                identity=uid,
                external_id=str(event.get("client_msg_id") or event.get("ts") or ""),
                text=text,
            )
            if not receipt.accepted:
                return

            use_hitl = _slack_hitl_enabled() and not auto_approve_tasks
            approver = (
                self.make_approver(channel, uid, say) if use_hitl else None
            )

            turn = await run_channel_agent_turn(
                phone=uid,
                text=text,
                store=self.store,
                auto_approve=auto_approve_tasks,
                approver=approver,
                channel_note=(
                    "You are answering via Slack. Keep replies concise and chat-friendly. "
                    "Remember prior turns in this session."
                ),
            )
            if turn.ok:
                reply = (turn.reply or "Done.").strip()
            else:
                log.error("slack turn failed for %s: %s", uid, turn.error)
                reply = (
                    "I couldn't complete that task. Ask me to retry or run "
                    "`kageha doctor --deep` for diagnostics."
                )
            for i in range(0, max(1, len(reply)), 3500):
                await say(reply[i : i + 3500])

        handler = AsyncSocketModeHandler(app, self.app_token)
        log.info("Slack Socket Mode starting (HITL=%s)", _slack_hitl_enabled())
        await handler.start_async()

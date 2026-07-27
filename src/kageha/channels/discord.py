"""Discord bot channel — DM/text turns via discord.py gateway + button HITL."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from kageha.channels.session_memory import ChannelSessionStore, run_channel_agent_turn
from kageha.harness.approvals import ApprovalRequest

log = logging.getLogger(__name__)


def parse_discord_allowlist(raw: str | None = None) -> set[str] | None:
    if os.environ.get("DISCORD_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("DISCORD_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


def _discord_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_DISCORD_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


class DiscordChannel:
    name = "discord"

    def __init__(
        self,
        token: str | None = None,
        *,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 120.0,
    ) -> None:
        self.token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self.allowed_users = (
            allowed_users
            if allowed_users is not None
            else parse_discord_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self.store = ChannelSessionStore(channel="discord")
        self.queue: Any = None

    @property
    def available(self) -> bool:
        return bool(self.token)

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        """Connect to Discord gateway and handle DM / mentioned messages."""
        if not self.available:
            raise RuntimeError("DISCORD_BOT_TOKEN missing")
        try:
            import discord  # type: ignore
        except ImportError as e:
            raise ImportError(
                "Discord extra not installed. Run: uv sync --extra discord "
                "(or pip install discord.py)"
            ) from e

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        client = discord.Client(intents=intents)

        class _ApproveView(discord.ui.View):
            def __init__(self, owner_id: int, timeout: float) -> None:
                super().__init__(timeout=timeout)
                self.owner_id = owner_id
                self.decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

            async def interaction_check(self, interaction: Any) -> bool:
                if interaction.user.id != self.owner_id:
                    await interaction.response.send_message(
                        "Only the requester can approve.", ephemeral=True
                    )
                    return False
                return True

            @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
            async def approve(self, interaction: Any, button: Any) -> None:  # noqa: ARG002
                if not self.decision.done():
                    self.decision.set_result(True)
                await interaction.response.send_message("Approved.", ephemeral=True)
                self.stop()

            @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
            async def deny(self, interaction: Any, button: Any) -> None:  # noqa: ARG002
                if not self.decision.done():
                    self.decision.set_result(False)
                await interaction.response.send_message("Denied.", ephemeral=True)
                self.stop()

        @client.event
        async def on_ready() -> None:
            log.info("Discord connected as %s", client.user)

        @client.event
        async def on_message(message: Any) -> None:
            if message.author.bot:
                return
            uid = str(message.author.id)
            if self.allowed_users is not None and uid not in self.allowed_users:
                return
            is_dm = message.guild is None
            mentioned = client.user and client.user.mentioned_in(message)
            if not is_dm and not mentioned:
                return
            text = (message.content or "").strip()
            if client.user:
                text = text.replace(f"<@{client.user.id}>", "").strip()
            if not text:
                return
            if self.queue is None:
                from kageha.runtime.channels import DurableChannelQueue

                self.queue = DurableChannelQueue(self.name)
            receipt = self.queue.register_inbound(
                identity=uid,
                external_id=str(message.id),
                text=text,
            )
            if not receipt.accepted:
                return

            async def approver(req: ApprovalRequest) -> bool:
                if not _discord_hitl_enabled():
                    return False
                prompt = (
                    f"Approve `{req.action}`?\n{req.detail[:500]}\n"
                    "Use the buttons, or reply **y** / **n** within "
                    f"{int(self.approval_timeout_s)}s."
                )
                view = _ApproveView(message.author.id, self.approval_timeout_s)
                await message.channel.send(prompt[:1900], view=view)

                def check(m: Any) -> bool:
                    return (
                        m.author.id == message.author.id
                        and m.channel.id == message.channel.id
                        and (m.content or "").strip().lower()
                        in {"y", "n", "yes", "no"}
                    )

                text_task = asyncio.create_task(
                    client.wait_for("message", check=check, timeout=self.approval_timeout_s)
                )
                button_task = asyncio.create_task(
                    asyncio.wait_for(view.decision, timeout=self.approval_timeout_s)
                )
                done, pending = await asyncio.wait(
                    {text_task, button_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                try:
                    finished = next(iter(done))
                    result = finished.result()
                except Exception:  # noqa: BLE001
                    return False
                if isinstance(result, bool):
                    return result
                return (getattr(result, "content", "") or "").strip().lower() in {
                    "y",
                    "yes",
                }

            turn = await run_channel_agent_turn(
                phone=uid,
                text=text,
                store=self.store,
                auto_approve=auto_approve_tasks,
                approver=None if auto_approve_tasks else approver,
                channel_note=(
                    "You are answering via Discord. Keep replies concise. "
                    "Remember prior turns in this session."
                ),
            )
            if turn.ok:
                reply = (turn.reply or "Done.").strip()
            else:
                log.error("discord turn failed for %s: %s", uid, turn.error)
                reply = (
                    "I couldn't complete that task. Ask me to retry or run "
                    "`kageha doctor --deep` for diagnostics."
                )
            for i in range(0, max(1, len(reply)), 1900):
                await message.channel.send(reply[i : i + 1900])

        await client.start(self.token)

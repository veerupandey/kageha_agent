"""Bridge normalized channel messages into the durable Kageha runtime."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

from kageha.app_server import AppServer
from kageha.channels.models import ChannelMessage, ChannelReply
from kageha.config import sessions_dir
from kageha.loop.artifacts import classify_artifacts
from kageha.runtime.channels import DurableChannelQueue
from kageha.runtime.store import RuntimeStore


def _chunk(text: str, limit: int = 4096) -> list[str]:
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


class ChannelRuntime:
    """Shared session, dedupe, and runtime bridge for all adapters."""

    def __init__(self, server: AppServer | None = None, store: RuntimeStore | None = None):
        self.server = server or AppServer()
        self.store = store or RuntimeStore()
        self._owns_server = server is None
        self._owns_store = store is None
        self._locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        if self._owns_server:
            self.server.close()
        if self._owns_store:
            self.store.close()

    async def process(self, message: ChannelMessage) -> ChannelReply | None:
        queue = DurableChannelQueue(message.channel, self.store)
        receipt = queue.register_inbound(
            identity=message.identity,
            external_id=message.message_id,
            text=message.text,
            payload={
                "text": message.text,
                "thread_id": message.thread_id,
                "media": [media.__dict__ for media in message.media],
                "metadata": message.metadata,
            },
        )
        if not receipt.accepted:
            return None

        lock = self._locks.setdefault(message.identity, asyncio.Lock())
        async with lock:
            prior = self.store.latest_channel_session(
                channel=message.channel,
                identity_key=queue.identity_key(message.identity),
            )
            prompt = message.text.strip()
            if message.media:
                attachments = [media.local_path for media in message.media if media.local_path]
                if attachments:
                    prompt = (
                        f"{prompt}\n\n" if prompt else ""
                    ) + "Attached files:\n" + "\n".join(
                        f"- `{path}`" for path in attachments
                    )
            if not prompt:
                prompt = "Please inspect the attached media and describe what you found."
            thread_id = f"{message.channel}-{message.identity}"
            params: dict[str, Any] = {
                "thread_id": thread_id,
                "message": prompt,
                "run_id": prior or "",
                "auto_approve": False,
                "auto_build": False,
                "user_id": message.peer_id,
                "channel_key": message.channel,
                "platform": message.channel,
                "loop_mode": "followup",
                "agent_mode": "normal",
                "defer_human_input": True,
            }
            request = {"jsonrpc": "2.0", "id": 1, "method": "thread/turn", "params": params}
            response = await self.server.handle(request)
            if "error" in response:
                detail = response["error"].get("message", "channel turn failed")
                return ChannelReply(text=f"I couldn't complete that request: {detail}")
            result = response.get("result") or {}
            run_id = str(result.get("run_id") or prior or "")
            text = str(result.get("message") or "").strip()
            artifacts = tuple(
                classify_artifacts([str(item) for item in result.get("artifacts") or []])
            )
            for part in _chunk(text):
                queue.enqueue_outbound(
                    identity=message.identity,
                    text=part,
                    idempotency_key=(
                        f"{message.message_id}:reply:"
                        f"{hashlib.sha256(part.encode('utf-8')).hexdigest()}"
                    ),
                    session_id=run_id,
                    turn_id=str(result.get("turn_id") or ""),
                )
            return ChannelReply(text=text, artifacts=artifacts, metadata={"run_id": run_id})


def artifact_path(run_id: str, relative: str) -> Path | None:
    """Resolve a runtime artifact without permitting workspace escape."""
    root = (sessions_dir() / run_id).resolve()
    candidate = (root / relative).resolve()
    if not str(candidate).startswith(str(root)) or not candidate.is_file():
        return None
    return candidate


def chunk_text(text: str, limit: int = 4096) -> list[str]:
    return _chunk(text, limit)

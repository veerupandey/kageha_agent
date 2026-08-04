"""Small Telegram Bot API adapter using long polling by default."""

from __future__ import annotations

import asyncio
import html
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from kageha.channels.models import ChannelMedia, ChannelMessage, ChannelReply
from kageha.channels.runtime import ChannelRuntime, artifact_path, chunk_text
from kageha.config import kageha_home
from kageha.runtime.channels import DurableChannelQueue


def _allowlist(name: str) -> set[str]:
    return {item.strip() for item in os.environ.get(name, "").split(",") if item.strip()}


def markdown_to_telegram_html(text: str) -> str:
    """Convert the common assistant Markdown subset to Telegram-safe HTML."""
    blocks: list[str] = []

    def code_block(match: re.Match[str]) -> str:
        blocks.append(f"<pre>{html.escape(match.group(1).strip(chr(10)))}</pre>")
        return f"\x00BLOCK{len(blocks) - 1}\x00"

    value = re.sub(r"```[^\n]*\n?(.*?)```", code_block, text, flags=re.DOTALL)
    value = html.escape(value, quote=False)
    value = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", value, flags=re.MULTILINE)
    value = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', value)
    value = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value, flags=re.DOTALL)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", value)
    value = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"<i>\1</i>", value)
    value = re.sub(r"^(?:[-*])\s+", "• ", value, flags=re.MULTILINE)
    for index, block in enumerate(blocks):
        value = value.replace(f"\x00BLOCK{index}\x00", block)
    return value


class TelegramAdapter:
    channel = "telegram"

    def __init__(
        self,
        runtime: ChannelRuntime,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
        on_reply: Callable[[ChannelMessage, ChannelReply], Awaitable[None]] | None = None,
    ) -> None:
        self.runtime = runtime
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")
        self.client = client or httpx.AsyncClient(timeout=40)
        self._owns_client = client is None
        self.on_reply = on_reply
        self._offset = 0
        self._stopped = asyncio.Event()
        self.allowed_users = _allowlist("TELEGRAM_ALLOWED_USERS")
        self.allow_all = os.environ.get("TELEGRAM_ALLOW_ALL_USERS", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    async def close(self) -> None:
        self._stopped.set()
        if self._owns_client:
            await self.client.aclose()

    async def call(self, method: str, **params: Any) -> Any:
        response = await self.client.post(f"{self.base_url}/{method}", json=params)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description', data)}")
        return data.get("result") or {}

    async def run(self) -> None:
        """Run long polling until cancelled."""
        while not self._stopped.is_set():
            try:
                updates = await self.call(
                    "getUpdates",
                    offset=self._offset,
                    timeout=25,
                    allowed_updates=["message", "edited_message", "callback_query"],
                )
                for update in updates:
                    self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                    await self.handle_update(update)
            except asyncio.CancelledError:
                raise
            except (httpx.HTTPError, RuntimeError):
                await asyncio.sleep(2)

    async def handle_update(self, update: dict[str, Any]) -> ChannelReply | None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return None
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        peer_id = str(chat.get("id") or "")
        user_id = str(sender.get("id") or peer_id)
        if (
            not peer_id
            or not self.allow_all
            and self.allowed_users
            and user_id not in self.allowed_users
        ):
            return None
        if not self.allow_all and not self.allowed_users:
            return None

        media = await self._download_media(message)
        text = str(message.get("text") or message.get("caption") or "")
        normalized = ChannelMessage(
            channel=self.channel,
            account_id="default",
            peer_id=peer_id,
            message_id=str(message.get("message_id") or update.get("update_id")),
            thread_id=str(message.get("message_thread_id") or ""),
            text=text,
            media=tuple(media),
            metadata={"user_id": user_id, "chat_type": chat.get("type", "")},
        )
        reply = await self.runtime.process(normalized)
        if reply is None:
            return None
        await self._send_reply(normalized, reply)
        if self.on_reply:
            await self.on_reply(normalized, reply)
        return reply

    async def _download_media(self, message: dict[str, Any]) -> list[ChannelMedia]:
        item: dict[str, Any] | None = None
        kind = "file"
        photo = message.get("photo")
        if isinstance(photo, list):
            photo_items = [value for value in photo if isinstance(value, dict)]
            if photo_items:
                item = max(photo_items, key=lambda value: int(value.get("file_size") or 0))
            kind = "image"
        for key, candidate_kind in (
            ("document", "document"),
            ("video", "video"),
            ("audio", "audio"),
            ("voice", "voice"),
        ):
            candidate = message.get(key)
            if isinstance(candidate, dict):
                item = candidate
                kind = candidate_kind
                break
        if not item or not item.get("file_id"):
            return []
        file_info = await self.call("getFile", file_id=item["file_id"])
        file_path = str(file_info.get("file_path") or "")
        if not file_path:
            return []
        response = await self.client.get(
            f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        )
        response.raise_for_status()
        root = kageha_home() / "platforms" / "telegram" / "inbound"
        root.mkdir(parents=True, exist_ok=True)
        name = Path(str(item.get("file_name") or Path(file_path).name)).name
        if not Path(name).suffix:
            name += ".bin"
        dest = root / f"{uuid.uuid4().hex[:12]}-{name}"
        dest.write_bytes(response.content)
        return [
            ChannelMedia(
                kind=kind,
                filename=name,
                content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                external_id=str(item["file_id"]),
                local_path=str(dest),
                caption=str(message.get("caption") or ""),
                size_bytes=len(response.content),
            )
        ]

    async def _send_reply(self, message: ChannelMessage, reply: ChannelReply) -> None:
        chat_id = message.peer_id
        thread_id = int(message.thread_id) if message.thread_id.isdigit() else None
        common: dict[str, Any] = {"chat_id": chat_id}
        if thread_id is not None:
            common["message_thread_id"] = thread_id
        for part in chunk_text(reply.text):
            queue = DurableChannelQueue(self.channel, self.runtime.store)
            queued = queue.claim_outbound(identity=message.identity)
            try:
                await self.call(
                    "sendMessage",
                    **common,
                    text=markdown_to_telegram_html(part),
                    parse_mode="HTML",
                )
            except Exception:
                if queued:
                    queue.finish_outbound(queued["id"], delivered=False, retry_after_s=5)
                raise
            if queued:
                queue.finish_outbound(queued["id"], delivered=True)
        for relative in reply.artifacts:
            run_id = str(reply.metadata.get("run_id") or "")
            path = artifact_path(run_id, relative)
            if path is None:
                continue
            method = "sendDocument"
            if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                method = "sendPhoto"
            elif path.suffix.lower() in {".mp4", ".webm", ".mov"}:
                method = "sendVideo"
            with path.open("rb") as handle:
                await self.client.post(
                    f"{self.base_url}/{method}",
                    data=common,
                    files={method.removeprefix("send").lower(): (path.name, handle)},
                )

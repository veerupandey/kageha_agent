"""Experimental WhatsApp Web QR adapter backed by the Node sidecar."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from kageha.channels.models import ChannelMedia, ChannelMessage
from kageha.channels.runtime import ChannelRuntime
from kageha.config import kageha_home


def whatsapp_qr_dependencies_ready() -> bool:
    root = Path(__file__).resolve().parents[3] / "integrations" / "whatsapp-qr"
    return (
        (root / "package.json").is_file()
        and (root / "node_modules" / "@whiskeysockets" / "baileys").is_dir()
        and (root / "node_modules" / "qrcode-terminal").is_dir()
    )


class WhatsAppQrAdapter:
    """Bridge JSON-lines events from ``integrations/whatsapp-qr`` to Kageha."""

    channel = "whatsapp-qr"

    def __init__(self, runtime: ChannelRuntime, *, node: str = "node") -> None:
        self.runtime = runtime
        self.node = node
        self.process: asyncio.subprocess.Process | None = None
        self.allowed_users = {
            value.strip()
            for value in os.environ.get("WHATSAPP_QR_ALLOWED_USERS", "").split(",")
            if value.strip()
        }
        self.allow_all = os.environ.get("WHATSAPP_QR_ALLOW_ALL_USERS", "").lower() in {
            "1", "true", "yes", "on"
        }
        self.auth_dir = Path(
            os.path.expanduser(
                os.environ.get(
                    "KAGEHA_WA_AUTH_DIR",
                    str(kageha_home() / "platforms" / "whatsapp" / "session"),
                )
            )
        )
        self.sidecar = Path(__file__).resolve().parents[3] / "integrations" / "whatsapp-qr" / "index.mjs"

    async def run(self) -> None:
        if shutil.which(self.node) is None:
            raise RuntimeError("node is required for the WhatsApp QR adapter")
        if not whatsapp_qr_dependencies_ready():
            raise RuntimeError(
                "WhatsApp QR dependencies are not installed. Run: "
                "cd integrations/whatsapp-qr && npm install"
            )
        self.auth_dir.mkdir(parents=True, exist_ok=True)
        self.process = await asyncio.create_subprocess_exec(
            self.node,
            str(self.sidecar),
            "--auth-dir",
            str(self.auth_dir),
            "--inbound-dir",
            str(kageha_home() / "platforms" / "whatsapp" / "inbound"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,
            # Keep the JSON-lines bridge tolerant of large upstream events/logs.
            limit=4 * 1024 * 1024,
        )
        assert self.process.stdout is not None
        async for raw in self.process.stdout:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await self.handle_event(event)

    async def close(self) -> None:
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()

    async def handle_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "qr":
            print("Scan this WhatsApp QR code from WhatsApp > Linked devices:", file=sys.stderr, flush=True)
            return
        if event_type == "ready":
            print("WhatsApp is connected. Send a message to the linked account.", file=sys.stderr, flush=True)
            return
        if event_type == "error":
            print(f"WhatsApp bridge error: {event.get('error', 'unknown error')}", file=sys.stderr, flush=True)
            return
        if event_type == "message_received":
            print(
                f"WhatsApp message received from {event.get('from', 'unknown')}; checking access.",
                file=sys.stderr,
                flush=True,
            )
            return
        if event_type != "message":
            return
        peer_id = str(event.get("from") or "")
        if not peer_id or not self.allow_all and peer_id not in self.allowed_users:
            return
        media = tuple(
            ChannelMedia(
                kind=str(item.get("kind") or "file"),
                filename=str(item.get("filename") or ""),
                content_type=str(item.get("content_type") or "application/octet-stream"),
                external_id=str(item.get("external_id") or ""),
                local_path=str(item.get("local_path") or ""),
                caption=str(item.get("caption") or ""),
                size_bytes=int(item.get("size_bytes") or 0),
            )
            for item in event.get("media") or []
        )
        message = ChannelMessage(
            channel=self.channel,
            account_id="default",
            peer_id=peer_id,
            message_id=str(event.get("id") or ""),
            text=str(event.get("text") or ""),
            media=media,
            metadata={"push_name": str(event.get("push_name") or "")},
        )
        reply = await self.runtime.process(message)
        if reply is None or self.process is None or self.process.stdin is None:
            return
        payload = {
            "type": "send",
            "to": peer_id,
            "text": reply.text,
            "run_id": str(reply.metadata.get("run_id") or ""),
            "artifacts": list(reply.artifacts),
        }
        self.process.stdin.write((json.dumps(payload) + "\n").encode())
        await self.process.stdin.drain()

"""WhatsApp Cloud API channel — Hermes-style webhook gateway + allowlist + chat HITL."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
)

log = logging.getLogger(__name__)

_PHONE_RE = re.compile(r"[^\d]")


def normalize_phone(value: str) -> str:
    """E.164-ish digits only (no leading +)."""
    return _PHONE_RE.sub("", (value or "").strip())


def parse_allowlist(raw: str | None = None) -> set[str] | None:
    """Return allowed phones, or None meaning allow-all.

    Env: WHATSAPP_ALLOWED_USERS=15551234567,15559876543
         WHATSAPP_ALLOWED_USERS=*
         WHATSAPP_ALLOW_ALL_USERS=true
    """
    if os.environ.get("WHATSAPP_ALLOW_ALL_USERS", "").strip().lower() in {"1", "true", "yes"}:
        return None
    raw = raw if raw is not None else os.environ.get("WHATSAPP_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()  # fail-closed: empty allowlist
    if raw == "*":
        return None
    return {normalize_phone(p) for p in raw.split(",") if p.strip()}


def verify_meta_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
    """Validate X-Hub-Signature-256 from Meta."""
    if not app_secret:
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = signature_header.split("=", 1)[1].strip()
    digest = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, expected)


def extract_inbound_texts(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Parse Cloud API webhook JSON → list of (from_phone, text, message_id)."""
    out: list[tuple[str, str, str]] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for msg in value.get("messages") or []:
                if (msg.get("type") or "") != "text":
                    continue
                text = ((msg.get("text") or {}).get("body") or "").strip()
                from_num = normalize_phone(str(msg.get("from") or ""))
                mid = str(msg.get("id") or "")
                if text and from_num:
                    out.append((from_num, text, mid))
    return out


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


@dataclass
class WhatsAppChannel:
    """Meta WhatsApp Business Cloud API adapter."""

    name: str = "whatsapp"
    token: str = ""
    phone_number_id: str = ""
    app_secret: str = ""
    verify_token: str = ""
    api_version: str = "v19.0"
    allowed_users: set[str] | None = field(default=None)
    approval_timeout_s: float = 600.0

    def __post_init__(self) -> None:
        self.token = self.token or os.environ.get("WHATSAPP_TOKEN", "")
        self.phone_number_id = self.phone_number_id or os.environ.get(
            "WHATSAPP_PHONE_NUMBER_ID", ""
        )
        self.app_secret = self.app_secret or os.environ.get("WHATSAPP_APP_SECRET", "")
        self.verify_token = self.verify_token or os.environ.get(
            "WHATSAPP_VERIFY_TOKEN", "kageha-whatsapp"
        )
        self.api_version = os.environ.get("WHATSAPP_API_VERSION", self.api_version)
        if self.allowed_users is None:
            # Env allowlist; empty env → fail-closed empty set (see parse_allowlist).
            self.allowed_users = parse_allowlist()
        self._pending_human: dict[str, _PendingHuman] = {}
        self._busy: set[str] = set()
        self._seen_ids: set[str] = set()
        self._durable_queue: Any = None

    @property
    def api(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}"

    @property
    def available(self) -> bool:
        return bool(self.token and self.phone_number_id)

    def is_allowed(self, phone: str) -> bool:
        phone = normalize_phone(phone)
        if self.allowed_users is None:
            return True
        return phone in self.allowed_users

    def make_approver(self, from_number: str) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        """Chat-native HITL: ask in WhatsApp and wait for y/n (or A/B)."""

        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f"🔐 Kageha needs approval\n"
                f"action: {req.action}\n"
                f"risk: {req.risk_class}\n"
                f"{req.detail[:1500]}\n\n"
                "Reply with: y / n  (or A / B)"
            )
            answer = await self.wait_for_human(from_number, prompt)
            if answer is None:
                return False
            a = answer.strip().lower()
            if a in {"y", "yes", "a", "approve", "ok"}:
                return True
            if a in {"n", "no", "b", "deny", "denied"}:
                return False
            # Ambiguous → deny (fail-closed)
            await self.send_raw(
                from_number,
                f"Didn't understand '{answer[:40]}' — treating as deny. Use y or n.",
            )
            return False

        return approver

    async def send_raw(self, to: str, text: str) -> dict[str, Any]:
        """Send text with no HITL (used for bot replies / approval prompts)."""
        if not self.available:
            return {"ok": False, "error": "WhatsApp not configured"}
        headers = {"Authorization": f"Bearer {self.token}"}
        payload = {
            "messaging_product": "whatsapp",
            "to": normalize_phone(to),
            "type": "text",
            "text": {"body": (text or "")[:4000]},
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.api}/messages", headers=headers, json=payload)
            try:
                data = resp.json()
            except Exception:  # noqa: BLE001
                data = {"text": resp.text}
            if resp.is_error:
                return {"ok": False, "status": resp.status_code, "error": data}
            return {"ok": True, **data}

    async def send(self, to: str, text: str, *, gate: ApprovalGate) -> dict[str, Any]:
        ok = await gate.require(
            ApprovalRequest(
                action="whatsapp_send",
                detail=f"to={to}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(to, text)

    async def wait_for_human(self, from_number: str, prompt: str) -> str | None:
        """Send prompt and wait for the next inbound text from that number."""
        from_number = normalize_phone(from_number)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[from_number] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(from_number, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("whatsapp HITL timeout for %s", from_number)
            return None
        finally:
            cur = self._pending_human.get(from_number)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(from_number, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, from_number: str, text: str) -> bool:
        """If this sender has an open HITL wait, resolve it and return True."""
        from_number = normalize_phone(from_number)
        pending = self._pending_human.get(from_number)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def handle_inbound(
        self,
        from_number: str,
        text: str,
        *,
        auto_approve_tasks: bool = False,
        message_id: str = "",
    ) -> dict[str, Any]:
        from_number = normalize_phone(from_number)
        if message_id:
            if self._durable_queue is None:
                from kageha.runtime.channels import DurableChannelQueue

                self._durable_queue = DurableChannelQueue(self.name)
            receipt = self._durable_queue.register_inbound(
                identity=from_number,
                external_id=message_id,
                text=text,
            )
            if not receipt.accepted:
                return {"ok": True, "deduped": True}
            if message_id in self._seen_ids:
                return {"ok": True, "deduped": True}
            self._seen_ids.add(message_id)
            if len(self._seen_ids) > 5000:
                # bound memory
                self._seen_ids = set(list(self._seen_ids)[-2000:])

        if self.consume_if_pending_human(from_number, text):
            return {"ok": True, "hitl": True}

        if not self.is_allowed(from_number):
            log.info("whatsapp reject (not allowlisted): %s", from_number)
            return {"ok": False, "error": "not_allowlisted"}

        if from_number in self._busy:
            await self.send_raw(
                from_number,
                "Still working on your previous message — please wait.",
            )
            return {"ok": True, "busy": True}

        from kageha.channels.session_memory import ChannelSessionStore, run_channel_agent_turn
        from kageha.loop.artifacts import format_artifacts_compact

        self._busy.add(from_number)
        try:
            from kageha.channels.progress import DelayedProgress

            store = ChannelSessionStore("whatsapp")
            approver = None if auto_approve_tasks else self.make_approver(from_number)
            async with DelayedProgress(
                lambda: self.send_raw(from_number, "⏳ Working on it…")
            ):
                turn = await run_channel_agent_turn(
                    phone=from_number,
                    text=text,
                    store=store,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                )
            if not turn.ok:
                log.error("whatsapp turn failed for %s: %s", from_number, turn.error)
                await self.send_raw(
                    from_number,
                    "I couldn't complete that task. Ask me to retry, or check "
                    "`kageha doctor --deep` from the CLI.",
                )
                return {"ok": False, "error": turn.error, "route": turn.route}

            reply = (turn.reply or "(no reply)").strip()
            if turn.artifacts and turn.run_id:
                arts = format_artifacts_compact(
                    run_id=turn.run_id, artifacts=turn.artifacts
                )
                reply = f"{reply}\n\n{arts}"
            if len(reply) > 3500:
                reply = reply[:3400] + "\n…"
            send_result = await self.send_raw(from_number, reply)
            return {
                "ok": True,
                "run_id": turn.run_id,
                "status": turn.status,
                "route": turn.route,
                "send": send_result,
            }
        except Exception as e:  # noqa: BLE001
            log.exception("whatsapp handle_inbound failed")
            await self.send_raw(
                from_number,
                "I hit an internal error while handling that message. Please retry.",
            )
            return {"ok": False, "error": str(e)}
        finally:
            self._busy.discard(from_number)

    async def process_webhook_payload(self, payload: dict[str, Any], *, auto_approve_tasks: bool = False) -> dict:
        texts = extract_inbound_texts(payload)
        results = []
        for from_num, text, mid in texts:
            # Run sequentially per webhook batch; per-sender busy handles overlap.
            results.append(
                await self.handle_inbound(
                    from_num,
                    text,
                    auto_approve_tasks=auto_approve_tasks,
                    message_id=mid,
                )
            )
        return {"processed": len(results), "results": results}

    async def serve(
        self,
        *,
        host: str = "0.0.0.0",
        port: int = 8787,
        path: str = "/webhook/whatsapp",
        auto_approve_tasks: bool = False,
    ) -> None:
        """Run aiohttp webhook server (requires kageha[channels])."""
        if not self.available:
            raise RuntimeError("WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID required")
        if self.allowed_users is not None and len(self.allowed_users) == 0:
            raise RuntimeError(
                "WHATSAPP_ALLOWED_USERS is empty (fail-closed). "
                "Set comma-separated phones, or WHATSAPP_ALLOWED_USERS=*, "
                "or WHATSAPP_ALLOW_ALL_USERS=true"
            )
        try:
            from aiohttp import web
        except ImportError as e:
            raise RuntimeError("Install channel deps: uv sync --extra channels") from e

        async def verify(request: web.Request) -> web.Response:
            mode = request.rel_url.query.get("hub.mode", "")
            token = request.rel_url.query.get("hub.verify_token", "")
            challenge = request.rel_url.query.get("hub.challenge", "")
            if mode == "subscribe" and token == self.verify_token:
                return web.Response(text=challenge)
            return web.Response(status=403, text="forbidden")

        async def inbound(request: web.Request) -> web.Response:
            body = await request.read()
            if self.app_secret:
                sig = request.headers.get("X-Hub-Signature-256", "")
                if not verify_meta_signature(body, sig, self.app_secret):
                    log.warning("whatsapp bad signature")
                    return web.Response(status=403, text="bad signature")
            try:
                payload = json.loads(body.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                return web.Response(status=400, text="bad json")
            # Acknowledge fast; process in background so Meta doesn't retry.
            asyncio.create_task(
                self.process_webhook_payload(payload, auto_approve_tasks=auto_approve_tasks)
            )
            return web.Response(text="EVENT_RECEIVED")

        app = web.Application()
        app.router.add_get(path, verify)
        app.router.add_post(path, inbound)

        async def healthz(_request: Any):
            return web.Response(text="ok")

        app.router.add_get("/healthz", healthz)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        allow = "ALL" if self.allowed_users is None else ",".join(sorted(self.allowed_users))
        print(
            f"[kageha] WhatsApp Cloud API webhook on http://{host}:{port}{path}\n"
            f"  verify_token={self.verify_token!r}\n"
            f"  allowlist={allow}\n"
            f"  Expose HTTPS (cloudflared/ngrok) and set Meta Callback URL to that URL+path.",
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

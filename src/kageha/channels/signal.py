"""Signal channel via signal-cli JSON-RPC + SSE (OpenClaw-compatible daemon)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
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


def parse_signal_allowlist(raw: str | None = None) -> set[str] | None:
    """Return allowed E.164 / UUID senders, or None for allow-all.

    Fail-closed when unset. SIGNAL_ALLOW_ALL_USERS=1 or SIGNAL_ALLOWED_USERS=* opens.
    """
    if os.environ.get("SIGNAL_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("SIGNAL_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    out: set[str] = set()
    for part in raw.split(","):
        p = part.strip().replace("signal:", "")
        if p:
            out.add(p)
    return out


def _signal_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_SIGNAL_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _normalize_base_url(url: str) -> str:
    trimmed = (url or "").strip()
    if not trimmed:
        return "http://127.0.0.1:8080"
    if not trimmed.lower().startswith(("http://", "https://")):
        trimmed = f"http://{trimmed}"
    return trimmed.rstrip("/")


def extract_signal_inbound(envelope: dict[str, Any]) -> tuple[str, str, str] | None:
    """Parse signal-cli receive envelope → (identity, text, external_id) or None."""
    if envelope.get("syncMessage"):
        return None
    data = envelope.get("dataMessage") or (envelope.get("editMessage") or {}).get(
        "dataMessage"
    )
    if not isinstance(data, dict):
        return None
    text = (data.get("message") or "").strip()
    if not text:
        return None
    source = (
        envelope.get("sourceNumber")
        or envelope.get("source")
        or envelope.get("sourceUuid")
        or ""
    )
    identity = str(source).strip()
    if not identity:
        return None
    ts = data.get("timestamp") or envelope.get("timestamp") or ""
    external_id = str(ts) if ts else f"{identity}:{text[:32]}"
    return identity, text, external_id


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class SignalChannel:
    name = "signal"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        account: str | None = None,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.base_url = _normalize_base_url(
            base_url or os.environ.get("SIGNAL_HTTP_URL", "http://127.0.0.1:8080")
        )
        self.account = (
            account
            if account is not None
            else (os.environ.get("SIGNAL_ACCOUNT", "") or "").strip() or None
        )
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_signal_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._durable_queue: Any = None
        self._seen_ids: set[str] = set()

    @property
    def available(self) -> bool:
        return bool(self.base_url)

    async def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        import httpx

        body = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": str(uuid.uuid4()),
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.base_url}/api/v1/rpc", json=body)
            if resp.status_code == 201:
                return None
            resp.raise_for_status()
            if not resp.text:
                return None
            parsed = resp.json()
            if parsed.get("error"):
                err = parsed["error"]
                raise RuntimeError(
                    f"Signal RPC {err.get('code', '?')}: {err.get('message', err)}"
                )
            return parsed.get("result")

    async def send_raw(self, recipient: str, text: str) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "Signal not configured"}
        params: dict[str, Any] = {
            "message": (text or "")[:4000],
            "recipient": [recipient],
        }
        if self.account:
            params["account"] = self.account
        try:
            result = await self._rpc("send", params)
            return {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send(self, recipient: str, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="signal_send",
                detail=f"to={recipient}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(recipient, text)

    def make_approver(
        self, recipient: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        key = str(recipient)

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

    async def wait_for_human(self, recipient: str, prompt: str) -> str | None:
        key = str(recipient)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("signal HITL timeout for %s", key)
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, recipient: str, text: str) -> bool:
        key = str(recipient)
        pending = self._pending_human.get(key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    def _allowed(self, identity: str) -> bool:
        if self.allowed_users is None:
            return True
        if identity in self.allowed_users:
            return True
        # Allowlist may use +E.164 while signal-cli omits +, or vice versa.
        digits = identity.lstrip("+")
        return digits in self.allowed_users or f"+{digits}" in self.allowed_users

    async def _handle_receive_event(self, data: str) -> None:
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        envelope = payload.get("envelope") if isinstance(payload, dict) else None
        if not isinstance(envelope, dict):
            return
        parsed = extract_signal_inbound(envelope)
        if not parsed:
            return
        identity, text, external_id = parsed
        if self.account and identity.lstrip("+") == self.account.lstrip("+"):
            return
        if not self._allowed(identity):
            return
        if self._durable_queue is not None:
            receipt = self._durable_queue.register_inbound(
                identity=identity,
                external_id=external_id,
                text=text,
            )
            if not receipt.accepted:
                return
        if external_id in self._seen_ids:
            return
        self._seen_ids.add(external_id)
        if len(self._seen_ids) > 5000:
            self._seen_ids = set(list(self._seen_ids)[-2000:])
        if self.consume_if_pending_human(identity, text):
            return
        await self._inbound.put((identity, text))

    async def _pump_sse(self) -> None:
        import httpx

        url = f"{self.base_url}/api/v1/events"
        params = {"account": self.account} if self.account else None
        while True:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "GET",
                        url,
                        params=params,
                        headers={"Accept": "text/event-stream"},
                    ) as resp:
                        resp.raise_for_status()
                        event_name = ""
                        data_lines: list[str] = []
                        async for line in resp.aiter_lines():
                            if line is None:
                                continue
                            if line.startswith(":"):
                                continue
                            if line == "":
                                if event_name == "receive" and data_lines:
                                    await self._handle_receive_event(
                                        "\n".join(data_lines)
                                    )
                                event_name = ""
                                data_lines = []
                                continue
                            if line.startswith("event:"):
                                event_name = line[6:].lstrip()
                            elif line.startswith("data:"):
                                data_lines.append(line[5:].lstrip())
            except Exception as exc:  # noqa: BLE001
                log.warning("signal SSE error: %s", exc)
                await asyncio.sleep(2.0)

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        """Connect to signal-cli SSE and run the agent on each text message."""
        if not self.available:
            raise RuntimeError("SIGNAL_HTTP_URL missing")
        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("signal")
        self._durable_queue = DurableChannelQueue("signal")
        pump = asyncio.create_task(self._pump_sse(), name="signal-sse")
        try:
            while True:
                identity, text = await self._inbound.get()
                use_chat_hitl = _signal_hitl_enabled() and not auto_approve_tasks
                approver = (
                    self.make_approver(identity) if use_chat_hitl else cli_approver
                )
                turn = await run_channel_agent_turn(
                    phone=identity,
                    text=text,
                    store=sessions,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                    channel_note=(
                        "You are answering via Signal. Keep replies concise and "
                        "chat-friendly. Preserve this sender's session context."
                    ),
                )
                if not turn.ok:
                    log.error("signal turn failed for %s: %s", identity, turn.error)
                    await self.send_raw(
                        identity,
                        "I couldn't complete that task. Ask me to retry or run "
                        "`kageha doctor --deep` for diagnostics.",
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
                await self.send_raw(identity, reply)
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            self._durable_queue.close()
            self._durable_queue = None

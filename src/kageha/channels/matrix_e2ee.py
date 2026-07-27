"""Matrix E2EE channel via matrix-nio (optional ``matrix-nio[e2e]`` extra)."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kageha.config import kageha_home
from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    cli_approver,
)

log = logging.getLogger(__name__)


def parse_matrix_allowlist(raw: str | None = None) -> set[str] | None:
    from kageha.channels.matrix import parse_matrix_allowlist as _parse

    return _parse(raw)


def _matrix_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_MATRIX_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class MatrixE2EEChannel:
    """Encrypted Matrix rooms using matrix-nio + Olm/Megolm."""

    name = "matrix"

    def __init__(
        self,
        homeserver: str | None = None,
        *,
        access_token: str | None = None,
        user_id: str | None = None,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
        store_path: str | Path | None = None,
    ) -> None:
        self.homeserver = (homeserver or os.environ.get("MATRIX_HOMESERVER", "")).rstrip(
            "/"
        )
        self.access_token = access_token or os.environ.get("MATRIX_ACCESS_TOKEN", "")
        self.user_id = (user_id or os.environ.get("MATRIX_USER_ID", "")).strip()
        self.device_id = (os.environ.get("MATRIX_DEVICE_ID") or "").strip() or None
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_matrix_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self.store_path = Path(
            store_path
            or os.environ.get("MATRIX_E2EE_STORE")
            or (kageha_home() / "matrix-e2ee")
        )
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[tuple[str, str, str]] = asyncio.Queue()
        self._client: Any = None
        self._durable_queue: Any = None

    @property
    def available(self) -> bool:
        return bool(self.homeserver and self.access_token and self.user_id)

    async def send_raw(self, room_id: str, text: str) -> dict[str, Any]:
        if self._client is None:
            return {"ok": False, "error": "Matrix E2EE client not started"}
        try:
            from nio import RoomSendResponse  # type: ignore

            resp = await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": (text or "")[:4000]},
            )
            if isinstance(resp, RoomSendResponse):
                return {"ok": True, "event_id": resp.event_id}
            return {"ok": False, "error": str(resp)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send(self, room_id: str, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="matrix_send",
                detail=f"room={room_id}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(room_id, text)

    def make_approver(
        self, room_id: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        key = str(room_id)

        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f"🔐 Kageha needs approval\n"
                f"action: {req.action}\n"
                f"risk: {req.risk_class}\n"
                f"{req.detail[:1500]}\n\n"
                "Reply with: y / n"
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

    async def wait_for_human(self, room_id: str, prompt: str) -> str | None:
        key = str(room_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("matrix E2EE HITL timeout for %s", key)
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, room_id: str, text: str) -> bool:
        key = str(room_id)
        pending = self._pending_human.get(key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        if not self.available:
            raise RuntimeError(
                "MATRIX_HOMESERVER, MATRIX_ACCESS_TOKEN, and MATRIX_USER_ID required "
                "for E2EE mode"
            )
        if not self.device_id:
            raise RuntimeError(
                "MATRIX_DEVICE_ID is required for encrypted Matrix sessions"
            )
        try:
            from nio import (  # type: ignore
                AsyncClient,
                AsyncClientConfig,
                Event,
                MatrixRoom,
                RoomMessageText,
            )
        except ImportError as e:
            raise ImportError(
                "Matrix E2EE requires matrix-nio[e2e]. Install:\n"
                "  uv sync --extra matrix-e2ee\n"
                "Also need libolm (brew install libolm / apt install libolm-dev)."
            ) from e

        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        self.store_path.mkdir(parents=True, exist_ok=True)
        config = AsyncClientConfig(encryption_enabled=True, store_sync_tokens=True)
        client = AsyncClient(
            self.homeserver,
            self.user_id,
            device_id=self.device_id,
            store_path=str(self.store_path),
            config=config,
        )
        client.access_token = self.access_token
        client.user_id = self.user_id
        if self.device_id:
            client.device_id = self.device_id
        self._client = client

        # Load store / keys
        client.load_store()
        if client.should_upload_keys:
            await client.keys_upload()

        sessions = ChannelSessionStore("matrix")
        self._durable_queue = DurableChannelQueue("matrix")

        async def on_message(room: MatrixRoom, event: Event) -> None:
            if not isinstance(event, RoomMessageText):
                return
            if event.sender == self.user_id:
                return
            if self.allowed_users is not None and event.sender not in self.allowed_users:
                return
            text = (event.body or "").strip()
            if not text:
                return
            event_id = getattr(event, "event_id", "") or ""
            if self._durable_queue is not None and event_id:
                receipt = self._durable_queue.register_inbound(
                    identity=event.sender,
                    external_id=event_id,
                    text=text,
                )
                if not receipt.accepted:
                    return
            if self.consume_if_pending_human(room.room_id, text):
                return
            await self._inbound.put((room.room_id, event.sender, text))

        client.add_event_callback(on_message, RoomMessageText)

        async def _sync_loop() -> None:
            while True:
                try:
                    await client.sync_forever(timeout=30000, full_state=True)
                except Exception as exc:  # noqa: BLE001
                    log.warning("matrix E2EE sync error: %s", exc)
                    await asyncio.sleep(2.0)

        pump = asyncio.create_task(_sync_loop(), name="matrix-e2ee-sync")
        log.info("Matrix E2EE sync starting (store=%s)", self.store_path)
        try:
            while True:
                room_id, sender, text = await self._inbound.get()
                use_chat_hitl = _matrix_hitl_enabled() and not auto_approve_tasks
                approver = (
                    self.make_approver(room_id) if use_chat_hitl else cli_approver
                )
                turn = await run_channel_agent_turn(
                    phone=sender,
                    text=text,
                    store=sessions,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                    channel_note=(
                        "You are answering via encrypted Matrix. Keep replies concise."
                    ),
                )
                if not turn.ok:
                    log.error("matrix E2EE turn failed for %s: %s", sender, turn.error)
                    await self.send_raw(
                        room_id,
                        "I couldn't complete that task. Ask me to retry.",
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
                await self.send_raw(room_id, reply)
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            await client.close()
            self._durable_queue.close()
            self._durable_queue = None
            self._client = None

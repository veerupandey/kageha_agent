"""Telegram bot channel adapter with chat-native HITL (WhatsApp parity)."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    cli_approver,
)

log = logging.getLogger(__name__)


def parse_telegram_allowlist(raw: str | None = None) -> set[str] | None:
    """Return allowed chat ids, or None meaning allow-all.

    Fail-closed when unset (empty set). Use TELEGRAM_ALLOW_ALL_USERS=1 or
    TELEGRAM_ALLOWED_USERS=* to open.
    """
    if os.environ.get("TELEGRAM_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {p.strip() for p in raw.split(",") if p.strip()}


def _tg_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_TG_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


class TelegramChannel:
    name = "telegram"

    def __init__(
        self,
        token: str | None = None,
        *,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 300.0,
    ) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.api = f"https://api.telegram.org/bot{self.token}"
        self.allowed_users = (
            allowed_users
            if allowed_users is not None
            else parse_telegram_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self._pending_human: dict[str, _PendingHuman] = {}
        # (chat_key, text, voice_reply)
        self._inbound: asyncio.Queue[tuple[str, str, bool]] = asyncio.Queue()
        self._offset = 0
        self._seen_ids: set[str] = set()
        self._durable_queue: Any = None

    @property
    def available(self) -> bool:
        return bool(self.token)

    async def _api(self, method: str, **params: Any) -> dict:
        import httpx

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(f"{self.api}/{method}", json=params)
            resp.raise_for_status()
            return resp.json()

    async def send_raw(self, chat_id: str | int, text: str) -> dict[str, Any]:
        """Send text with no HITL (bot replies / approval prompts)."""
        if not self.available:
            return {"ok": False, "error": "Telegram not configured"}
        try:
            data = await self._api(
                "sendMessage", chat_id=chat_id, text=(text or "")[:4000]
            )
            return {"ok": True, **data}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send_voice(self, chat_id: str | int, wav_path: str) -> dict[str, Any]:
        """Upload a local WAV/OGG as a Telegram voice note."""
        import httpx

        if not self.available:
            return {"ok": False, "error": "Telegram not configured"}
        path = Path(wav_path)
        if not path.is_file():
            return {"ok": False, "error": f"missing audio {wav_path}"}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                with path.open("rb") as fh:
                    resp = await client.post(
                        f"{self.api}/sendVoice",
                        data={"chat_id": str(chat_id)},
                        files={"voice": (path.name, fh, "audio/wav")},
                    )
                resp.raise_for_status()
                return {"ok": True, **resp.json()}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def download_file(self, file_id: str, dest: Path) -> Path:
        """Download a Telegram file_id to dest."""
        import httpx

        meta = await self._api("getFile", file_id=file_id)
        file_path = ((meta.get("result") or {}).get("file_path")) or ""
        if not file_path:
            raise RuntimeError(f"getFile missing path: {meta}")
        url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest

    async def send(self, chat_id: str | int, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="telegram_send",
                detail=f"chat={chat_id}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(chat_id, text)

    def make_approver(
        self, chat_id: str | int
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        """Chat-native HITL: ask in Telegram and wait for y/n (or A/B)."""
        chat_key = str(chat_id)

        async def approver(req: ApprovalRequest) -> bool:
            prompt = (
                f"🔐 Kageha needs approval\n"
                f"action: {req.action}\n"
                f"risk: {req.risk_class}\n"
                f"{req.detail[:1500]}\n\n"
                "Reply with: y / n  (or A / B)"
            )
            answer = await self.wait_for_human(chat_key, prompt)
            if answer is None:
                return False
            a = answer.strip().lower()
            if a in {"y", "yes", "a", "approve", "ok"}:
                return True
            if a in {"n", "no", "b", "deny", "denied"}:
                return False
            await self.send_raw(
                chat_key,
                f"Didn't understand '{answer[:40]}' — treating as deny. Use y or n.",
            )
            return False

        return approver

    async def wait_for_human(self, chat_id: str | int, prompt: str) -> str | None:
        chat_key = str(chat_id)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[chat_key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(chat_key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("telegram HITL timeout for %s", chat_key)
            return None
        finally:
            cur = self._pending_human.get(chat_key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(chat_key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, chat_id: str | int, text: str) -> bool:
        chat_key = str(chat_id)
        pending = self._pending_human.get(chat_key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    async def _pump_updates(self) -> None:
        """Continuous getUpdates so HITL replies arrive while a turn runs."""
        while True:
            try:
                data = await self._api(
                    "getUpdates",
                    offset=self._offset,
                    timeout=30,
                    allowed_updates=["message"],
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("telegram getUpdates error: %s", exc)
                await asyncio.sleep(2.0)
                continue
            for upd in data.get("result") or []:
                self._offset = int(upd["update_id"]) + 1
                msg = upd.get("message") or {}
                text = (msg.get("text") or "").strip()
                voice_reply = False
                chat = (msg.get("chat") or {}).get("id")
                msg_id = str(msg.get("message_id") or "")
                if chat is None:
                    continue
                chat_key = str(chat)
                if self.allowed_users is not None and chat_key not in self.allowed_users:
                    continue

                # Voice / audio notes → STT → text turn.
                voice = msg.get("voice") or msg.get("audio")
                if not text and voice and voice.get("file_id"):
                    try:
                        from kageha.models.stt import transcribe_audio

                        suffix = ".ogg" if msg.get("voice") else ".audio"
                        tmp = Path(tempfile.mkstemp(prefix="kageha-tg-", suffix=suffix)[1])
                        await self.download_file(str(voice["file_id"]), tmp)
                        try:
                            text = (await transcribe_audio(tmp)).strip()
                        finally:
                            try:
                                tmp.unlink(missing_ok=True)
                            except Exception:  # noqa: BLE001
                                pass
                        voice_reply = True
                        if not text:
                            await self.send_raw(
                                chat_key, "I couldn't hear anything in that voice note."
                            )
                            continue
                    except Exception as exc:  # noqa: BLE001
                        log.warning("telegram voice STT failed: %s", exc)
                        await self.send_raw(
                            chat_key,
                            f"Voice note failed to transcribe: {exc}",
                        )
                        continue

                if not text:
                    continue
                if msg_id:
                    if self._durable_queue is not None:
                        receipt = self._durable_queue.register_inbound(
                            identity=chat_key,
                            external_id=msg_id,
                            text=text,
                        )
                        if not receipt.accepted:
                            continue
                    if msg_id in self._seen_ids:
                        continue
                    self._seen_ids.add(msg_id)
                    if len(self._seen_ids) > 5000:
                        self._seen_ids = set(list(self._seen_ids)[-2000:])
                if self.consume_if_pending_human(chat_key, text):
                    continue
                await self._inbound.put((chat_key, text, voice_reply))
            await asyncio.sleep(0.05)

    async def poll_and_run(self, *, auto_approve_tasks: bool = False, offset: int = 0) -> None:
        """Long-poll Telegram and run the agent on each text message."""
        if not self.available:
            raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
        self._offset = offset
        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("telegram")
        self._durable_queue = DurableChannelQueue("telegram")
        pump = asyncio.create_task(self._pump_updates(), name="telegram-pump")
        try:
            while True:
                chat_key, text, voice_reply = await self._inbound.get()
                use_chat_hitl = _tg_hitl_enabled() and not auto_approve_tasks
                approver = (
                    self.make_approver(chat_key) if use_chat_hitl else cli_approver
                )
                from kageha.channels.progress import DelayedProgress

                async with DelayedProgress(
                    lambda: self.send_raw(chat_key, "⏳ Working on it…")
                ):
                    turn = await run_channel_agent_turn(
                        phone=chat_key,
                        text=text,
                        store=sessions,
                        auto_approve=auto_approve_tasks,
                        approver=approver,
                        channel_note=(
                            "You are answering via Telegram. Keep replies concise and "
                            "chat-friendly. Preserve this sender's session context."
                        ),
                    )
                if not turn.ok:
                    log.error("telegram turn failed for %s: %s", chat_key, turn.error)
                    await self.send_raw(
                        chat_key,
                        "I couldn't complete that task. You can ask me to retry or "
                        "run /status from the CLI for diagnostics.",
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
                await self.send_raw(chat_key, reply)
                from kageha.chat.voice_io import synthesize_reply_wav, voice_reply_enabled

                want_voice = voice_reply or voice_reply_enabled()
                if want_voice and reply:
                    wav: Path | None = None
                    try:
                        wav = Path(
                            tempfile.mkstemp(prefix="kageha-tg-out-", suffix=".wav")[1]
                        )
                        await synthesize_reply_wav(reply, wav)
                        sent = await self.send_voice(chat_key, str(wav))
                        if not sent.get("ok"):
                            log.warning("telegram voice reply failed: %s", sent)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("telegram voice reply skipped: %s", exc)
                    finally:
                        if wav is not None:
                            try:
                                wav.unlink(missing_ok=True)
                            except Exception:  # noqa: BLE001
                                pass
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            self._durable_queue.close()
            self._durable_queue = None

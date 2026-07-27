"""WhatsApp Web (Baileys) channel — OpenClaw-style QR linked-device bridge."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha.channels.whatsapp import normalize_phone, parse_allowlist
from kageha.config import kageha_home, project_root
from kageha.harness.approvals import ApprovalRequest, reset_channel_asker, set_channel_asker

log = logging.getLogger(__name__)

# Instant replies — do not spin LoopController (was hanging WhatsApp on "hey").
def _quick_whatsapp_reply(text: str) -> str | None:
    from kageha.chat.quick import quick_chat_reply

    return quick_chat_reply(text, channel="whatsapp")


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def resolve_image_artifacts(
    run_id: str,
    artifacts: list[str] | None,
    *,
    limit: int = 4,
) -> list[Path]:
    """Absolute image paths from the active journal-backed session only."""
    from kageha.config import sessions_dir

    roots = [(sessions_dir() / run_id).resolve()]
    out: list[Path] = []
    seen: set[str] = set()
    for rel in artifacts or []:
        requested = Path(rel)
        candidates = [requested] if requested.is_absolute() else [
            root / requested for root in roots
        ]
        for candidate in candidates:
            try:
                path = candidate.resolve()
            except Exception:  # noqa: BLE001
                continue
            if not any(path.is_relative_to(root) for root in roots):
                continue
            if path.suffix.lower() not in _IMAGE_EXTS or not path.is_file():
                continue
            key = str(path)
            if key in seen:
                break
            seen.add(key)
            out.append(path)
            break
        if len(out) >= limit:
            break
    return out


def bridge_root() -> Path:
    return project_root() / "bridges" / "whatsapp-baileys"


def default_auth_dir() -> Path:
    d = kageha_home() / "platforms" / "whatsapp" / "session"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_bridge_installed(root: Path | None = None) -> Path:
    """npm install bridge deps if node_modules missing. Returns bridge dir."""
    root = root or bridge_root()
    if not (root / "bridge.mjs").is_file():
        raise FileNotFoundError(f"Baileys bridge missing at {root}")
    if not shutil.which("node"):
        raise RuntimeError("Node.js required for WhatsApp QR bridge (install Node 18+)")
    if not shutil.which("npm"):
        raise RuntimeError("npm required for WhatsApp QR bridge")
    nm = root / "node_modules" / "@whiskeysockets" / "baileys"
    if not nm.is_dir():
        print(f"[kageha] Installing WhatsApp bridge deps in {root} …", flush=True)
        subprocess.run(
            ["npm", "install", "--omit=dev"],
            cwd=str(root),
            check=True,
        )
    return root


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


@dataclass
class WhatsAppQRChannel:
    """OpenClaw-style WhatsApp Web channel via Baileys subprocess."""

    name: str = "whatsapp_qr"
    auth_dir: Path | None = None
    allowed_users: set[str] | None = None
    approval_timeout_s: float = 600.0
    auto_approve_tasks: bool = False
    allow_groups: bool = False

    _proc: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _pending_human: dict[str, _PendingHuman] = field(default_factory=dict, init=False, repr=False)
    _busy: set[str] = field(default_factory=set, init=False, repr=False)
    _busy_notified: set[str] = field(default_factory=set, init=False, repr=False)
    _seen_ids: set[str] = field(default_factory=set, init=False, repr=False)
    _recent_outbound: set[str] = field(default_factory=set, init=False, repr=False)
    _reply_chat: dict[str, str] = field(default_factory=dict, init=False, repr=False)
    _ready: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _me: str = field(default="", init=False, repr=False)
    _durable_queue: Any = field(default=None, init=False, repr=False)

    _BOT_PREFIXES = (
        "⏳",
        "⛔",
        "🔐",
        "Still working on your previous",
        "Kageha needs approval",
        "Didn't understand",
        "Error:",
    )

    def __post_init__(self) -> None:
        if self.auth_dir is None:
            env = os.environ.get("KAGEHA_WA_AUTH_DIR", "").strip()
            self.auth_dir = Path(env) if env else default_auth_dir()
        if self.allowed_users is None:
            self.allowed_users = parse_allowlist(
                os.environ.get("WHATSAPP_QR_ALLOWED_USERS")
                or os.environ.get("WHATSAPP_ALLOWED_USERS")
            )
        if os.environ.get("KAGEHA_WA_GROUPS", "").strip() in {"1", "true", "yes"}:
            self.allow_groups = True

    def is_allowed(self, phone: str) -> bool:
        phone = normalize_phone(phone)
        if self.allowed_users is None:
            return True
        return phone in self.allowed_users

    @staticmethod
    def _is_dm_chat(chat: str) -> bool:
        """True for 1:1 chats (@s.whatsapp.net / @lid). False for newsletters/groups/status."""
        c = (chat or "").strip().lower()
        if not c:
            # Unknown chat JID — treat as DM so allowlist still gates the sender.
            return True
        if c == "status@broadcast" or c.endswith("@newsletter") or c.endswith("@broadcast"):
            return False
        if c.endswith("@g.us"):
            return False
        return c.endswith("@s.whatsapp.net") or c.endswith("@lid")

    def _is_bot_echo(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if t in self._recent_outbound:
            return True
        return any(t.startswith(p) for p in self._BOT_PREFIXES)

    async def send_raw(
        self,
        to: str,
        text: str,
        *,
        chat: str | None = None,
    ) -> dict[str, Any]:
        body = text or ""
        self._recent_outbound.add(body.strip())
        if len(self._recent_outbound) > 200:
            # bound memory
            self._recent_outbound = set(list(self._recent_outbound)[-80:])
        phone = normalize_phone(to)
        # Reply into the same WhatsApp thread the user used (@lid vs @s.whatsapp.net).
        reply_chat = chat or self._reply_chat.get(phone) or ""
        cmd: dict[str, Any] = {"type": "send", "to": phone, "text": body}
        if reply_chat:
            cmd["chat"] = reply_chat
        await self._send_cmd(cmd)
        return {"ok": True, "to": phone, "chat": reply_chat or phone}

    async def send_image(
        self,
        to: str,
        path: str | Path,
        *,
        caption: str = "",
        chat: str | None = None,
    ) -> dict[str, Any]:
        """Send a local image file into the WhatsApp thread (shows inline)."""
        phone = normalize_phone(to)
        reply_chat = chat or self._reply_chat.get(phone) or ""
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            return {"ok": False, "error": f"missing image: {file_path}"}
        if caption:
            self._recent_outbound.add(caption.strip())
        self._recent_outbound.add(f"[image:{file_path.name}]")
        cmd: dict[str, Any] = {
            "type": "send_image",
            "to": phone,
            "path": str(file_path),
            "caption": (caption or "")[:1024],
        }
        if reply_chat:
            cmd["chat"] = reply_chat
        await self._send_cmd(cmd)
        print(f"[kageha] WA image → {phone}: {file_path.name}", flush=True)
        return {"ok": True, "to": phone, "chat": reply_chat or phone, "path": str(file_path)}

    def make_approver(self, from_number: str) -> Callable[[ApprovalRequest], Awaitable[bool]]:
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
            await self.send_raw(
                from_number,
                f"Didn't understand '{answer[:40]}' — treating as deny. Use y or n.",
            )
            return False

        return approver

    async def wait_for_human(self, from_number: str, prompt: str) -> str | None:
        """Wait for WhatsApp reply, Terminal y/n, or ANSWER.txt — whichever first."""
        import threading

        from kageha.harness.approvals import race_tty_and_file

        from_number = normalize_phone(from_number)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[from_number] = _PendingHuman(future=fut, prompt=prompt)
        ext_stop = threading.Event()
        try:
            await self.send_raw(from_number, prompt)

            async def from_tty_or_file() -> None:
                try:
                    ans = await asyncio.to_thread(
                        race_tty_and_file,
                        [
                            "[HITL] WhatsApp approval also waiting in chat.",
                            "Reply y/n in WhatsApp OR type here at >>>",
                            prompt[:800],
                        ],
                        timeout=self.approval_timeout_s,
                        external_stop=ext_stop,
                    )
                except Exception:  # noqa: BLE001
                    return
                if ans and not fut.done():
                    fut.set_result(ans.strip())

            tty_task = asyncio.create_task(from_tty_or_file())
            try:
                return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
            finally:
                ext_stop.set()  # release /dev/tty reader if WhatsApp won
                tty_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await tty_task
        except asyncio.TimeoutError:
            return None
        finally:
            ext_stop.set()
            cur = self._pending_human.get(from_number)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(from_number, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, from_number: str, text: str) -> bool:
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
        message_id: str = "",
        chat: str = "",
    ) -> dict[str, Any]:
        from_number = normalize_phone(from_number)
        if chat:
            self._reply_chat[from_number] = chat
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
                self._seen_ids = set(list(self._seen_ids)[-2000:])

        if self.consume_if_pending_human(from_number, text):
            return {"ok": True, "hitl": True}

        if not self._is_dm_chat(chat) and not self.allow_groups:
            log.info("whatsapp-qr ignore non-dm chat=%s from=%s", chat, from_number)
            print(
                f"[kageha] ignore non-dm chat={chat or '?'} from={from_number}",
                flush=True,
            )
            return {"ok": True, "ignored": "non_dm"}

        if not self.is_allowed(from_number):
            # Silent by default — strangers must not see any bot reply.
            log.info("whatsapp-qr reject (not allowlisted): %s", from_number)
            print(f"[kageha] reject not_allowlisted from={from_number} (silent)", flush=True)
            if os.environ.get("KAGEHA_WA_REJECT_REPLY", "").strip().lower() in {
                "1",
                "true",
                "yes",
            }:
                try:
                    await self.send_raw(
                        from_number,
                        f"⛔ Number {from_number} is not allowlisted.\n"
                        f"Run: kageha whatsapp-setup\n"
                        f"Or set WHATSAPP_ALLOWED_USERS in .env",
                        chat=chat or None,
                    )
                except Exception:  # noqa: BLE001
                    pass
            return {"ok": False, "error": "not_allowlisted"}

        if from_number in self._busy:
            # Do NOT spam WhatsApp — each busy reply was re-ingested as fromMe → loop.
            if from_number not in self._busy_notified:
                self._busy_notified.add(from_number)
                print(
                    f"[kageha] busy — ignoring extra message from={from_number} "
                    f"(not notifying again until idle)",
                    flush=True,
                )
            return {"ok": True, "busy": True}

        from kageha.channels.session_memory import (
            ChannelSessionStore,
            is_session_reset,
            run_channel_agent_turn,
        )
        from kageha.loop.artifacts import format_artifacts_compact

        # Safe conversational replies never need the model or agent loop, even
        # when a sender already has an active session.
        store = ChannelSessionStore("whatsapp")
        quick = _quick_whatsapp_reply(text)
        if quick and not is_session_reset(text):
            print(f"[kageha] quick WA reply to={from_number}: {quick[:60]!r}", flush=True)
            send_result = await self.send_raw(from_number, quick, chat=chat or None)
            return {"ok": True, "quick": True, "send": send_result}

        self._busy.add(from_number)
        asker_token = set_channel_asker(
            lambda q: self.wait_for_human(from_number, str(q))
        )
        try:
            from kageha.channels.progress import DelayedProgress

            # WhatsApp chat: auto-approve tools so simple DMs don't stall on HITL.
            # Set KAGEHA_WA_HITL=1 to require in-chat y/n for risky tools.
            wa_hitl = os.environ.get("KAGEHA_WA_HITL", "").strip() in {"1", "true", "yes"}
            auto = self.auto_approve_tasks or not wa_hitl
            approver = None if auto else self.make_approver(from_number)
            progress = DelayedProgress(
                lambda: self.send_raw(
                    from_number,
                    "⏳ Working on it…",
                    chat=chat or None,
                ),
                delay_s=(0.75 if not is_session_reset(text) else 3600.0),
            )
            async with progress:
                turn = await run_channel_agent_turn(
                    phone=from_number,
                    text=text,
                    store=store,
                    auto_approve=auto,
                    approver=approver,
                )
            if not turn.ok:
                log.error("whatsapp-qr turn failed for %s: %s", from_number, turn.error)
                await self.send_raw(
                    from_number,
                    "I couldn't complete that task. Ask me to retry, or check "
                    "`kageha doctor --deep` from the CLI.",
                    chat=chat or None,
                )
                return {"ok": False, "error": turn.error, "route": turn.route}

            print(
                f"[kageha] WA session route={turn.route} run={turn.run_id or '-'} "
                f"from={from_number}",
                flush=True,
            )
            images = (
                resolve_image_artifacts(turn.run_id, turn.artifacts)
                if turn.run_id
                else []
            )
            reply = (turn.reply or "(no reply)").strip()
            if images:
                if len(reply) > 900:
                    reply = reply[:850].rstrip() + "…"
            elif turn.artifacts and turn.run_id:
                arts = format_artifacts_compact(
                    run_id=turn.run_id, artifacts=turn.artifacts
                )
                reply = f"{reply}\n\n{arts}"
            if len(reply) > 3500:
                reply = reply[:3400] + "\n…"
            send_result = await self.send_raw(from_number, reply, chat=chat or None)
            media_sent: list[dict[str, Any]] = []
            for i, img in enumerate(images):
                media_sent.append(
                    await self.send_image(
                        from_number,
                        img,
                        caption=img.name if i > 0 else "",
                        chat=chat or None,
                    )
                )
            return {
                "ok": True,
                "run_id": turn.run_id,
                "status": turn.status,
                "route": turn.route,
                "send": send_result,
                "images": media_sent,
            }
        except Exception as e:  # noqa: BLE001
            log.exception("whatsapp-qr handle_inbound failed")
            await self.send_raw(
                from_number,
                "I hit an internal error while handling that message. Please retry.",
                chat=chat or None,
            )
            return {"ok": False, "error": str(e)}
        finally:
            reset_channel_asker(asker_token)
            self._busy.discard(from_number)
            self._busy_notified.discard(from_number)

    async def _send_cmd(self, cmd: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("bridge not running")
        line = json.dumps(cmd) + "\n"
        self._proc.stdin.write(line.encode("utf-8"))
        await self._proc.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                log.warning("bridge non-json: %s", line[:200])
                continue
            await self._on_event(evt)

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            # QR art + status — pass through to user's terminal
            try:
                sys_stderr = __import__("sys").stderr
                sys_stderr.buffer.write(line)
                sys_stderr.buffer.flush()
            except Exception:  # noqa: BLE001
                print(line.decode("utf-8", errors="replace"), end="", flush=True)

    async def _on_event(self, evt: dict[str, Any]) -> None:
        et = evt.get("type")
        if et == "ready":
            self._me = normalize_phone(str(evt.get("me") or ""))
            self._ready.set()
            print(f"[kageha] WhatsApp linked (me={self._me or '?'})", flush=True)
        elif et == "qr":
            print(
                "\n[kageha] QR ready — open WhatsApp → Linked devices → Link a device\n",
                flush=True,
            )
        elif et == "message":
            from_num = normalize_phone(str(evt.get("from") or ""))
            text = str(evt.get("text") or "")
            mid = str(evt.get("id") or "")
            # Self-chat / @lid sometimes yields empty from — map to allowlist number.
            if (not from_num or from_num == "self") and evt.get("self"):
                if self.allowed_users:
                    from_num = next(iter(sorted(self.allowed_users)))
                elif self._me:
                    from_num = self._me
            if from_num and text:
                if evt.get("self") and self._is_bot_echo(text):
                    print(f"[kageha] skip bot echo: {text[:60]!r}", flush=True)
                    return
                chat = str(evt.get("chat") or "")
                print(
                    f"[kageha] WA message from={from_num} self={evt.get('self')} "
                    f"chat={chat} text={text[:80]!r}",
                    flush=True,
                )
                # Fire-and-forget so bridge reader stays responsive for HITL
                asyncio.create_task(
                    self.handle_inbound(
                        from_num, text, message_id=mid, chat=chat
                    )
                )
        elif et == "error":
            log.error("bridge error: %s", evt.get("error"))
            print(f"[kageha-wa] error: {evt.get('error')}", flush=True)
        elif et == "closed":
            print(
                f"[kageha-wa] connection closed loggedOut={evt.get('loggedOut')}",
                flush=True,
            )
            if evt.get("loggedOut"):
                self._ready.clear()

    async def start_bridge(self) -> None:
        root = ensure_bridge_installed()
        if self.allowed_users is not None and len(self.allowed_users) == 0:
            raise RuntimeError(
                "Allowlist empty (fail-closed). Set WHATSAPP_ALLOWED_USERS / "
                "WHATSAPP_QR_ALLOWED_USERS to your phone digits, or * / "
                "WHATSAPP_ALLOW_ALL_USERS=true"
            )
        env = os.environ.copy()
        env["KAGEHA_WA_AUTH_DIR"] = str(self.auth_dir)
        if self.allow_groups:
            env["KAGEHA_WA_GROUPS"] = "1"
        self._proc = await asyncio.create_subprocess_exec(
            "node",
            str(root / "bridge.mjs"),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(root),
        )
        asyncio.create_task(self._read_stdout())
        asyncio.create_task(self._read_stderr())

    async def run_forever(self) -> None:
        """Start bridge and block until process exits."""
        await self.start_bridge()
        allow = "ALL" if self.allowed_users is None else ",".join(sorted(self.allowed_users))
        print(
            f"[kageha] WhatsApp QR bridge starting\n"
            f"  session: {self.auth_dir}\n"
            f"  allowlist: {allow}\n"
            f"  Scan QR if shown, then message this WhatsApp from an allowlisted number.\n"
            f"  Unofficial linked-device mode (ban risk) — prefer a dedicated number.\n",
            flush=True,
        )
        assert self._proc is not None
        code = await self._proc.wait()
        raise SystemExit(code or 0)

    async def stop(self, *, logout: bool = False) -> None:
        """Stop the bridge process. By default keeps the linked-device session."""
        if self._proc and self._proc.returncode is None:
            if logout:
                try:
                    await self._send_cmd({"type": "logout"})
                except Exception:  # noqa: BLE001
                    self._proc.terminate()
            else:
                self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()

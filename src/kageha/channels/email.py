"""Email channel — IMAP poll inbound + SMTP replies (OpenClaw has no first-class email)."""

from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import os
import re
import smtplib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage, Message
from email.utils import formataddr, parseaddr
from typing import Any

from kageha.harness.approvals import (
    ApprovalDecision,
    ApprovalGate,
    ApprovalRequest,
    cli_approver,
)

log = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)


def normalize_email(value: str) -> str:
    name, addr = parseaddr(value or "")
    addr = (addr or value or "").strip().lower()
    return addr


def parse_email_allowlist(raw: str | None = None) -> set[str] | None:
    """Return allowed From addresses, or None for allow-all.

    Fail-closed when unset. EMAIL_ALLOW_ALL_USERS=1 or EMAIL_ALLOWED_USERS=* opens.
    """
    if os.environ.get("EMAIL_ALLOW_ALL_USERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    raw = raw if raw is not None else os.environ.get("EMAIL_ALLOWED_USERS", "")
    raw = (raw or "").strip()
    if not raw:
        return set()
    if raw == "*":
        return None
    return {normalize_email(p) for p in raw.split(",") if p.strip()}


def _email_hitl_enabled() -> bool:
    raw = os.environ.get("KAGEHA_EMAIL_HITL", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:  # noqa: BLE001
        return raw


def extract_body_text(msg: Message) -> str:
    """Best-effort plain-text body from an email.message.Message."""
    def payload_bytes(part: Message) -> bytes:
        value = part.get_payload(decode=True)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode()
        return b""

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp.lower():
                payload = payload_bytes(part)
                charset = part.get_content_charset() or "utf-8"
                try:
                    return payload.decode(charset, errors="replace").strip()
                except Exception:  # noqa: BLE001
                    return payload.decode("utf-8", errors="replace").strip()
        return ""
    payload = payload_bytes(msg)
    charset = msg.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace").strip()
    except Exception:  # noqa: BLE001
        return payload.decode("utf-8", errors="replace").strip()


@dataclass
class _PendingHuman:
    future: asyncio.Future[str]
    prompt: str


@dataclass
class _InboundMail:
    identity: str
    text: str
    message_id: str
    subject: str
    reply_to: str


def _gmail_oauth_available() -> tuple[bool, str]:
    """Return (ok, account_email) if ``kageha connect login gmail`` succeeded."""
    try:
        from kageha.connections.providers.gmail import GmailProvider
        from kageha.connections.store import ConnectionStore

        st = GmailProvider().status(store=ConnectionStore())
        if st.connected and st.account:
            return True, st.account
        return bool(st.connected), st.account or ""
    except Exception:  # noqa: BLE001
        return False, ""


def _gmail_access_token() -> str:
    from kageha.connections.providers.gmail import GmailProvider
    from kageha.connections.store import ConnectionStore

    return GmailProvider().access_token(store=ConnectionStore())


class EmailChannel:
    name = "email"

    def __init__(
        self,
        *,
        allowed_users: set[str] | None = None,
        approval_timeout_s: float = 600.0,
        poll_interval_s: float | None = None,
    ) -> None:
        oauth_ok, oauth_account = _gmail_oauth_available()
        self.use_xoauth2 = bool(oauth_ok) and not os.environ.get(
            "EMAIL_IMAP_PASSWORD", ""
        )
        self.imap_host = os.environ.get("EMAIL_IMAP_HOST", "").strip()
        self.imap_port = int(os.environ.get("EMAIL_IMAP_PORT", "993") or "993")
        self.imap_user = os.environ.get("EMAIL_IMAP_USER", "").strip()
        self.imap_password = os.environ.get("EMAIL_IMAP_PASSWORD", "")
        self.smtp_host = os.environ.get("EMAIL_SMTP_HOST", "").strip()
        self.smtp_port = int(os.environ.get("EMAIL_SMTP_PORT", "587") or "587")
        self.smtp_user = (
            os.environ.get("EMAIL_SMTP_USER", "").strip() or self.imap_user
        )
        self.smtp_password = (
            os.environ.get("EMAIL_SMTP_PASSWORD", "") or self.imap_password
        )
        if self.use_xoauth2:
            # Gmail OAuth path: fill hosts/user from connection when unset.
            self.imap_host = self.imap_host or "imap.gmail.com"
            self.smtp_host = self.smtp_host or "smtp.gmail.com"
            self.imap_user = self.imap_user or oauth_account
            self.smtp_user = self.smtp_user or self.imap_user
        self.from_addr = (
            os.environ.get("EMAIL_FROM", "").strip() or self.smtp_user or self.imap_user
        )
        self.mailbox = os.environ.get("EMAIL_IMAP_MAILBOX", "INBOX").strip() or "INBOX"
        self.allowed_users = (
            allowed_users if allowed_users is not None else parse_email_allowlist()
        )
        self.approval_timeout_s = approval_timeout_s
        self.poll_interval_s = poll_interval_s or float(
            os.environ.get("EMAIL_POLL_INTERVAL_S", "30") or "30"
        )
        self._pending_human: dict[str, _PendingHuman] = {}
        self._inbound: asyncio.Queue[_InboundMail] = asyncio.Queue()
        self._durable_queue: Any = None
        self._reply_ctx: dict[str, dict[str, str]] = {}

    @property
    def available(self) -> bool:
        has_auth = bool(self.imap_password) or self.use_xoauth2
        return bool(
            self.imap_host
            and self.imap_user
            and has_auth
            and self.smtp_host
            and self.from_addr
        )

    def _imap_authenticate(self, mail: imaplib.IMAP4_SSL) -> None:
        if self.use_xoauth2:
            from kageha.connections.gmail_api import xoauth2_string

            token = _gmail_access_token()
            auth = xoauth2_string(self.imap_user, token)
            mail.authenticate("XOAUTH2", lambda _x: auth)  # type: ignore[arg-type]
            return
        mail.login(self.imap_user, self.imap_password)

    def _smtp_authenticate(self, smtp: smtplib.SMTP) -> None:
        if self.use_xoauth2:
            from kageha.connections.gmail_api import xoauth2_string

            token = _gmail_access_token()
            auth = xoauth2_string(self.smtp_user or self.imap_user, token)

            def _auth_xoauth2(_challenge: bytes | None = None) -> str:
                return auth

            # smtplib.auth base64-encodes the initial response (Gmail XOAUTH2).
            smtp.auth("XOAUTH2", _auth_xoauth2)  # type: ignore[arg-type]
            return
        if self.smtp_user and self.smtp_password:
            smtp.login(self.smtp_user, self.smtp_password)

    def _smtp_send(
        self,
        to_addr: str,
        subject: str,
        body: str,
        *,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> dict[str, Any]:
        msg = EmailMessage()
        msg["From"] = formataddr(("Kageha", self.from_addr))
        msg["To"] = to_addr
        msg["Subject"] = subject
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = references or in_reply_to
        msg.set_content(body[:100_000])
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=60) as smtp:
            smtp.ehlo()
            if os.environ.get("EMAIL_SMTP_STARTTLS", "1").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }:
                smtp.starttls()
                smtp.ehlo()
            self._smtp_authenticate(smtp)
            smtp.send_message(msg)
        return {"ok": True}

    async def send_raw(
        self,
        to_addr: str,
        text: str,
        *,
        subject: str | None = None,
        in_reply_to: str | None = None,
        references: str | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "Email not configured"}
        ctx = self._reply_ctx.get(normalize_email(to_addr), {})
        subj = subject or ctx.get("subject") or "Re: Kageha"
        if not subj.lower().startswith("re:"):
            subj = f"Re: {subj}"
        try:
            return await asyncio.to_thread(
                self._smtp_send,
                to_addr,
                subj,
                text or "",
                in_reply_to=in_reply_to or ctx.get("message_id"),
                references=references or ctx.get("message_id"),
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    async def send(self, to_addr: str, text: str, *, gate: ApprovalGate) -> dict:
        ok = await gate.require(
            ApprovalRequest(
                action="email_send",
                detail=f"to={to_addr}\n{text[:500]}",
                risk_class="messaging",
                default=ApprovalDecision.ASK,
            )
        )
        if not ok:
            return {"ok": False, "error": "DENIED"}
        return await self.send_raw(to_addr, text)

    def make_approver(
        self, to_addr: str
    ) -> Callable[[ApprovalRequest], Awaitable[bool]]:
        key = normalize_email(to_addr)

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

    async def wait_for_human(self, to_addr: str, prompt: str) -> str | None:
        key = normalize_email(to_addr)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        self._pending_human[key] = _PendingHuman(future=fut, prompt=prompt)
        try:
            await self.send_raw(key, prompt)
            return await asyncio.wait_for(fut, timeout=self.approval_timeout_s)
        except asyncio.TimeoutError:
            log.warning("email HITL timeout for %s", key)
            return None
        finally:
            cur = self._pending_human.get(key)
            if cur is not None and cur.future is fut:
                self._pending_human.pop(key, None)
            if not fut.done():
                fut.cancel()

    def consume_if_pending_human(self, from_addr: str, text: str) -> bool:
        key = normalize_email(from_addr)
        pending = self._pending_human.get(key)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(text)
        return True

    def _fetch_unseen(self) -> list[_InboundMail]:
        out: list[_InboundMail] = []
        mail = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        try:
            self._imap_authenticate(mail)
            mail.select(self.mailbox)
            typ, data = mail.search(None, "UNSEEN")
            if typ != "OK" or not data or not data[0]:
                return out
            for num in data[0].split():
                typ, msg_data = mail.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                first = msg_data[0]
                if not isinstance(first, tuple) or len(first) < 2:
                    continue
                raw = first[1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                msg = email.message_from_bytes(raw)
                from_addr = normalize_email(msg.get("From", ""))
                if not from_addr or not _ADDR_RE.search(from_addr):
                    continue
                if self.allowed_users is not None and from_addr not in self.allowed_users:
                    continue
                body = extract_body_text(msg)
                if not body:
                    continue
                mid = (msg.get("Message-ID") or "").strip() or f"imap-{num.decode()}"
                subject = _decode_header_value(msg.get("Subject"))
                reply_to = normalize_email(msg.get("Reply-To") or "") or from_addr
                out.append(
                    _InboundMail(
                        identity=from_addr,
                        text=body,
                        message_id=mid,
                        subject=subject,
                        reply_to=reply_to,
                    )
                )
        finally:
            try:
                mail.logout()
            except Exception:  # noqa: BLE001
                pass
        return out

    async def _pump_imap(self) -> None:
        while True:
            try:
                mails = await asyncio.to_thread(self._fetch_unseen)
                for m in mails:
                    if self._durable_queue is not None:
                        receipt = self._durable_queue.register_inbound(
                            identity=m.identity,
                            external_id=m.message_id,
                            text=m.text,
                        )
                        if not receipt.accepted:
                            continue
                    self._reply_ctx[m.identity] = {
                        "message_id": m.message_id,
                        "subject": m.subject,
                        "reply_to": m.reply_to,
                    }
                    if self.consume_if_pending_human(m.identity, m.text):
                        continue
                    await self._inbound.put(m)
            except Exception as exc:  # noqa: BLE001
                log.warning("email IMAP poll error: %s", exc)
            await asyncio.sleep(self.poll_interval_s)

    async def poll_and_run(self, *, auto_approve_tasks: bool = False) -> None:
        """Poll IMAP for unread mail and reply via SMTP."""
        if not self.available:
            raise RuntimeError(
                "Email not configured. Either set EMAIL_IMAP_HOST/USER/PASSWORD + "
                "EMAIL_SMTP_HOST (+ EMAIL_FROM), or run: kageha connect login gmail"
            )
        from kageha.channels.session_memory import (
            ChannelSessionStore,
            run_channel_agent_turn,
        )
        from kageha.runtime.channels import DurableChannelQueue

        sessions = ChannelSessionStore("email")
        self._durable_queue = DurableChannelQueue("email")
        pump = asyncio.create_task(self._pump_imap(), name="email-imap")
        try:
            while True:
                mail = await self._inbound.get()
                use_chat_hitl = _email_hitl_enabled() and not auto_approve_tasks
                approver = (
                    self.make_approver(mail.reply_to) if use_chat_hitl else cli_approver
                )
                turn = await run_channel_agent_turn(
                    phone=mail.identity,
                    text=mail.text,
                    store=sessions,
                    auto_approve=auto_approve_tasks,
                    approver=approver,
                    channel_note=(
                        "You are answering via email. Keep replies clear and "
                        "professional. Preserve this sender's session context."
                    ),
                )
                if not turn.ok:
                    log.error("email turn failed for %s: %s", mail.identity, turn.error)
                    await self.send_raw(
                        mail.reply_to,
                        "I couldn't complete that task. Ask me to retry or run "
                        "`kageha doctor --deep` for diagnostics.",
                        subject=mail.subject,
                        in_reply_to=mail.message_id,
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
                await self.send_raw(
                    mail.reply_to,
                    reply,
                    subject=mail.subject,
                    in_reply_to=mail.message_id,
                )
        finally:
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            self._durable_queue.close()
            self._durable_queue = None

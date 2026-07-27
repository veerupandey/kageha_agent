"""Allowlists + parsers for Signal / Matrix / Email / iMessage."""

from __future__ import annotations

import asyncio

import pytest

from kageha.channels.email import EmailChannel, normalize_email, parse_email_allowlist
from kageha.channels.imessage import (
    extract_bluebubbles_inbound,
    iMessageChannel,
    parse_imessage_allowlist,
)
from kageha.channels.matrix import MatrixChannel, parse_matrix_allowlist
from kageha.channels.signal import (
    SignalChannel,
    extract_signal_inbound,
    parse_signal_allowlist,
)
from kageha.harness.approvals import ApprovalDecision, ApprovalRequest


def test_signal_allowlist(monkeypatch):
    monkeypatch.delenv("SIGNAL_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("SIGNAL_ALLOWED_USERS", "")
    assert parse_signal_allowlist() == set()
    assert parse_signal_allowlist("*") is None
    assert parse_signal_allowlist("+15551234567,signal:+1999") == {
        "+15551234567",
        "+1999",
    }


def test_extract_signal_inbound():
    env = {
        "sourceNumber": "+15551234567",
        "timestamp": 123,
        "dataMessage": {"message": "hello", "timestamp": 456},
    }
    assert extract_signal_inbound(env) == ("+15551234567", "hello", "456")
    assert extract_signal_inbound({"syncMessage": {}}) is None


def test_matrix_allowlist(monkeypatch):
    monkeypatch.setenv("MATRIX_ALLOWED_USERS", "@a:example.org,@b:example.org")
    assert parse_matrix_allowlist() == {"@a:example.org", "@b:example.org"}


def test_email_allowlist_and_normalize(monkeypatch):
    monkeypatch.delenv("EMAIL_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("EMAIL_ALLOWED_USERS", "Alice <a@Ex.Com>,b@ex.com")
    assert parse_email_allowlist() == {"a@ex.com", "b@ex.com"}
    assert normalize_email("Bob <Bob@Ex.COM>") == "bob@ex.com"


def test_imessage_allowlist(monkeypatch):
    monkeypatch.delenv("IMESSAGE_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("BLUEBUBBLES_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("IMESSAGE_ALLOWED_USERS", "+1555,Me@Ex.Com")
    assert parse_imessage_allowlist() == {"+1555", "me@ex.com"}


def test_extract_bluebubbles_inbound():
    payload = {
        "type": "new-message",
        "data": {
            "text": "hi",
            "guid": "msg-1",
            "isFromMe": False,
            "handle": {"address": "+15551234567"},
            "chats": [{"guid": "iMessage;-;+15551234567"}],
        },
    }
    rows = extract_bluebubbles_inbound(payload)
    assert rows == [
        ("+15551234567", "hi", "msg-1", "iMessage;-;+15551234567")
    ]


@pytest.mark.asyncio
async def test_signal_approver_yes(monkeypatch):
    ch = SignalChannel(base_url="http://127.0.0.1:9", allowed_users=None)

    async def fake_send(recipient, text):
        return {"ok": True}

    monkeypatch.setattr(ch, "send_raw", fake_send)

    async def reply_soon():
        await asyncio.sleep(0.05)
        assert ch.consume_if_pending_human("+1", "y")

    task = asyncio.create_task(reply_soon())
    ok = await ch.make_approver("+1")(
        ApprovalRequest(
            action="bash",
            detail="echo hi",
            risk_class="shell",
            default=ApprovalDecision.ASK,
        )
    )
    await task
    assert ok is True


@pytest.mark.asyncio
async def test_matrix_approver_deny(monkeypatch):
    ch = MatrixChannel(
        homeserver="https://matrix.example",
        access_token="tok",
        allowed_users=None,
    )

    async def fake_send(room_id, text):
        return {"ok": True}

    monkeypatch.setattr(ch, "send_raw", fake_send)

    async def reply_soon():
        await asyncio.sleep(0.05)
        ch.consume_if_pending_human("!room:ex", "n")

    task = asyncio.create_task(reply_soon())
    ok = await ch.make_approver("!room:ex")(
        ApprovalRequest(
            action="bash",
            detail="rm",
            risk_class="shell",
            default=ApprovalDecision.ASK,
        )
    )
    await task
    assert ok is False


@pytest.mark.asyncio
async def test_imessage_webhook_accept(monkeypatch):
    ch = iMessageChannel(
        server_url="http://127.0.0.1:1234",
        password="secret",
        allowed_users={"+15551234567"},
    )
    n = await ch.handle_webhook_payload(
        {
            "type": "new-message",
            "data": {
                "text": "ping",
                "guid": "g1",
                "handle": {"address": "+15551234567"},
                "chats": [{"guid": "chat-1"}],
            },
        }
    )
    assert n == 1
    handle, text, chat = ch._inbound.get_nowait()
    assert handle == "+15551234567"
    assert text == "ping"
    assert chat == "chat-1"


def test_email_available_false_without_env(monkeypatch):
    for key in (
        "EMAIL_IMAP_HOST",
        "EMAIL_IMAP_USER",
        "EMAIL_IMAP_PASSWORD",
        "EMAIL_SMTP_HOST",
        "EMAIL_FROM",
    ):
        monkeypatch.delenv(key, raising=False)
    assert EmailChannel().available is False

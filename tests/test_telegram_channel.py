import asyncio

import pytest

from kageha.channels.telegram import TelegramChannel, parse_telegram_allowlist
from kageha.harness.approvals import ApprovalDecision, ApprovalRequest


def test_parse_allowlist_fail_closed(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "")
    assert parse_telegram_allowlist() == set()
    assert parse_telegram_allowlist("*") is None
    assert parse_telegram_allowlist("111,222") == {"111", "222"}


@pytest.mark.asyncio
async def test_approver_yes(monkeypatch):
    ch = TelegramChannel(token="fake", allowed_users=None)
    sent: list[str] = []

    async def fake_send(chat_id, text):
        sent.append(text)
        return {"ok": True}

    monkeypatch.setattr(ch, "send_raw", fake_send)

    async def reply_soon():
        await asyncio.sleep(0.05)
        assert ch.consume_if_pending_human("99", "y")

    task = asyncio.create_task(reply_soon())
    approver = ch.make_approver("99")
    ok = await approver(
        ApprovalRequest(
            action="bash",
            detail="echo hi",
            risk_class="shell",
            default=ApprovalDecision.ASK,
        )
    )
    await task
    assert ok is True
    assert any("approval" in s.lower() for s in sent)


@pytest.mark.asyncio
async def test_approver_deny(monkeypatch):
    ch = TelegramChannel(token="fake", allowed_users=None)

    async def fake_send(chat_id, text):
        return {"ok": True}

    monkeypatch.setattr(ch, "send_raw", fake_send)

    async def reply_soon():
        await asyncio.sleep(0.05)
        ch.consume_if_pending_human("7", "n")

    task = asyncio.create_task(reply_soon())
    ok = await ch.make_approver("7")(
        ApprovalRequest(
            action="bash",
            detail="rm -rf /",
            risk_class="shell",
            default=ApprovalDecision.ASK,
        )
    )
    await task
    assert ok is False


def test_consume_without_pending():
    ch = TelegramChannel(token="fake")
    assert ch.consume_if_pending_human("1", "y") is False

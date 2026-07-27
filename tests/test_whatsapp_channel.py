"""WhatsApp Cloud API channel unit tests (no live Meta calls)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json

import pytest

from kageha.channels.whatsapp import (
    WhatsAppChannel,
    extract_inbound_texts,
    normalize_phone,
    parse_allowlist,
    verify_meta_signature,
)
from kageha.harness.approvals import ApprovalDecision, ApprovalRequest


def test_normalize_phone():
    assert normalize_phone("+1 (555) 123-4567") == "15551234567"


def test_parse_allowlist(monkeypatch):
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    assert parse_allowlist("1555111, +1 555-222") == {"1555111", "1555222"}
    assert parse_allowlist("*") is None
    monkeypatch.setenv("WHATSAPP_ALLOW_ALL_USERS", "true")
    assert parse_allowlist("") is None


def test_verify_meta_signature():
    secret = "testsecret"
    body = b'{"object":"whatsapp_business_account"}'
    dig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, f"sha256={dig}", secret)
    assert not verify_meta_signature(body, "sha256=deadbeef", secret)
    assert not verify_meta_signature(body, "", secret)


def test_extract_inbound_texts():
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "15551234567",
                                    "id": "wamid.ABC",
                                    "type": "text",
                                    "text": {"body": "hello agent"},
                                },
                                {
                                    "from": "1555999",
                                    "id": "wamid.IMG",
                                    "type": "image",
                                },
                            ]
                        }
                    }
                ]
            }
        ]
    }
    msgs = extract_inbound_texts(payload)
    assert msgs == [("15551234567", "hello agent", "wamid.ABC")]


def test_is_allowed_fail_closed(monkeypatch):
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "")
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1")
    ch = WhatsAppChannel()
    assert ch.allowed_users == set()
    assert not ch.is_allowed("15551234567")


def test_is_allowed_star(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1")
    ch = WhatsAppChannel()
    assert ch.is_allowed("15551234567")


@pytest.mark.asyncio
async def test_hitl_consume(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "15551234567")
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1")
    ch = WhatsAppChannel(approval_timeout_s=2.0)

    sent: list[str] = []

    async def fake_send(to: str, text: str):
        sent.append(text)
        return {"ok": True}

    ch.send_raw = fake_send  # type: ignore[method-assign]

    async def ask():
        return await ch.wait_for_human("15551234567", "Approve?")

    task = asyncio.create_task(ask())
    await asyncio.sleep(0.05)
    assert ch.consume_if_pending_human("15551234567", "y")
    assert await task == "y"
    assert any("Approve" in s for s in sent)


@pytest.mark.asyncio
async def test_approver_yes(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1")
    ch = WhatsAppChannel(approval_timeout_s=2.0)

    async def fake_send(to: str, text: str):
        return {"ok": True}

    ch.send_raw = fake_send  # type: ignore[method-assign]
    approver = ch.make_approver("15551234567")

    async def answer_soon():
        await asyncio.sleep(0.05)
        ch.consume_if_pending_human("15551234567", "y")

    asyncio.create_task(answer_soon())
    ok = await approver(
        ApprovalRequest(
            action="browser_open",
            detail="url=https://example.com",
            risk_class="browser",
            default=ApprovalDecision.ASK,
        )
    )
    assert ok is True


@pytest.mark.asyncio
async def test_handle_inbound_not_allowlisted(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "19999999999")
    monkeypatch.setenv("WHATSAPP_TOKEN", "t")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "1")
    ch = WhatsAppChannel()
    result = await ch.handle_inbound("15551234567", "hi")
    assert result["ok"] is False
    assert result["error"] == "not_allowlisted"


def test_webhook_payload_json_roundtrip():
    raw = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "1",
                                        "id": "m1",
                                        "type": "text",
                                        "text": {"body": "ping"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    )
    assert extract_inbound_texts(json.loads(raw))[0][1] == "ping"

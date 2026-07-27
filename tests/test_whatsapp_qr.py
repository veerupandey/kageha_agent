"""WhatsApp QR (Baileys) channel unit tests — no live WhatsApp."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kageha.channels.whatsapp_qr import (
    WhatsAppQRChannel,
    _quick_whatsapp_reply,
    bridge_root,
    ensure_bridge_installed,
    resolve_image_artifacts,
)


def test_bridge_files_exist():
    root = bridge_root()
    assert (root / "bridge.mjs").is_file()
    assert (root / "package.json").is_file()


def test_quick_whatsapp_reply():
    assert _quick_whatsapp_reply("Hey")
    assert _quick_whatsapp_reply("hi!")
    assert _quick_whatsapp_reply("ping")
    assert _quick_whatsapp_reply("Who are you?").startswith("I'm Kageha")
    assert _quick_whatsapp_reply("create a reel") is None


@pytest.mark.asyncio
async def test_identity_reply_stays_instant_with_active_session(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    sessions = tmp_path / "channels" / "whatsapp" / "sessions.json"
    sessions.parent.mkdir(parents=True)
    sessions.write_text(
        '{"15551234567": {"run_id": "existing-session"}}\n',
        encoding="utf-8",
    )
    ch = WhatsAppQRChannel(auth_dir=tmp_path / "session")
    sent: list[str] = []

    async def fake_send(to: str, text: str, *, chat: str | None = None):
        sent.append(text)
        return {"ok": True}

    ch.send_raw = fake_send  # type: ignore[method-assign]
    result = await ch.handle_inbound(
        "15551234567",
        "Who are you?",
        chat="15551234567@s.whatsapp.net",
    )

    assert result["quick"] is True
    assert sent == [
        "I'm Kageha, your AI agent. I can research, use tools, create files, "
        "and continue work across this WhatsApp session."
    ]
    assert "Working" not in sent[0]


def test_resolve_image_artifacts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    run = tmp_path / "sessions" / "abc"
    img = run / "artifacts" / "out.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    (run / "artifacts" / "notes.md").write_text("x")
    got = resolve_image_artifacts("abc", ["artifacts/out.png", "artifacts/notes.md"])
    assert got == [img.resolve()]

    shot = tmp_path / "sessions" / "run-a" / "artifacts" / "shot.png"
    shot.parent.mkdir(parents=True)
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert resolve_image_artifacts(
        "run-a",
        ["artifacts/shot.png", str(outside)],
    ) == [shot.resolve()]
    assert resolve_image_artifacts("missing", ["artifacts/old.png"]) == []


@pytest.mark.asyncio
async def test_send_image_cmd(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    ch = WhatsAppQRChannel(auth_dir=tmp_path / "session")
    cmds: list[dict] = []

    async def fake_cmd(cmd: dict):
        cmds.append(cmd)

    ch._send_cmd = fake_cmd  # type: ignore[method-assign]
    ch._reply_chat["15551234567"] = "15551234567@lid"
    out = await ch.send_image("15551234567", img, caption="here")
    assert out["ok"] is True
    assert cmds[0]["type"] == "send_image"
    assert cmds[0]["path"] == str(img.resolve())
    assert cmds[0]["caption"] == "here"
    assert cmds[0]["chat"] == "15551234567@lid"


def test_allowlist(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "15551234567")
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("WHATSAPP_QR_ALLOWED_USERS", raising=False)
    ch = WhatsAppQRChannel()
    assert ch.is_allowed("15551234567")
    assert not ch.is_allowed("19999999999")


def test_is_dm_chat():
    assert WhatsAppQRChannel._is_dm_chat("15551234567@s.whatsapp.net")
    assert WhatsAppQRChannel._is_dm_chat("260631517229175@lid")
    assert not WhatsAppQRChannel._is_dm_chat("120363299015844582@newsletter")
    assert not WhatsAppQRChannel._is_dm_chat("status@broadcast")
    assert not WhatsAppQRChannel._is_dm_chat("120363@g.us")


@pytest.mark.asyncio
async def test_not_allowlisted_is_silent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "15551234567")
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("KAGEHA_WA_REJECT_REPLY", raising=False)
    ch = WhatsAppQRChannel(auth_dir=tmp_path / "session")
    sent: list[str] = []

    async def fake_send(to: str, text: str, *, chat: str | None = None):
        sent.append(text)
        return {"ok": True}

    ch.send_raw = fake_send  # type: ignore[method-assign]
    result = await ch.handle_inbound(
        "19999999999",
        "hi stranger",
        chat="19999999999@s.whatsapp.net",
    )
    assert result["error"] == "not_allowlisted"
    assert sent == []


@pytest.mark.asyncio
async def test_newsletter_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "15551234567")
    ch = WhatsAppQRChannel(auth_dir=tmp_path / "session")
    sent: list[str] = []

    async def fake_send(to: str, text: str, *, chat: str | None = None):
        sent.append(text)
        return {"ok": True}

    ch.send_raw = fake_send  # type: ignore[method-assign]
    result = await ch.handle_inbound(
        "120363299015844582",
        "https://bit.ly/spam",
        chat="120363299015844582@newsletter",
    )
    assert result.get("ignored") == "non_dm"
    assert sent == []


@pytest.mark.asyncio
async def test_ready_and_message_dispatch(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    ch = WhatsAppQRChannel(auth_dir=tmp_path / "session")
    handled: list[tuple[str, str]] = []

    async def fake_inbound(
        from_number: str, text: str, *, message_id: str = "", chat: str = ""
    ):
        handled.append((from_number, text))
        return {"ok": True}

    ch.handle_inbound = fake_inbound  # type: ignore[method-assign]

    await ch._on_event({"type": "ready", "me": "15550001111"})
    assert ch._ready.is_set()
    assert ch._me == "15550001111"

    await ch._on_event(
        {"type": "message", "from": "+1 555 123 4567", "text": "hello qr", "id": "m1"}
    )
    await asyncio.sleep(0.05)
    assert handled == [("15551234567", "hello qr")]


@pytest.mark.asyncio
async def test_hitl_consume(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_ALLOWED_USERS", "*")
    ch = WhatsAppQRChannel(auth_dir=tmp_path / "session", approval_timeout_s=2.0)
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


def test_ensure_bridge_installs():
    root = bridge_root()
    got = ensure_bridge_installed(root)
    assert got == root
    assert (root / "node_modules" / "@whiskeysockets" / "baileys").is_dir()

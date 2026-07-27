"""Deterministic where / status helpers."""

from __future__ import annotations

import json

from kageha.chat.quick import (
    answer_before_workspace,
    answer_status,
    answer_where,
    is_where_question,
    quick_chat_reply,
)
from kageha.harness.sandbox import SessionWorkspace


def test_quick_chat_reply_greetings():
    assert quick_chat_reply("Hey")
    assert quick_chat_reply("hi!")
    assert "pong" in (quick_chat_reply("ping") or "").lower()
    assert "welcome" in (quick_chat_reply("thanks") or "").lower()
    assert (quick_chat_reply("Who are you?") or "").startswith("I'm Kageha")
    assert "Plan" in (quick_chat_reply("what can you do?") or "")
    assert "WhatsApp" in (quick_chat_reply("ping", channel="whatsapp") or "")
    assert "ready" in (quick_chat_reply("how's it going?") or "").lower()
    assert "ready" in (quick_chat_reply("hi — just checking you're there") or "").lower()
    assert "ready" in (quick_chat_reply("you there?") or "").lower()
    assert quick_chat_reply("create a reel") is None
    assert quick_chat_reply("hey can you make a deck about Q3") is None


def test_where_detection():
    assert is_where_question("where did you save it?")
    assert is_where_question("where's the video?")
    assert is_where_question("show me the files")
    assert is_where_question("path")
    assert not is_where_question("make another slide about coaching")


def test_pre_workspace_commands_describe_state_precisely():
    assert "no task workspace" in answer_before_workspace("/where").lower()
    assert "No files yet" in answer_before_workspace("/files")
    assert "Chat is ready" in answer_before_workspace("/status")


def test_answer_status_from_goal_card(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("status-snap")
    ws.write_text(
        "goal_card.json",
        json.dumps(
            {
                "task": "Control Sony Bravia TV",
                "items": [
                    {"id": "g1", "description": "Pair TV", "passes": True},
                    {"id": "g2", "description": "Send pause", "passes": False},
                ],
            }
        ),
    )
    (ws.root / "artifacts").mkdir(exist_ok=True)
    (ws.root / "artifacts" / "note.md").write_text("hi", encoding="utf-8")
    text = answer_status(ws)
    assert "Progress: 1/2" in text
    assert "Pair TV" in text
    assert "note.md" in text or "Artifacts" in text


def test_answer_where(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("where1")
    (ws.root / "artifacts").mkdir(exist_ok=True)
    (ws.root / "artifacts" / "deck.pptx").write_bytes(b"x")
    text = answer_where(ws)
    assert "deck.pptx" in text
    assert str(ws.root) in text

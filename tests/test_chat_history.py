"""Session chat history loading for multi-turn continuity."""

from __future__ import annotations

from pathlib import Path

from kageha.chat.history import (
    drop_trailing_user,
    format_chat_block,
    load_chat_records,
    prior_history_messages,
    session_continuity_extra,
)
from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.resume_text import build_followup_prompt


def _ws(tmp_path: Path) -> SessionWorkspace:
    root = tmp_path / "session"
    root.mkdir()
    (root / "artifacts").mkdir()
    return SessionWorkspace(run_id="hist1", root=root)


def test_load_and_drop_trailing(tmp_path: Path):
    ws = _ws(tmp_path)
    path = ws.root / "chat.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"role":"user","text":"make a carousel"}',
                '{"role":"assistant","text":"Done with 5 slides"}',
                '{"role":"user","text":"make it bluer"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    rows = load_chat_records(ws)
    assert len(rows) == 3
    trimmed = drop_trailing_user(rows, "make it bluer")
    assert len(trimmed) == 2
    assert trimmed[-1]["role"] == "assistant"
    block = format_chat_block(trimmed)
    assert "carousel" in block
    assert "bluer" not in block


def test_prior_messages_and_extra(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws.root / "chat.jsonl").write_text(
        '{"role":"user","text":"Prefer cream backgrounds"}\n'
        '{"role":"assistant","text":"Got it — cream backgrounds."}\n'
        '{"role":"user","text":"now make slide 2"}\n',
        encoding="utf-8",
    )
    (ws.root / "artifacts" / "slide_01.jpg").write_bytes(b"x")
    msgs = prior_history_messages(ws, current_user="now make slide 2")
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    extra = session_continuity_extra(ws, current_user="now make slide 2")
    assert "cream" in extra.lower()
    assert "slide_01" in extra or "Session files" in extra


def test_followup_includes_recent_chat():
    text = build_followup_prompt(
        run_id="abc",
        message="make it bluer",
        original="create carousel",
        recent_chat="## Recent conversation\nUser: Prefer navy accents",
    )
    assert "navy" in text
    assert "bluer" in text
    assert "Honor preferences" in text


def test_load_chat_records_falls_back_to_turns(tmp_path: Path):
    ws = _ws(tmp_path)
    turns = ws.root / "_turns"
    turns.mkdir()
    (turns / "t1.json").write_text(
        '{"turn_id":"t1","request":"hey","answer":"Hello!","status":"success"}\n',
        encoding="utf-8",
    )
    (turns / "t2.json").write_text(
        '{"turn_id":"t2","request":"what next?","answer":"Ship it.","status":"success"}\n',
        encoding="utf-8",
    )
    rows = load_chat_records(ws)
    assert [(r["role"], r["text"]) for r in rows] == [
        ("user", "hey"),
        ("assistant", "Hello!"),
        ("user", "what next?"),
        ("assistant", "Ship it."),
    ]


def test_load_chat_records_prefers_richer_chat_jsonl(tmp_path: Path):
    ws = _ws(tmp_path)
    (ws.root / "chat.jsonl").write_text(
        '{"role":"user","text":"from chat"}\n'
        '{"role":"assistant","text":"chat reply"}\n',
        encoding="utf-8",
    )
    turns = ws.root / "_turns"
    turns.mkdir()
    (turns / "t1.json").write_text(
        '{"request":"only turn","answer":"turn reply"}\n',
        encoding="utf-8",
    )
    rows = load_chat_records(ws)
    assert rows[0]["text"] == "from chat"

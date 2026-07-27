"""Per-phone WhatsApp session continuity."""

from __future__ import annotations

from pathlib import Path

import pytest

from kageha.channels.session_memory import (
    ChannelSessionStore,
    is_session_reset,
    run_channel_agent_turn,
)
from kageha.chat.turn_manager import TurnDecision
from kageha.loop.controller import RunResult
from kageha.loop.goal_card import GoalCard
from kageha.runtime import RuntimeStore, RunEventKind, TurnRequest
from kageha.harness.sandbox import SessionWorkspace


def test_store_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    store = ChannelSessionStore("whatsapp")
    assert store.get("15551234567") is None
    store.set("+1 (555) 123-4567", "abc123session")
    assert store.get("15551234567") == "abc123session"
    store.clear("15551234567")
    assert store.get("15551234567") is None


def test_is_session_reset():
    assert is_session_reset("new session")
    assert is_session_reset("/new")
    assert is_session_reset("start over")
    assert not is_session_reset("create a new carousel")


def test_open_workspace_clears_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    store = ChannelSessionStore("whatsapp")
    store.set("15551234567", "missingrunid12")
    rid, ws = store.open_workspace("15551234567")
    assert rid is None and ws is None
    assert store.get("15551234567") is None


@pytest.mark.asyncio
async def test_reset_clears_mapping(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    store = ChannelSessionStore("whatsapp")
    store.set("15551234567", "unused-session")
    turn = await run_channel_agent_turn(
        phone="15551234567",
        text="new session",
        store=store,
        auto_approve=True,
    )
    assert turn.ok and turn.reset
    assert store.get("15551234567") is None


@pytest.mark.asyncio
async def test_second_turn_resumes_same_run(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    store = ChannelSessionStore("whatsapp")
    run_id = "run_session_1"
    runtime_store = RuntimeStore()
    accepted, _ = runtime_store.start_turn(
        TurnRequest(objective="Create an image of a dog"),
        session_id=run_id,
    )
    runtime_store.append_event(
        session_id=run_id,
        turn_id=accepted.turn_id,
        kind=RunEventKind.FAILED,
        payload={"status": "success"},
    )
    runtime_store.close()
    root = SessionWorkspace.create(run_id).root
    (root / "goal_card.json").write_text(
        '{"task":"Create an image of a dog","items":[]}',
        encoding="utf-8",
    )
    store.set("15550001111", run_id)

    async def fake_classify(message, ctx):
        return TurnDecision(
            intent="continue_task",
            reason="test continue",
            requires_tools=True,
            source="deterministic",
        )

    async def fake_resume(_runtime, rid, message, **kwargs):
        assert rid == run_id
        assert "bluer" in message
        return RunResult(
            run_id=rid,
            status="success",
            message="Made it bluer",
            goal=GoalCard.from_task("Create an image of a dog"),
            steps=2,
            spent_usd=0.0,
            artifacts=["artifacts/dog.png"],
        )

    monkeypatch.setattr(
        "kageha.chat.turn_manager.classify_turn",
        fake_classify,
    )
    monkeypatch.setattr(
        "kageha.runtime.AgentRuntime.execute_resume",
        fake_resume,
    )

    turn = await run_channel_agent_turn(
        phone="15550001111",
        text="make it bluer",
        store=store,
        auto_approve=True,
    )
    assert turn.ok
    assert turn.run_id == run_id
    assert turn.route == "resume"
    assert "bluer" in turn.reply.lower()
    assert store.get("15550001111") == run_id
    chat = (root / "chat.jsonl").read_text(encoding="utf-8")
    assert "make it bluer" in chat


@pytest.mark.asyncio
async def test_no_session_qa_goes_to_agent(tmp_path: Path, monkeypatch):
    """Self-depth: easy Q&A is an agent turn (model may use 0 tools)."""
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path / "khome"))
    store = ChannelSessionStore("whatsapp")
    seen: dict = {}

    async def fake_execute(self, request):
        del self
        seen["loop_mode"] = getattr(request, "loop_mode", None)
        seen["objective"] = request.objective
        return RunResult(
            run_id="run_qa_1",
            status="success",
            message="A subnet mask divides a network into subnets.",
            goal=GoalCard.from_task(request.objective),
            steps=1,
            spent_usd=0.0,
            artifacts=[],
        )

    monkeypatch.setattr(
        "kageha.runtime.AgentRuntime.execute",
        fake_execute,
    )

    turn = await run_channel_agent_turn(
        phone="15550002222",
        text="what is a subnet mask?",
        store=store,
        auto_approve=True,
    )
    assert turn.ok
    assert turn.route == "first_run"
    assert not turn.quick
    assert "subnet" in turn.reply.lower()
    assert seen.get("loop_mode") == "followup"

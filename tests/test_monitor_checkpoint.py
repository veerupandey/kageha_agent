"""Stage-gate monitor + mid-run checkpoint compaction."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.checkpoint import create_checkpoint, history_token_estimate
from kageha.loop.goal_card import GoalCard
from kageha.loop.monitor import MonitorVerdict, monitor_plan_alignment
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage


def _router_with_content(text: str):
    router = MagicMock()
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content=text),
        usage=ChatUsage(prompt_tokens=10, completion_tokens=20),
    )
    router.chat = AsyncMock(return_value=(MagicMock(model_id="flash"), resp))
    return router


def test_monitor_parses_drift_verdict():
    router = _router_with_content(
        '{"on_plan": false, "stage_complete": false, "current_stage": "p2",'
        ' "drift": "bash PDF loop", "redirect": "use web_search", "escalate": false}'
    )
    v = asyncio.run(
        monitor_plan_alignment(
            router=router,
            plan_summary="Research LLM coaching",
            plan_steps=["p1: search", "p2: synthesize"],
            goal_md="# Goal",
            todo_md="- [ ] p1",
            workspace_summary="todo.md",
            transcript_tail="assistant: bash cat pdf",
        )
    )
    assert v.on_plan is False
    assert "web_search" in v.redirect
    assert "DRIFT" in v.steering_message()


def test_monitor_fail_closed_on_bad_json():
    router = _router_with_content("not json at all")
    v = asyncio.run(
        monitor_plan_alignment(
            router=router,
            plan_summary="x",
            plan_steps=[],
            goal_md="",
            todo_md="",
            workspace_summary="",
            transcript_tail="",
        )
    )
    assert v.on_plan is True
    assert v.escalate is False


def test_checkpoint_compacts_history(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("ckpt")
    goal = GoalCard.from_task("research")
    history = [ChatMessage(role="user", content="Task: research coaching")]
    for i in range(20):
        history.append(ChatMessage(role="assistant", content=f"thinking {i}" * 40))
        history.append(
            ChatMessage(
                role="tool",
                name="bash",
                tool_call_id=f"t{i}",
                content=("stdout " * 80) + f" step{i}",
            )
        )
    before = history_token_estimate(history)
    assert before > 500

    router = _router_with_content(
        "## Facts\n- found paper A\n## Files\n- notes.md\n## Next\n- write brief"
    )
    result = asyncio.run(
        create_checkpoint(
            workspace=ws,
            step=6,
            history=history,
            goal=goal,
            plan_summary="Research LLM coaching",
            router=router,
            keep_recent=4,
            reason="context_window",
        )
    )
    assert result.history_tokens_after < result.history_tokens_before
    assert len(result.history) < len(history)
    assert any("[checkpoint" in (m.content or "") for m in result.history)
    assert (ws.root / "checkpoints" / "LATEST.md").is_file()
    assert "paper A" in (ws.root / "checkpoints" / "LATEST.md").read_text()


def test_monitor_verdict_steering_defaults():
    v = MonitorVerdict(on_plan=True, current_stage="p1", stage_complete=True)
    msg = v.steering_message()
    assert "ON PLAN" in msg
    assert "checkpoint" in msg.lower()

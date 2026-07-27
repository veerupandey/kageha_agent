"""TaskState persistence, projection, anti-loop, and adaptive control."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from kageha.loop.adaptive import apply_decision, decide_control, repair_steering_message
from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.loop.planner import PlanStep
from kageha.loop.stop_rules import StopReason, StopRules
from kageha.loop.task_state import (
    Assumption,
    ControlDecision,
    Defect,
    FailureKind,
    TaskState,
    ValidationSnapshot,
)
from kageha.loop.verifier import VerifyResult, build_workspace_evidence, verify_with_defects
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage


def test_task_state_roundtrip(tmp_path: Path):
    state = TaskState(objective="Build a deck")
    state.set_stages_from_plan(
        [
            PlanStep("s1", "Outline", ["write_file"]),
            PlanStep("s2", "Slides", ["bash"]),
        ]
    )
    state.add_fact("Need 8 slides", source="user", certainty="verified")
    state.assumptions.append(Assumption("Will use pptx"))
    state.record_tool(step=1, tool="write_file", content="wrote outline.md")
    state.record_tool(step=2, tool="bash", content="ERROR: timeout after 30s")
    state.apply_validation(
        ValidationSnapshot(
            status="repair",
            defects=[
                Defect(
                    artifact="deck.pptx",
                    severity="critical",
                    problem="Only 6 of 8 slides",
                    evidence="pptx_slides=6",
                    repair="Add limitations and implementation slides",
                )
            ],
            next_action="repair_artifact",
        )
    )
    path = tmp_path / "task_state.json"
    state.save(path)
    loaded = TaskState.load(path)
    assert loaded.objective == "Build a deck"
    assert len(loaded.stages) == 2
    assert loaded.stages[0].status == "active"
    assert loaded.stages[1].depends_on == ["s1"]
    assert loaded.failures[-1].kind == FailureKind.TIMEOUT.value
    assert loaded.validation.defects[0].artifact == "deck.pptx"
    proj = loaded.projection()
    assert "TaskState" in proj
    assert "Open defects" in proj
    assert "pptx_slides=6" in proj or "Only 6" in proj


def test_anti_loop_and_decide_repair():
    state = TaskState(objective="x")
    state.set_stages_from_plan([PlanStep("s1", "Do work")])
    state.goals = [{"id": "g1", "description": "done", "passes": False, "evidence": ""}]
    state.apply_validation(
        ValidationSnapshot(
            status="repair",
            defects=[
                Defect(
                    artifact="out.md",
                    severity="critical",
                    problem="missing",
                    repair="write out.md",
                )
            ],
            next_action="repair_artifact",
        )
    )
    d, reason = decide_control(state)
    assert d == ControlDecision.REPAIR
    assert "defect" in reason.lower() or "repair" in reason.lower()
    msg = repair_steering_message(state)
    assert "write out.md" in msg
    assert "MUST" in msg or "Do NOT claim done" in msg


def test_decide_stop_when_validated():
    state = TaskState(objective="x")
    state.goals = [{"id": "g1", "description": "done", "passes": True, "evidence": "file"}]
    state.validation = ValidationSnapshot(status="pass")
    d, _ = decide_control(state)
    assert d == ControlDecision.STOP_SUCCESS


def test_anti_loop_triggers_replan():
    state = TaskState(objective="x")
    state.set_stages_from_plan([PlanStep("s1", "search")])
    state.goals = [{"id": "g1", "description": "x", "passes": False, "evidence": ""}]
    for i in range(2):
        state.record_tool(step=i + 1, tool="bash", content="ERROR: timeout")
    assert state.anti_loop_hit("bash", FailureKind.TIMEOUT.value)
    d, reason = decide_control(state)
    assert d in {
        ControlDecision.SWITCH_TOOL,
        ControlDecision.REPLAN_STAGE,
        ControlDecision.REPLAN_TASK,
    }
    apply_decision(state, d, reason)
    assert state.control == d.value


def test_access_challenge_is_recorded_as_failure():
    state = TaskState(objective="Open a protected profile")
    state.record_tool(
        step=1,
        tool="browser_open",
        content="LinkedIn challenge page: verify you are human",
    )
    assert state.tool_results[-1].ok is False
    assert state.failures[-1].kind == FailureKind.ACCESS_BLOCKED.value


def test_discovered_artifacts_are_not_implicit_requirements():
    state = TaskState(objective="Find a profile")
    state.sync_artifacts(["artifacts/browse.png"])
    assert state.deliverables[0].role == "discovered"
    assert state.deliverables[0].required is False


def test_begin_turn_resets_execution_state_but_preserves_memory():
    state = TaskState(objective="old task")
    state.add_fact("User prefers concise answers", source="user", certainty="verified")
    state.record_tool(step=1, tool="browser_open", content="old profile result")
    state.sync_artifacts(["artifacts/old.png"])
    state.validation = ValidationSnapshot(status="pass")

    state.begin_turn(
        turn_id="turn-new",
        objective="open the browser",
        goals=[
            {
                "id": "g1",
                "description": "browser opened",
                "passes": False,
                "evidence": "",
            }
        ],
        plan_steps=[PlanStep("s1", "Open browser", ["browser_open"])],
        max_steps=8,
        max_usd=1.0,
    )

    assert state.turn_id == "turn-new"
    assert state.turn_tool_result_start == 1
    assert state.objective == "open the browser"
    assert state.validation.status == "unknown"
    assert state.deliverables == []
    assert state.goals[0]["passes"] is False
    assert state.facts[0].text == "User prefers concise answers"


def test_sync_artifacts_can_scope_deliverables_to_current_turn():
    state = TaskState(objective="new turn")
    state.sync_artifacts(
        ["artifacts/old.png", "artifacts/new.png"],
        current_paths={"artifacts/new.png"},
    )
    assert [item.path for item in state.deliverables] == ["artifacts/new.png"]


def test_stop_rules_ignore_model_said_done():
    rules = StopRules(max_steps=40, max_usd=2.0)
    goal = GoalCard(task="t", items=[GoalItem("g1", "deliverable", passes=False)])
    d = rules.evaluate(
        step=3,
        spent_usd=0.01,
        goal=goal,
        stagnant_steps=0,
        model_said_done=True,
        validated=False,
        validation_status="repair",
    )
    assert d.reason == StopReason.CONTINUE

    goal.mark("g1", passes=True, evidence="ok")
    d2 = rules.evaluate(
        step=3,
        spent_usd=0.01,
        goal=goal,
        stagnant_steps=0,
        model_said_done=True,
        validated=True,
        validation_status="pass",
    )
    assert d2.reason == StopReason.SUCCESS


def test_stop_rules_hard_limits_beat_unverified_done():
    rules = StopRules(max_steps=5, max_usd=2.0, no_progress_limit=3)
    goal = GoalCard(task="t", items=[GoalItem("g1", "deliverable", passes=False)])
    d = rules.evaluate(
        step=5,
        spent_usd=0.01,
        goal=goal,
        stagnant_steps=0,
        model_said_done=True,
        validated=False,
        validation_status="repair",
    )
    assert d.reason == StopReason.MAX_STEPS

    d2 = rules.evaluate(
        step=2,
        spent_usd=0.01,
        goal=goal,
        stagnant_steps=3,
        model_said_done=True,
        validated=False,
        validation_status="repair",
    )
    assert d2.reason == StopReason.NO_PROGRESS


def test_stop_rules_cap_stuck_repair_cycles():
    rules = StopRules(max_steps=40, max_same_repair=4, max_total_repair=8)
    goal = GoalCard(task="t", items=[GoalItem("g1", "deliverable", passes=False)])
    d = rules.evaluate(
        step=6,
        spent_usd=0.01,
        goal=goal,
        stagnant_steps=0,
        model_said_done=False,
        validated=False,
        validation_status="repair",
        same_repair_streak=4,
        total_repair_cycles=4,
    )
    assert d.reason == StopReason.ASK_USER
    assert "repair" in d.message.lower()

    d2 = rules.evaluate(
        step=10,
        spent_usd=0.01,
        goal=goal,
        stagnant_steps=0,
        validated=False,
        validation_status="repair",
        same_repair_streak=1,
        total_repair_cycles=8,
    )
    assert d2.reason == StopReason.ASK_USER


def test_verifier_emits_defects():
    router = MagicMock()
    payload = {
        "status": "repair",
        "updates": [{"id": "g1", "passes": False, "evidence": "pptx_slides=6"}],
        "defects": [
            {
                "artifact": "deck.pptx",
                "severity": "critical",
                "problem": "Only 6 of 8 requested slides exist",
                "evidence": "pptx_slides=6",
                "repair": "Create slides for limitations and implementation guidance",
            }
        ],
        "next_action": "repair_artifact",
        "notes": "incomplete deck",
    }
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content=json.dumps(payload)),
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
    )
    router.chat = AsyncMock(return_value=(MagicMock(model_id="fast"), resp))
    goal = GoalCard(task="deck", items=[GoalItem("g1", "8-slide deck")])
    result = asyncio.run(
        verify_with_defects(
            goal,
            router=router,
            workspace_summary="- deck.pptx | bytes=1000 | pptx_slides=6",
            transcript_tail="assistant: done",
            task_state_projection="Objective: deck",
        )
    )
    assert isinstance(result, VerifyResult)
    assert result.snapshot.status == "repair"
    assert result.snapshot.next_action == "repair_artifact"
    assert len(result.snapshot.defects) == 1
    assert "implementation" in result.snapshot.defects[0].repair


def test_build_workspace_evidence_skips_task_state(tmp_path: Path):
    (tmp_path / "task_state.json").write_text("{}")
    (tmp_path / "notes.md").write_text("hello evidence")
    ev = build_workspace_evidence(tmp_path)
    assert "notes.md" in ev
    assert "task_state.json" not in ev
    assert "hello evidence" in ev


def test_workspace_evidence_can_be_scoped_to_current_turn(tmp_path: Path):
    (tmp_path / "old.md").write_text("stale LinkedIn profile")
    (tmp_path / "new.md").write_text("browser opened at example.com")
    ev = build_workspace_evidence(tmp_path, include_paths={"new.md"})
    assert "new.md" in ev
    assert "example.com" in ev
    assert "old.md" not in ev
    assert "LinkedIn" not in ev

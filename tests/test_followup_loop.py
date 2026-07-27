"""Followup/act loop — skip planner + preserve goals."""

from __future__ import annotations

import json
from pathlib import Path

from kageha.harness.sandbox import SessionWorkspace
from kageha.loop.goal_card import GoalCard
from kageha.loop.planner import make_followup_plan


def test_make_followup_plan_is_one_step():
    plan = make_followup_plan("pause the TV")
    assert plan.source == "followup"
    assert len(plan.steps) == 1
    assert "pause" in plan.steps[0].description.lower()
    assert "chat" in plan.steps[0].description.lower()
    assert "chat" in plan.milestones[0].lower()


def test_followup_uses_fresh_goal_not_stale_passed(tmp_path: Path, monkeypatch):
    """Act-mode must not reuse a completed prior goal (that stops after 1 tool)."""
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("followup-fresh")
    prior = GoalCard.from_task(
        "Control Sony Bravia TV",
        milestones=["Pair the TV", "Confirm remote works"],
    )
    prior.items[0].passes = True
    prior.items[0].evidence = "paired"
    prior.items[1].passes = True
    prior.save(ws.path("goal_card.json"))
    assert prior.all_passed()

    turn_objective = "open comet and browse to https://kageha.ca"
    plan = make_followup_plan(turn_objective)
    # Mirror controller followup branch (fresh act goal).
    goal = GoalCard.from_task(turn_objective, milestones=plan.milestones)
    goal.save(ws.path("goal_card.json"))
    ws.write_text(
        "plan.json",
        json.dumps(
            {
                "summary": plan.summary,
                "source": plan.source,
                "steps": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "tools": s.tools,
                    }
                    for s in plan.steps
                ],
            },
            indent=2,
        ),
    )

    saved = GoalCard.load(ws.path("goal_card.json"))
    assert "kageha.ca" in saved.task
    assert not saved.all_passed()
    assert saved.items[0].passes is False
    pdata = json.loads(ws.path("plan.json").read_text())
    assert pdata["source"] == "followup"
    assert len(pdata["steps"]) == 1


def test_resumed_full_mode_resets_goal_for_different_ask(tmp_path: Path, monkeypatch):
    """Full-mode resume with a new ask must not keep a completed prior goal."""
    from kageha.loop.resume_text import unwrap_objective
    from kageha.loop.task_state import TaskState, ValidationSnapshot

    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("full-reset-ask")
    prior_task = (
        "Please do research for me on kageha.ca classic ceremonial "
        "and create a 6 slide carousel"
    )
    prior = GoalCard.from_task(
        prior_task,
        milestones=["Follow-up completed with evidence"],
    )
    prior.items[0].passes = True
    prior.items[0].evidence = "parallel_web_search, web_search, write_file"
    prior.save(ws.path("goal_card.json"))
    ws.write_text(
        "plan.json",
        json.dumps(
            {
                "summary": f"Follow-up: {prior_task[:80]}",
                "source": "followup",
                "steps": [
                    {
                        "id": "act",
                        "description": prior_task,
                        "tools": [],
                    }
                ],
            },
            indent=2,
        ),
    )
    state = TaskState(objective=prior_task)
    state.goals = [
        {
            "id": "g1",
            "description": "Follow-up completed with evidence",
            "passes": True,
            "evidence": "parallel_web_search",
        }
    ]
    state.validation = ValidationSnapshot(
        status="pass",
        notes="followup deterministic verify (no LLM)",
    )
    state.save(ws.path("task_state.json"))

    turn_objective = (
        "please use the original kageha classic ceremonial tin "
        "if you need to use it in your carousels"
    )
    # Mirror controller resumed-full different-ask branch.
    goal = GoalCard.load(ws.path("goal_card.json"))
    prior_task_n = unwrap_objective(goal.task or "", fallback=goal.task or "").strip()
    new_task_n = unwrap_objective(turn_objective, fallback=turn_objective).strip()
    assert prior_task_n != new_task_n
    assert goal.all_passed()

    plan = make_followup_plan(turn_objective)
    goal = GoalCard.from_task(turn_objective, milestones=plan.milestones)
    goal.save(ws.path("goal_card.json"))
    ws.write_text(
        "plan.json",
        json.dumps(
            {
                "summary": plan.summary,
                "source": plan.source,
                "steps": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "tools": s.tools,
                    }
                    for s in plan.steps
                ],
            },
            indent=2,
        ),
    )
    fresh_turn = True
    task_state = TaskState.load(ws.path("task_state.json"))
    task_state.begin_turn(
        turn_id="turn-new",
        objective=turn_objective,
        goals=[
            {
                "id": item.id,
                "description": item.description,
                "passes": item.passes,
                "evidence": item.evidence,
            }
            for item in goal.items
        ],
        plan_steps=plan.steps,
        max_steps=24,
        max_usd=2.0,
    )
    task_state.save(ws.path("task_state.json"))

    saved = GoalCard.load(ws.path("goal_card.json"))
    assert "ceremonial tin" in saved.task
    assert prior_task not in saved.task
    assert not saved.all_passed()
    reloaded = TaskState.load(ws.path("task_state.json"))
    assert reloaded.validation.status != "pass"
    assert not reloaded.goals_all_passed()
    assert fresh_turn is True
    pdata = json.loads(ws.path("plan.json").read_text())
    assert pdata["source"] == "followup"
    assert "ceremonial tin" in pdata["summary"]


def test_polish_typo_resumes_artifacts():
    from kageha.chat.turn_manager import (
        TurnContext,
        classify_deterministic,
        route_for_decision,
    )

    ctx = TurnContext(
        run_id="s1",
        objective="Create presentation about LLM coaches",
        artifacts=["artifacts/presentation/deck.pptx", "artifacts/presentation/slide_01.png"],
        recent_artifacts=["artifacts/presentation/deck.pptx"],
    )
    d = classify_deterministic("make itb polished", ctx)
    assert d.intent == "modify_artifact"
    assert d.discard_old_plan is False
    assert route_for_decision(d, has_session=True, message="make itb polished") == "resume"

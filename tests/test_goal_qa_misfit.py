"""Phase C1 — Goal UX soft-redirect for informational Q&A."""

from __future__ import annotations

from pathlib import Path

import pytest

from kageha.loop.mode_policy import (
    GOAL_QA_MISFIT_MESSAGE,
    MODE_CHIP_DESCRIPTIONS,
    goal_qa_misfit,
    is_informational_qa_prompt,
    requires_plan_approval,
)


@pytest.mark.parametrize(
    "prompt",
    [
        "What is HTTP 429?",
        "what's the status of the deploy?",
        "Who owns the billing service?",
        "List available TVs on the LAN",
        "summarize the error log",
        "how many pods are running?",
    ],
)
def test_informational_qa_classifier_positive(prompt: str):
    assert is_informational_qa_prompt(prompt)
    assert goal_qa_misfit("goal", prompt)
    assert not goal_qa_misfit("plan", prompt)
    assert not goal_qa_misfit("normal", prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Ship hello.txt with hi and verify it exists",
        "Create a file named hello.txt containing exactly: hi",
        "Implement the healthcheck endpoint and verify it returns 200",
        "generate a carousel of product images",
        "create an 8-slide powerpoint deck",
    ],
)
def test_verifiable_objective_stays_in_goal(prompt: str):
    assert not is_informational_qa_prompt(prompt)
    assert not goal_qa_misfit("goal", prompt)


def test_goal_never_gets_build_gate():
    assert not requires_plan_approval("goal")
    assert GOAL_QA_MISFIT_MESSAGE == "This looks like Normal"
    assert "execute" in MODE_CHIP_DESCRIPTIONS["goal"].lower()
    assert "hitl" in MODE_CHIP_DESCRIPTIONS["goal"].lower()


def _event_kinds(ws) -> list[str]:
    path = ws.root / "events.jsonl"
    if not path.is_file():
        return []
    kinds: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        import json

        try:
            kinds.append(str(json.loads(line).get("kind") or ""))
        except json.JSONDecodeError:
            continue
    return kinds


@pytest.mark.asyncio
async def test_goal_qa_soft_redirects_to_followup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Informational Goal prompts answer like Normal — no plan/Build theater."""
    from kageha.loop.mode_policy import PLAN_APPROVED_FLAG
    from test_mode_machines import _run_mode

    result, ws, _plan = await _run_mode(
        tmp_path,
        monkeypatch,
        agent_mode="goal",
        auto_approve=True,
        auto_build=False,
        objective="What is HTTP 429?",
    )
    assert result.status != "awaiting_plan_approval"
    assert "goal_qa_misfit" in _event_kinds(ws)
    assert not (ws.root / "plan.md").is_file()
    assert not (ws.root / PLAN_APPROVED_FLAG).is_file()


@pytest.mark.asyncio
async def test_goal_verifiable_objective_still_runs_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from test_mode_machines import _run_mode

    result, ws, _plan = await _run_mode(
        tmp_path,
        monkeypatch,
        agent_mode="goal",
        auto_approve=True,
        auto_build=False,
        objective="Ship hello.txt with hi and verify it exists",
    )
    assert result.status != "awaiting_plan_approval"
    assert "goal_qa_misfit" not in _event_kinds(ws)
    assert (ws.root / "task_state.json").is_file()

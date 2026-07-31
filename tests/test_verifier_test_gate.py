"""Deterministic verifier gates for production-grade success criteria."""

from __future__ import annotations

from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.loop.task_state import ValidationSnapshot
from kageha.loop.verifier import (
    _enforce_test_evidence_gate,
    has_test_pass_evidence,
    task_requires_test_evidence,
)


def test_task_requires_test_evidence_detects_pytest_asks() -> None:
    assert task_requires_test_evidence(
        "Build a KV API and include pytest coverage that proves concurrent writes"
    )
    assert not task_requires_test_evidence("Summarize this README")


def test_has_test_pass_evidence_matches_pytest_output() -> None:
    assert has_test_pass_evidence("======================== 12 passed in 0.41s ========================")
    assert not has_test_pass_evidence("wrote tests/test_api.py")


def test_enforce_test_evidence_gate_downgrades_pass_without_proof() -> None:
    goal = GoalCard(
        task="Build a KV HTTP API with pytest coverage",
        items=[GoalItem(id="g1", description="Verified", passes=True, evidence="files exist")],
    )
    snapshot = ValidationSnapshot(status="pass", notes="looks done")
    gated = _enforce_test_evidence_gate(goal, snapshot, workspace_summary="api.py ok")
    assert gated.status == "repair"
    assert any(d.artifact == "tests" for d in gated.defects)


def test_enforce_test_evidence_gate_keeps_pass_with_proof() -> None:
    goal = GoalCard(
        task="Build a KV HTTP API with pytest coverage",
        items=[
            GoalItem(
                id="g1",
                description="Verified",
                passes=True,
                evidence="pytest: 8 passed",
            )
        ],
    )
    snapshot = ValidationSnapshot(status="pass")
    gated = _enforce_test_evidence_gate(
        goal,
        snapshot,
        transcript_tail="bash ← pytest\n8 passed in 0.2s",
    )
    assert gated.status == "pass"

"""Deterministic lookup/status verify — avoids unknown stalls."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.loop.verifier import (
    is_lookup_status_goal,
    try_deterministic_lookup_verify,
    verify_with_defects,
)
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage


def _lookup_goal() -> GoalCard:
    return GoalCard(
        task="scan the LAN for available TVs",
        items=[
            GoalItem("g1", "Network scanned for TV devices"),
            GoalItem("g2", "List of available TVs obtained and reported"),
        ],
    )


def _deck_goal() -> GoalCard:
    return GoalCard(
        task="create an 8-slide powerpoint deck",
        items=[GoalItem("g1", "8-slide pptx deck produced")],
    )


def test_is_lookup_status_goal_positive():
    assert is_lookup_status_goal(_lookup_goal()) is True
    assert (
        is_lookup_status_goal(
            GoalCard(task="what's the status of the living room TV?", items=[])
        )
        is True
    )


def test_is_lookup_status_goal_rejects_heavy_deliverables():
    assert is_lookup_status_goal(_deck_goal()) is False
    assert (
        is_lookup_status_goal(
            GoalCard(
                task="generate a carousel of product images",
                items=[GoalItem("g1", "carousel slides rendered")],
            )
        )
        is False
    )


def test_deterministic_lookup_pass_with_tool_and_artifact():
    goal = _lookup_goal()
    result = try_deterministic_lookup_verify(
        goal,
        successful_tools=["skill_run"],
        turn_artifacts=["research/network_tvs.md"],
    )
    assert result is not None
    assert result.snapshot.status == "pass"
    assert goal.all_passed()
    assert "skill_run" in goal.items[0].evidence


def test_deterministic_lookup_pass_with_tool_and_answer():
    goal = _lookup_goal()
    result = try_deterministic_lookup_verify(
        goal,
        successful_tools=["skill_run"],
        answer_text=(
            "Found 2 TVs on the LAN: Living Room (192.168.1.40) and "
            "Bedroom (192.168.1.41)."
        ),
    )
    assert result is not None
    assert result.snapshot.status == "pass"
    assert "answer_text" in result.snapshot.notes


def test_deterministic_lookup_requires_tool_success():
    goal = _lookup_goal()
    assert (
        try_deterministic_lookup_verify(
            goal,
            successful_tools=[],
            turn_artifacts=["research/network_tvs.md"],
            answer_text="Found two TVs on the network after a full LAN scan.",
        )
        is None
    )


def test_deterministic_lookup_ignores_meta_tools_only():
    goal = _lookup_goal()
    assert (
        try_deterministic_lookup_verify(
            goal,
            successful_tools=["todo_write", "read_file"],
            answer_text="Found two TVs on the network after a full LAN scan.",
        )
        is None
    )


def test_deterministic_lookup_does_not_pass_heavy_goals():
    goal = _deck_goal()
    assert (
        try_deterministic_lookup_verify(
            goal,
            successful_tools=["bash"],
            turn_artifacts=["deck.pptx"],
            answer_text="Created the powerpoint deck with eight slides as requested.",
        )
        is None
    )


def test_verify_unknown_short_circuits_lookup_when_done():
    router = MagicMock()
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content="not json at all"),
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
    )
    router.chat = AsyncMock(return_value=(MagicMock(model_id="fast"), resp))
    goal = _lookup_goal()
    result = asyncio.run(
        verify_with_defects(
            goal,
            router=router,
            workspace_summary="- research/tvs.md | bytes=120 | text_preview=\"Living Room\"",
            transcript_tail="assistant: Found Living Room TV at 192.168.1.40",
            model_said_done=True,
            successful_tools=["skill_run"],
            turn_artifacts=["research/tvs.md"],
            answer_text=(
                "Found 1 TV on the LAN: Living Room (192.168.1.40). "
                "Details saved to research/tvs.md."
            ),
        )
    )
    assert result.snapshot.status == "pass"
    assert goal.all_passed()
    assert "deterministic lookup/status verify" in result.snapshot.notes


def test_verify_unknown_does_not_short_circuit_mid_run():
    router = MagicMock()
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content=""),
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
    )
    router.chat = AsyncMock(return_value=(MagicMock(model_id="fast"), resp))
    goal = _lookup_goal()
    result = asyncio.run(
        verify_with_defects(
            goal,
            router=router,
            workspace_summary="- research/tvs.md | bytes=120",
            transcript_tail="tool: ok",
            model_said_done=False,
            successful_tools=["skill_run"],
            turn_artifacts=["research/tvs.md"],
            answer_text="Found Living Room TV at 192.168.1.40 on the LAN scan.",
        )
    )
    assert result.snapshot.status == "unknown"
    assert not goal.all_passed()


def test_verify_real_repair_not_weakened():
    router = MagicMock()
    payload = {
        "status": "repair",
        "updates": [{"id": "g1", "passes": False, "evidence": "empty"}],
        "defects": [
            {
                "artifact": "scan",
                "severity": "critical",
                "problem": "Scan tool returned ERROR",
                "evidence": "ERROR: timeout",
                "repair": "Retry skill_run with a longer timeout",
            }
        ],
        "next_action": "repair_artifact",
        "notes": "tool failed",
    }
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content=json.dumps(payload)),
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
    )
    router.chat = AsyncMock(return_value=(MagicMock(model_id="fast"), resp))
    goal = _lookup_goal()
    result = asyncio.run(
        verify_with_defects(
            goal,
            router=router,
            workspace_summary="(no generated files)",
            transcript_tail="tool: ERROR: timeout",
            model_said_done=True,
            successful_tools=["skill_run"],
            turn_artifacts=[],
            answer_text="The scan failed with a timeout; retrying may help discover TVs.",
        )
    )
    assert result.snapshot.status == "repair"
    assert len(result.snapshot.defects) == 1
    assert not goal.all_passed()


def test_verify_empty_defect_unknown_remap_short_circuits_lookup():
    """LLM status=unknown is remapped to repair with no defects — still stall-prone."""
    router = MagicMock()
    payload = {
        "status": "unknown",
        "updates": [],
        "defects": [],
        "next_action": "",
        "notes": "unsure",
    }
    resp = ChatResponse(
        message=ChatMessage(role="assistant", content=json.dumps(payload)),
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
    )
    router.chat = AsyncMock(return_value=(MagicMock(model_id="fast"), resp))
    goal = _lookup_goal()
    result = asyncio.run(
        verify_with_defects(
            goal,
            router=router,
            workspace_summary="- research/tvs.md | bytes=80",
            transcript_tail="assistant: listed TVs",
            model_said_done=True,
            successful_tools=["skill_run"],
            turn_artifacts=["research/tvs.md"],
        )
    )
    assert result.snapshot.status == "pass"
    assert goal.all_passed()

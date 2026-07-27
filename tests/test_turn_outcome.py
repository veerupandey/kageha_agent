"""Per-turn completion and response isolation contracts."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from kageha.loop.controller import (
    _compose_turn_answer,
    _format_pending_question,
    _is_user_facing_reply,
    _latest_turn_assistant_text,
    _pending_question_from_results,
    _sanitized_tool_action,
)
from kageha.loop.goal_card import GoalCard, GoalItem
from kageha.models.base import ChatMessage, ToolCall


def test_internal_completion_status_is_not_a_user_reply():
    assert not _is_user_facing_reply("Goals validated with evidence")
    assert not _is_user_facing_reply("Hit max steps (15)")
    assert _is_user_facing_reply("The browser is open at https://example.com.")


def test_latest_turn_assistant_text_ignores_prior_continuity():
    """Regression: follow-up must not re-emit truncated prior-turn answers."""
    prior = "### Research Summary & 6-Slide Carousel Concept for Kageha Matcha…"
    history = [
        ChatMessage(role="user", content="research matcha carousel"),
        ChatMessage(role="assistant", content=prior),
        ChatMessage(
            role="user",
            content="Task: please use the original tin\n\nWork in session workspace: /tmp",
        ),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="1", name="list_dir", arguments={"path": "."})],
        ),
        ChatMessage(role="tool", name="list_dir", content="research_and_carousel_concept.md"),
    ]
    assert (
        _latest_turn_assistant_text(history, turn_start=2, require_no_tool_calls=True)
        == ""
    )
    history.append(
        ChatMessage(
            role="assistant",
            content="Got it — I'll use the original Classic Ceremonial tin in the slides.",
        )
    )
    assert "original Classic Ceremonial tin" in _latest_turn_assistant_text(
        history, turn_start=2, require_no_tool_calls=True
    )
    assert prior not in _latest_turn_assistant_text(
        history, turn_start=2, require_no_tool_calls=True
    )


def test_compose_turn_answer_does_not_use_prior_research_summary():
    """Even with a completed prior goal card, compose only current-turn evidence."""
    router = MagicMock()
    router.chat = AsyncMock(side_effect=RuntimeError("model unavailable"))
    prior_summary = (
        "### Research Summary & 6-Slide Carousel Concept for Kageha Matcha\n"
        "Deliverable document saved."
    )
    goal = GoalCard(
        task="please use the original ceremonial tin",
        items=[
            GoalItem(
                "g1",
                "Follow-up completed with evidence",
                passes=False,
                evidence="",
            )
        ],
    )
    history = [
        ChatMessage(role="assistant", content=prior_summary),
        ChatMessage(
            role="user",
            content="please use the original ceremonial tin",
        ),
        ChatMessage(
            role="tool",
            name="list_dir",
            content="research_and_carousel_concept.md",
        ),
    ]
    answer = asyncio.run(
        _compose_turn_answer(
            router=router,
            objective="please use the original ceremonial tin",
            status="success",
            goal=goal,
            history=history[1:],  # current-turn slice only
            turn_artifacts=[],
        )
    )
    assert "Research Summary" not in answer
    assert "list_dir" in answer or "research_and_carousel" in answer


def test_final_answer_fallback_uses_only_supplied_turn_history():
    router = MagicMock()
    router.chat = AsyncMock(side_effect=RuntimeError("model unavailable"))
    goal = GoalCard(
        task="open browser",
        items=[GoalItem("g1", "browser open", passes=True, evidence="example.com")],
    )
    history = [
        ChatMessage(
            role="tool",
            name="browser_open",
            content="title: Example Domain url: https://example.com",
        )
    ]

    answer = asyncio.run(
        _compose_turn_answer(
            router=router,
            objective="open browser",
            status="success",
            goal=goal,
            history=history,
            turn_artifacts=["artifacts/browser_open.png"],
        )
    )

    assert "example.com" in answer
    assert "LinkedIn" not in answer


def test_verbose_action_trace_redacts_secrets():
    trace = _sanitized_tool_action(
        [
            ToolCall(
                id="1",
                name="web_request",
                arguments={
                    "url": "https://example.com?q=ok&token=secret-value",
                    "api_key": "super-secret",
                    "headers": {"authorization": "Bearer abc123"},
                },
            )
        ]
    )
    assert "example.com" in trace
    assert "secret-value" not in trace
    assert "super-secret" not in trace
    assert "abc123" not in trace
    assert trace.count("[redacted]") >= 3


def test_deferred_question_becomes_a_turn_outcome():
    result = ChatMessage(
        role="tool",
        name="ask_human",
        content=(
            '{"status":"needs_user_input","question":"Use this style?",'
            '"yes_label":"Use it","no_label":"Choose another"}'
        ),
    )
    pending = _pending_question_from_results([result])
    assert pending == ("Use this style?", "Use it", "Choose another")
    assert _format_pending_question(*pending) == (
        "Use this style?\n\n[Y] Use it\n[N] Choose another"
    )

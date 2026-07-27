"""Huddle control decision + steering."""

from __future__ import annotations

from kageha.loop.adaptive import decide_control, huddle_steering_message
from kageha.loop.task_state import (
    ControlDecision,
    FailureKind,
    FailureRecord,
    TaskState,
    ValidationSnapshot,
)


def test_anti_loop_tool_error_enters_huddle():
    state = TaskState(objective="do X")
    state.failures = [
        FailureRecord(
            step=1,
            action="web_search",
            result="ERROR",
            cause="timeout",
            kind=FailureKind.TOOL_ERROR.value,
            required_change="try different query",
        ),
        FailureRecord(
            step=2,
            action="web_search",
            result="ERROR",
            cause="timeout",
            kind=FailureKind.TOOL_ERROR.value,
            required_change="try different query",
        ),
    ]
    decision, reason = decide_control(state)
    assert decision == ControlDecision.HUDDLE
    assert "Huddle" in reason


def test_huddle_steering_mentions_hitl():
    state = TaskState(objective="y")
    state.validation = ValidationSnapshot(status="fail", notes="blocked")
    msg = huddle_steering_message(state)
    assert "Human confirmation" in msg
    assert "forge_tool" in msg

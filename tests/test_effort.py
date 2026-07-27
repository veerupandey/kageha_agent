"""Task effort classification + Gemini thinking mapping."""

from __future__ import annotations

from kageha.models.effort import classify_effort, gemini_thinking_level


def test_classify_effort_levels():
    assert classify_effort("ok") == "low"
    assert classify_effort("thanks") == "low"
    assert classify_effort("fix the typo in readme") == "medium"
    assert classify_effort(
        "Research Amazon Bedrock AgentCore and create a production architecture "
        "diagram plus a full presentation deck"
    ) == "high"


def test_gemini_thinking_level_mapping():
    assert gemini_thinking_level("high", has_tools=True) == "high"
    assert gemini_thinking_level("medium", has_tools=True) == "medium"
    assert gemini_thinking_level("low", has_tools=True) == "low"
    assert gemini_thinking_level("high", has_tools=False) == "medium"
    assert gemini_thinking_level("medium", has_tools=False) == "low"

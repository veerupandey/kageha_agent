"""Gemini tool-history repair + 400 flatten retry."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx

from kageha.models.base import ChatMessage, ChatResponse, ToolCall, ToolSpec
from kageha.models.gemini import (
    GeminiModel,
    _is_tool_history_400,
    flatten_messages_for_gemini_retry,
)


def test_is_tool_history_400_detects_function_call_order():
    assert _is_tool_history_400(
        'Please ensure that function call turn comes immediately after a user turn'
    )
    assert not _is_tool_history_400("thinkingConfig is not supported")


def test_repair_orphaned_function_response():
    m = GeminiModel(
        model_id="gemini-flash",
        provider="gemini",
        model="gemini-3.6-flash",
        api_key="x",
    )
    messages = [
        ChatMessage(role="user", content="calc"),
        ChatMessage(
            role="tool",
            name="computer_get_state",
            tool_call_id="1",
            content='{"readings":{"value":"17"}}',
        ),
        ChatMessage(role="user", content="continue"),
    ]
    _, contents = m._to_contents(messages)
    assert contents
    # Orphan FR converted to plain text — no functionResponse left.
    assert not any(
        "functionResponse" in (p or {})
        for c in contents
        for p in (c.get("parts") or [])
    )
    assert any("computer_get_state" in str(c) for c in contents)


def test_flatten_keeps_tool_facts():
    msgs = [
        ChatMessage(role="user", content="go"),
        ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall(id="1", name="computer_click", arguments={"ref": "e6"})
            ],
        ),
        ChatMessage(
            role="tool",
            name="computer_click",
            tool_call_id="1",
            content='{"ok": true}',
        ),
    ]
    flat = flatten_messages_for_gemini_retry(msgs)
    assert all(m.role != "tool" for m in flat)
    assert not any(m.tool_calls for m in flat)
    assert any("computer_click" in (m.content or "") for m in flat)
    assert not any(m.role == "assistant" and m.tool_calls for m in flat)
    assert any(
        m.role == "user" and "prior step called" in (m.content or "") for m in flat
    )


def test_chat_retries_with_flattened_history_on_function_call_400():
    m = GeminiModel(
        model_id="gemini-flash",
        provider="gemini",
        model="gemini-3.6-flash",
        api_key="x",
    )
    good = ChatResponse(
        message=ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                ToolCall(id="2", name="computer_get_state", arguments={"app": "Calculator"})
            ],
        )
    )
    err = httpx.HTTPStatusError(
        "Client error '400' — Please ensure that function call turn comes immediately after a user turn",
        request=httpx.Request("POST", "https://example.com"),
        response=httpx.Response(400, request=httpx.Request("POST", "https://example.com")),
    )
    calls: list[list[ChatMessage]] = []

    async def fake_once(messages, tools=None, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            raise err
        return good

    with patch.object(m, "_chat_once", side_effect=fake_once):
        resp = asyncio.run(
            m.chat(
                [
                    ChatMessage(role="user", content="calc"),
                    ChatMessage(
                        role="tool",
                        name="computer_get_state",
                        content="{}",
                    ),
                ],
                tools=[ToolSpec(name="computer_get_state", description="s", parameters={})],
            )
        )
    assert resp.message.tool_calls
    assert len(calls) == 2
    assert all(x.role != "tool" for x in calls[1])

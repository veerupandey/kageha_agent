"""Gemini streamGenerateContent (offline)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from kageha.models.base import ChatMessage, ToolSpec
from kageha.models.gemini import GeminiModel
from kageha.models.streaming import collect_stream


def _model() -> GeminiModel:
    return GeminiModel(
        model_id="gemini-test",
        provider="gemini",
        model="gemini-2.5-flash",
        api_key="test-key",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )


@pytest.mark.asyncio
async def test_gemini_stream_yields_text_deltas():
    lines = [
        'data: {"candidates":[{"content":{"parts":[{"text":"Hi"}]}}]}',
        'data: {"candidates":[{"content":{"parts":[{"text":" there"}]},'
        '"finishReason":"STOP"}]}',
    ]
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None

    async def aiter_lines():
        for line in lines:
            yield line

    mock_resp.aiter_lines = aiter_lines

    @asynccontextmanager
    async def fake_stream(*_args, **_kwargs):
        yield mock_resp

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.stream = fake_stream

    model = _model()
    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = await collect_stream(
            model.stream([ChatMessage(role="user", content="x")]),
            model_id=model.model_id,
        )
    assert resp.message.content == "Hi there"
    assert resp.stop_reason == "STOP"


@pytest.mark.asyncio
async def test_gemini_stream_function_call():
    lines = [
        (
            'data: {"candidates":[{"content":{"parts":[{"functionCall":'
            '{"name":"echo","args":{"x":1}}}]},'
            '"finishReason":"STOP"}]}'
        ),
    ]
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None

    async def aiter_lines():
        for line in lines:
            yield line

    mock_resp.aiter_lines = aiter_lines

    @asynccontextmanager
    async def fake_stream(*_args, **_kwargs):
        yield mock_resp

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.stream = fake_stream

    model = _model()
    spec = ToolSpec(name="echo", description="e", parameters={})
    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = await collect_stream(
            model.stream(
                [ChatMessage(role="user", content="x")],
                tools=[spec],
            )
        )
    assert len(resp.message.tool_calls) == 1
    assert resp.message.tool_calls[0].name == "echo"
    assert resp.message.tool_calls[0].arguments == {"x": 1}

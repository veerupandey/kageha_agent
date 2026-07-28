"""OpenAI-compatible streaming chat completions (offline)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from kageha.models.base import ChatMessage, ToolSpec
from kageha.models.openai_compat import OpenAICompatModel
from kageha.models.streaming import collect_stream


def _model() -> OpenAICompatModel:
    return OpenAICompatModel(
        model_id="test",
        provider="openai",
        model="gpt-test",
        base_url="https://example.com/v1",
        api_key="sk-test",
    )


@pytest.mark.asyncio
async def test_openai_compat_stream_yields_content_deltas():
    lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        "",
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "data: [DONE]",
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
        pieces = [
            d
            async for d in model.stream(
                [ChatMessage(role="user", content="hi")],
            )
        ]

    assert [p.text for p in pieces] == ["Hello", " world"]


@pytest.mark.asyncio
async def test_openai_compat_stream_sends_stream_true():
    captured: dict = {}

    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None

    async def aiter_lines():
        yield "data: [DONE]"

    mock_resp.aiter_lines = aiter_lines

    @asynccontextmanager
    async def fake_stream(_method, _url, *, headers=None, json=None, **kwargs):
        captured["json"] = json
        captured["headers"] = headers
        yield mock_resp

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.stream = fake_stream

    model = _model()
    with patch("httpx.AsyncClient", return_value=mock_client):
        async for _ in model.stream([ChatMessage(role="user", content="x")]):
            pass

    assert captured["json"]["stream"] is True
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


@pytest.mark.asyncio
async def test_openai_compat_stream_keeps_reasoning_separate():
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"Simple greeting."}}]}',
        'data: {"choices":[{"delta":{"content":"Hey!"}}]}',
        "data: [DONE]",
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
            model.stream([ChatMessage(role="user", content="hi")]),
        )
    assert resp.message.content == "Hey!"
    assert resp.message.reasoning == "Simple greeting."


@pytest.mark.asyncio
async def test_openai_compat_stream_with_tools_assembles_tool_calls():
    lines = [
        (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1",'
            '"function":{"name":"echo","arguments":"{\\"x\\":"}}]}}]}'
        ),
        (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"1}"}}]},"finish_reason":"tool_calls"}]}'
        ),
        "data: [DONE]",
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
    spec = ToolSpec(name="echo", description="echo", parameters={})
    with patch("httpx.AsyncClient", return_value=mock_client):
        resp = await collect_stream(
            model.stream(
                [ChatMessage(role="user", content="x")],
                tools=[spec],
            ),
            model_id=model.model_id,
        )

    assert resp.stop_reason == "tool_calls"
    assert len(resp.message.tool_calls) == 1
    assert resp.message.tool_calls[0].name == "echo"
    assert resp.message.tool_calls[0].arguments == {"x": 1}

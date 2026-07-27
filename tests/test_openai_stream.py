"""OpenAI-compatible streaming chat completions (offline)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from kageha.models.base import ChatMessage, StreamDelta, ToolSpec
from kageha.models.openai_compat import OpenAICompatModel


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

    assert pieces == [
        StreamDelta(text="Hello"),
        StreamDelta(text=" world"),
    ]


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
async def test_openai_compat_stream_tools_not_implemented():
    model = _model()
    spec = ToolSpec(name="echo", description="echo", parameters={})
    with pytest.raises(NotImplementedError, match="tools"):
        async for _ in model.stream(
            [ChatMessage(role="user", content="x")],
            tools=[spec],
        ):
            pass

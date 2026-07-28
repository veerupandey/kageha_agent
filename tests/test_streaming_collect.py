"""Stream delta accumulator."""

from __future__ import annotations

import pytest

from kageha.models.base import StreamDelta, ToolCall
from kageha.models.streaming import collect_stream, supports_stream


async def _gen(deltas):
    for d in deltas:
        yield d


@pytest.mark.asyncio
async def test_collect_stream_text_and_callback():
    seen: list[str] = []
    resp = await collect_stream(
        _gen(
            [
                StreamDelta(text="Hello"),
                StreamDelta(text=" world"),
                StreamDelta(text="", finish_reason="stop"),
            ]
        ),
        on_text_delta=seen.append,
        model_id="m1",
    )
    assert resp.message.content == "Hello world"
    assert seen == ["Hello", " world"]
    assert resp.stop_reason == "stop"
    assert resp.model == "m1"


@pytest.mark.asyncio
async def test_collect_stream_hides_reasoning_from_callback():
    seen: list[str] = []
    resp = await collect_stream(
        _gen(
            [
                StreamDelta(reasoning="Simple greeting. Reply in chat, no tools."),
                StreamDelta(text="Hey! What can I help you with today?"),
            ]
        ),
        on_text_delta=seen.append,
    )
    assert seen == ["Hey! What can I help you with today?"]
    assert resp.message.content == "Hey! What can I help you with today?"
    assert "Simple greeting" in resp.message.reasoning


@pytest.mark.asyncio
async def test_collect_stream_tool_fragments():
    resp = await collect_stream(
        _gen(
            [
                StreamDelta(
                    tool_call_index=0,
                    tool_call_id="c1",
                    tool_name="echo",
                    arguments_json='{"a":',
                ),
                StreamDelta(tool_call_index=0, arguments_json="1}"),
                StreamDelta(finish_reason="tool_calls"),
            ]
        )
    )
    assert len(resp.message.tool_calls) == 1
    assert resp.message.tool_calls[0].name == "echo"
    assert resp.message.tool_calls[0].arguments == {"a": 1}


@pytest.mark.asyncio
async def test_collect_stream_complete_tool_call():
    resp = await collect_stream(
        _gen(
            [
                StreamDelta(
                    tool_call=ToolCall(id="x", name="web_search", arguments={"q": "hi"})
                )
            ]
        )
    )
    assert resp.message.tool_calls[0].name == "web_search"


def test_supports_stream():
    class HasStream:
        async def stream(self, *a, **k):  # noqa: ANN001
            if False:
                yield StreamDelta(text="")

    class NoStream:
        pass

    assert supports_stream(HasStream())
    assert not supports_stream(NoStream())

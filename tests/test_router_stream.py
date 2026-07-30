"""Router prefers model.stream when on_text_delta is set."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kageha.models.base import ChatMessage, ChatResponse, ChatUsage, StreamDelta
from kageha.models.router import ModelRouter


class _FakeStreamModel:
    model_id = "fake-stream"
    provider = "openai"

    def __init__(self) -> None:
        self.chat_calls = 0
        self.stream_calls = 0

    async def chat(self, messages, tools=None, **kwargs):  # noqa: ANN001
        self.chat_calls += 1
        return ChatResponse(
            message=ChatMessage(role="assistant", content="buffered"),
            usage=ChatUsage(),
            model=self.model_id,
        )

    async def stream(self, messages, tools=None, **kwargs):  # noqa: ANN001
        self.stream_calls += 1
        yield StreamDelta(text="streamed", model=self.model_id)
        yield StreamDelta(text="", finish_reason="stop", model=self.model_id)

    async def smoke(self) -> str:
        return "ok"


@pytest.mark.asyncio
async def test_router_uses_stream_when_delta_callback_set(monkeypatch):
    fake = _FakeStreamModel()
    router = ModelRouter.__new__(ModelRouter)
    router.registry = MagicMock()
    router.sticky = {}
    router.last_provider = {}
    router.last_model = {}
    router.history = []
    router.failover_notices = []
    router.once_override = None
    router.session_override = None
    router.role_overrides = {}
    router.on_failover = None
    router.failures = {}

    def pick(_role, **kwargs):  # noqa: ANN001, ANN003
        return fake

    router._pick_untried = pick  # type: ignore[method-assign]
    router.ladder = lambda role: ["fake-stream"]  # type: ignore[method-assign]
    router.record_success = lambda *a, **k: None  # type: ignore[method-assign]
    router.record_failure = lambda *a, **k: None  # type: ignore[method-assign]
    router._record_failover = lambda **k: None  # type: ignore[method-assign]
    router.drain_failover_notices = lambda: []  # type: ignore[method-assign]

    deltas: list[str] = []
    model, resp = await ModelRouter.chat(
        router,
        [ChatMessage(role="user", content="hi")],
        tools=None,
        role="default",
        on_text_delta=deltas.append,
    )
    assert model is fake
    assert resp.message.content == "streamed"
    assert fake.stream_calls == 1
    assert fake.chat_calls == 0
    assert deltas == ["streamed"]


@pytest.mark.asyncio
async def test_router_falls_back_to_chat_without_callback():
    fake = _FakeStreamModel()
    router = ModelRouter.__new__(ModelRouter)
    router.registry = MagicMock()
    router.sticky = {}
    router.last_provider = {}
    router.last_model = {}
    router.history = []
    router.failover_notices = []
    router.once_override = None
    router.session_override = None
    router.role_overrides = {}
    router.on_failover = None
    router.failures = {}
    router._pick_untried = lambda _role, **k: fake  # type: ignore[method-assign]
    router.ladder = lambda role: ["fake-stream"]  # type: ignore[method-assign]
    router.record_success = lambda *a, **k: None  # type: ignore[method-assign]
    router.record_failure = lambda *a, **k: None  # type: ignore[method-assign]
    router._record_failover = lambda **k: None  # type: ignore[method-assign]

    model, resp = await ModelRouter.chat(
        router,
        [ChatMessage(role="user", content="hi")],
        tools=None,
        role="default",
    )
    assert resp.message.content == "buffered"
    assert fake.chat_calls == 1
    assert fake.stream_calls == 0

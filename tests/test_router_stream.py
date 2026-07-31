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


def _bare_router(fake: object) -> ModelRouter:
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
    router.circuit_failures = {}
    router.open_circuit_after = 3
    router.provider_control = None
    router._pick_untried = lambda _role, **k: fake  # type: ignore[method-assign]
    router.ladder = lambda role: ["fake-stream"]  # type: ignore[method-assign]
    router.record_success = lambda *a, **k: None  # type: ignore[method-assign]
    router.record_failure = lambda *a, **k: None  # type: ignore[method-assign]
    router._record_failover = lambda **k: None  # type: ignore[method-assign]
    router.drain_failover_notices = lambda: []  # type: ignore[method-assign]
    return router


@pytest.mark.asyncio
async def test_router_uses_stream_when_delta_callback_set(monkeypatch):
    fake = _FakeStreamModel()
    router = _bare_router(fake)

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
    router = _bare_router(fake)

    _model, resp = await ModelRouter.chat(
        router,
        [ChatMessage(role="user", content="hi")],
        tools=None,
        role="default",
    )
    assert resp.message.content == "buffered"
    assert fake.chat_calls == 1
    assert fake.stream_calls == 0


@pytest.mark.asyncio
async def test_router_falls_back_when_stream_is_empty():
    """Empty streamed replies (no text, no tools) must retry buffered chat."""

    class _EmptyThenBuffered:
        model_id = "fake-stream"
        provider = "openai"

        def __init__(self) -> None:
            self.chat_calls = 0
            self.stream_calls = 0

        async def chat(self, messages, tools=None, **kwargs):  # noqa: ANN001
            self.chat_calls += 1
            return ChatResponse(
                message=ChatMessage(role="assistant", content="buffered-ok"),
                usage=ChatUsage(),
                model=self.model_id,
            )

        async def stream(self, messages, tools=None, **kwargs):  # noqa: ANN001
            self.stream_calls += 1
            yield StreamDelta(reasoning="thinking…", model=self.model_id)
            yield StreamDelta(text="", finish_reason="stop", model=self.model_id)

        async def smoke(self) -> str:
            return "ok"

    fake = _EmptyThenBuffered()
    router = _bare_router(fake)

    model, resp = await ModelRouter.chat(
        router,
        [ChatMessage(role="user", content="hi")],
        tools=None,
        role="default",
        on_text_delta=lambda _t: None,
    )
    assert model is fake
    assert resp.message.content == "buffered-ok"
    assert fake.stream_calls == 1
    assert fake.chat_calls == 1


@pytest.mark.asyncio
async def test_router_retries_same_model_on_rate_limit(monkeypatch):
    class _FlakyThenOk:
        model_id = "fake-stream"
        provider = "openai"

        def __init__(self) -> None:
            self.chat_calls = 0

        async def chat(self, messages, tools=None, **kwargs):  # noqa: ANN001
            self.chat_calls += 1
            if self.chat_calls == 1:
                from kageha.models.retry import ProviderHTTPError

                raise ProviderHTTPError(
                    "HTTP 429 Too Many Requests",
                    status_code=429,
                    retry_after_s=0.01,
                )
            return ChatResponse(
                message=ChatMessage(role="assistant", content="recovered"),
                usage=ChatUsage(),
                model=self.model_id,
            )

        async def smoke(self) -> str:
            return "ok"

    fake = _FlakyThenOk()
    router = _bare_router(fake)
    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("kageha.models.retry.sleep_backoff", _fake_sleep)

    model, resp = await ModelRouter.chat(
        router,
        [ChatMessage(role="user", content="hi")],
        tools=None,
        role="default",
    )
    assert model is fake
    assert resp.message.content == "recovered"
    assert fake.chat_calls == 2
    assert sleeps

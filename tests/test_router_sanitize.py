"""Cross-provider message sanitization + sticky routing."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from kageha.models.base import ChatMessage, ToolCall
from kageha.models.router import ModelRouter, sanitize_messages_for_provider


def test_sanitize_collapses_tools_when_provider_changes():
    msgs = [
        ChatMessage(role="user", content="search"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="1", name="web_search", arguments={"query": "x"})],
        ),
        ChatMessage(role="tool", name="web_search", tool_call_id="1", content="results"),
    ]
    out = sanitize_messages_for_provider(
        msgs, target_provider="gemini", source_provider="siliconflow"
    )
    assert all(m.role in {"user", "assistant", "system"} for m in out)
    assert not any(m.tool_calls for m in out)
    assert any("web_search" in (m.content or "") for m in out)
    # Tool-call turns become user breadcrumbs — never assistant stubs to echo.
    assert not any(
        (m.role == "assistant" and "[called tools:" in (m.content or "")) for m in out
    )
    assert any(
        m.role == "user" and "prior step called" in (m.content or "") for m in out
    )


def test_sanitize_noop_same_provider():
    msgs = [
        ChatMessage(
            role="assistant",
            tool_calls=[ToolCall(id="1", name="bash", arguments={})],
        )
    ]
    out = sanitize_messages_for_provider(
        msgs, target_provider="gemini", source_provider="gemini"
    )
    assert out[0].tool_calls


def test_router_surfaces_real_errors_not_already_tried():
    registry = MagicMock()
    registry.roles = {"tool_calling": ["a", "b"]}
    registry.available_models.return_value = [
        MagicMock(id="a"),
        MagicMock(id="b"),
    ]

    def build(mid: str):
        m = MagicMock()
        m.model_id = mid
        m.provider = "p"
        m.chat = AsyncMock(side_effect=RuntimeError(f"{mid} exploded 403 Forbidden"))
        return m

    registry.build.side_effect = build
    router = ModelRouter(registry)
    with __import__("pytest").raises(RuntimeError) as ei:
        asyncio.run(
            router.chat(
                [ChatMessage(role="user", content="hi")],
                role="tool_calling",
                task_id="t1",
            )
        )
    msg = str(ei.value)
    assert "already_tried" not in msg
    assert "a exploded" in msg
    assert "b exploded" in msg


def test_router_failover_notice_on_recovery():
    registry = MagicMock()
    registry.roles = {"default": ["a", "b"]}
    registry.available_models.return_value = [
        MagicMock(id="a"),
        MagicMock(id="b"),
    ]
    good = MagicMock()
    good.message = ChatMessage(role="assistant", content="ok")
    good.usage = MagicMock(prompt_tokens=1, completion_tokens=1, cached_tokens=0)

    def build(mid: str):
        m = MagicMock()
        m.model_id = mid
        m.provider = "p"
        if mid == "a":
            m.chat = AsyncMock(side_effect=RuntimeError("a timed out"))
        else:
            m.chat = AsyncMock(return_value=good)
        return m

    registry.build.side_effect = build
    notices: list[tuple[str, str, str]] = []
    router = ModelRouter(
        registry,
        on_failover=lambda f, t, e: notices.append((f, t, e)),
    )
    model, resp = asyncio.run(
        router.chat(
            [ChatMessage(role="user", content="hi")],
            role="default",
            task_id="t1",
        )
    )
    assert model.model_id == "b"
    assert resp.message.content == "ok"
    drained = router.drain_failover_notices()
    # Retry notices may precede the actual failover notice
    failover = [n for n in drained if n["from"] != n["to"]]
    assert failover and failover[0]["from"] == "a" and failover[0]["to"] == "b"
    line = ModelRouter.format_failover_line(failover[0])
    assert "a → b" in line
    assert notices and notices[0][0] == "a"


def test_gemini3_uses_thinking_level_minimal():
    from kageha.models.gemini import GeminiModel

    m = GeminiModel(
        model_id="gemini-flash",
        provider="gemini",
        model="gemini-3.6-flash",
        api_key="x",
    )
    cfg = m._thinking_config(tools=None, max_tokens=512)
    assert cfg == {"thinkingLevel": "minimal"}
    assert m._thinking_config(tools=[MagicMock()], max_tokens=8192)["thinkingLevel"] in {
        "low",
        "medium",
    }

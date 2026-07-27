"""Router must not select Antigravity/gemini-cli when native tools are attached."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from kageha.models.base import ChatMessage, ChatResponse, ToolCall, ToolSpec
from kageha.models.registry import ModelConfig, ModelRegistry, ProviderConfig
from kageha.models.router import ModelRouter


def _registry() -> ModelRegistry:
    return ModelRegistry(
        providers={
            "gemini": ProviderConfig(
                name="gemini",
                protocol="gemini",
                base_url="https://example",
                api_key_env="GEMINI_API_KEY",
            ),
            "antigravity": ProviderConfig(
                name="antigravity",
                protocol="gemini_cli",
                base_url="",
                api_key_env="ANTIGRAVITY_CLI",
            ),
        },
        models={
            "antigravity-flash": ModelConfig(
                id="antigravity-flash",
                provider="antigravity",
                model="gemini-3.6-flash",
                roles=["tool_calling", "default"],
                capabilities=[],
            ),
            "gemini-flash": ModelConfig(
                id="gemini-flash",
                provider="gemini",
                model="gemini-3.6-flash",
                roles=["tool_calling"],
                capabilities=["tool_calling", "vision"],
            ),
        },
        roles={
            "tool_calling": ["antigravity-flash", "gemini-flash"],
            "default": ["antigravity-flash", "gemini-flash"],
        },
    )


def test_pick_skips_antigravity_when_tools_required(monkeypatch):
    reg = _registry()
    router = ModelRouter(reg)
    built: list[str] = []

    def fake_build(model_id: str):
        built.append(model_id)
        m = MagicMock()
        m.model_id = model_id
        m.provider = reg.models[model_id].provider
        return m

    monkeypatch.setattr(
        reg,
        "available_models",
        lambda: [reg.models["antigravity-flash"], reg.models["gemini-flash"]],
    )
    monkeypatch.setattr(reg, "build", fake_build)

    model = router._pick_untried(
        "tool_calling",
        task_id="t1",
        tried=set(),
        require_tool_calling=True,
    )
    assert model.model_id == "gemini-flash"
    assert built == ["gemini-flash"]


def test_chat_with_tools_uses_api_model_not_antigravity(monkeypatch):
    reg = _registry()
    router = ModelRouter(reg)
    router.set_session_override("antigravity-flash")

    api_model = MagicMock()
    api_model.model_id = "gemini-flash"
    api_model.provider = "gemini"
    api_model.chat = AsyncMock(
        return_value=ChatResponse(
            message=ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="computer_get_state",
                        arguments={"app": "Calculator"},
                    )
                ],
            )
        )
    )

    def fake_build(model_id: str):
        assert model_id == "gemini-flash"
        return api_model

    monkeypatch.setattr(
        reg,
        "available_models",
        lambda: [reg.models["antigravity-flash"], reg.models["gemini-flash"]],
    )
    monkeypatch.setattr(reg, "build", fake_build)

    tools = [ToolSpec(name="computer_get_state", description="state", parameters={})]

    async def _run():
        model, resp = await router.chat(
            [ChatMessage(role="user", content="open Calculator")],
            tools,
            role="tool_calling",
            task_id="run-cu",
        )
        assert model.model_id == "gemini-flash"
        assert resp.message.tool_calls
        notices = router.drain_failover_notices()
        assert notices
        assert notices[0]["from"] == "antigravity-flash"
        assert notices[0]["to"] == "gemini-flash"

    asyncio.run(_run())


def test_gemini_cli_raises_when_tools_passed():
    import pytest

    from kageha.models.gemini_cli import GeminiCliModel

    cli = GeminiCliModel(model_id="antigravity-flash", provider="antigravity", model="x")

    async def _run():
        with pytest.raises(RuntimeError, match="cannot execute Kageha native tool loops"):
            await cli.chat(
                [ChatMessage(role="user", content="hi")],
                tools=[ToolSpec(name="computer_click", description="c", parameters={})],
            )

    asyncio.run(_run())

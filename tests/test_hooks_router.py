"""Router honors project lifecycle hooks."""

from __future__ import annotations

import asyncio

from kageha.harness.router import execute_tool_calls
from kageha.harness.tools.base import Tool, ToolRegistry
from kageha.models.base import ToolCall
from kageha.project.hooks import HookRunner, HookSpec


def test_pre_tool_hook_blocks_invoke():
    reg = ToolRegistry()

    async def echo(text: str = "") -> str:
        return f"ok:{text}"

    reg.register(
        Tool(
            name="echo_tool",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
            handler=echo,
        )
    )
    hooks = HookRunner(
        hooks=[
            HookSpec(
                event="preToolUse",
                matcher="echo_tool",
                deny_message="blocked by policy",
            )
        ]
    )
    msgs = asyncio.run(
        execute_tool_calls(
            reg,
            [ToolCall(id="1", name="echo_tool", arguments={"text": "hi"})],
            hooks=hooks,
        )
    )
    assert len(msgs) == 1
    assert "DENIED" in (msgs[0].content or "")
    assert "blocked by policy" in (msgs[0].content or "")


def test_post_tool_hook_appends_context():
    reg = ToolRegistry()

    async def echo(text: str = "") -> str:
        return f"ok:{text}"

    reg.register(
        Tool(
            name="echo_tool",
            description="echo",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
            handler=echo,
        )
    )
    hooks = HookRunner(
        hooks=[
            HookSpec(
                event="postToolUse",
                command="printf 'lint-ok'",
            )
        ]
    )
    msgs = asyncio.run(
        execute_tool_calls(
            reg,
            [ToolCall(id="1", name="echo_tool", arguments={"text": "hi"})],
            hooks=hooks,
        )
    )
    assert "ok:hi" in (msgs[0].content or "")
    assert "lint-ok" in (msgs[0].content or "")

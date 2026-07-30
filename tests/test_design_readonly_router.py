"""Router hard-denies mutating tools during Plan design."""

from __future__ import annotations


import pytest

from kageha.harness.router import execute_tool_calls
from kageha.harness.tools.base import Tool, ToolRegistry
from kageha.models.base import ToolCall


@pytest.mark.asyncio
async def test_design_readonly_blocks_write_file():
    reg = ToolRegistry()
    called = {"n": 0}

    async def handler(path: str = "", content: str = "") -> str:
        called["n"] += 1
        return "wrote"

    reg.register(
        Tool(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
    )
    results = await execute_tool_calls(
        reg,
        [ToolCall(id="1", name="write_file", arguments={"path": "a", "content": "b"})],
        design_readonly=True,
    )
    assert called["n"] == 0
    assert "DENIED" in (results[0].content or "")
    assert "read-only" in (results[0].content or "").lower()


@pytest.mark.asyncio
async def test_design_readonly_allows_read_file():
    reg = ToolRegistry()

    async def handler(path: str = "") -> str:
        return "ok-content"

    reg.register(
        Tool(
            name="read_file",
            description="read",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )
    )
    results = await execute_tool_calls(
        reg,
        [ToolCall(id="1", name="read_file", arguments={"path": "a"})],
        design_readonly=True,
    )
    assert results[0].content == "ok-content"

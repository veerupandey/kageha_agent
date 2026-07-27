import asyncio

from kageha.harness.router import execute_tool_calls
from kageha.harness.tools.base import Tool, ToolRegistry
from kageha.models.base import ToolCall


def test_parallel_tools():
    reg = ToolRegistry()

    async def slow_a() -> str:
        await asyncio.sleep(0.05)
        return "a"

    async def slow_b() -> str:
        await asyncio.sleep(0.05)
        return "b"

    reg.register(
        Tool(
            name="a",
            description="a",
            parameters={"type": "object", "properties": {}},
            handler=slow_a,
        )
    )
    reg.register(
        Tool(
            name="b",
            description="b",
            parameters={"type": "object", "properties": {}},
            handler=slow_b,
        )
    )

    async def _run():
        calls = [
            ToolCall(id="1", name="a", arguments={}),
            ToolCall(id="2", name="b", arguments={}),
        ]
        t0 = asyncio.get_event_loop().time()
        msgs = await execute_tool_calls(reg, calls, max_parallel=2)
        elapsed = asyncio.get_event_loop().time() - t0
        assert {m.content for m in msgs} == {"a", "b"}
        assert elapsed < 0.09  # parallel, not 0.10+

    asyncio.run(_run())

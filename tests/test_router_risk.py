"""Router risk_class policy + serialize gated tools."""

from __future__ import annotations

import asyncio

from kageha.harness.approvals import ApprovalDecision, ApprovalGate, ApprovalRequest
from kageha.harness.router import execute_tool_calls
from kageha.harness.tools.base import Tool, ToolRegistry
from kageha.models.base import ToolCall


def test_browser_risk_requires_approval():
    reg = ToolRegistry()
    called = {"n": 0}

    async def open_url(url: str) -> str:
        called["n"] += 1
        return f"opened {url}"

    reg.register(
        Tool(
            name="browser_open",
            description="open",
            parameters={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
            handler=open_url,
            risk_class="browser",
        )
    )

    async def deny(_req: ApprovalRequest) -> bool:
        return False

    gate = ApprovalGate(approver=deny, auto_approve=False)

    async def _run():
        msgs = await execute_tool_calls(
            reg,
            [ToolCall(id="1", name="browser_open", arguments={"url": "https://example.com"})],
            approvals=gate,
        )
        return msgs

    msgs = asyncio.run(_run())
    assert called["n"] == 0
    assert "DENIED" in (msgs[0].content or "")


def test_safe_tools_still_parallel():
    reg = ToolRegistry()

    async def slow_a() -> str:
        await asyncio.sleep(0.05)
        return "a"

    async def slow_b() -> str:
        await asyncio.sleep(0.05)
        return "b"

    reg.register(Tool(name="a", description="a", parameters={"type": "object", "properties": {}}, handler=slow_a))
    reg.register(Tool(name="b", description="b", parameters={"type": "object", "properties": {}}, handler=slow_b))

    async def _run():
        t0 = asyncio.get_event_loop().time()
        msgs = await execute_tool_calls(
            reg,
            [ToolCall(id="1", name="a", arguments={}), ToolCall(id="2", name="b", arguments={})],
            max_parallel=2,
        )
        return msgs, asyncio.get_event_loop().time() - t0

    msgs, elapsed = asyncio.run(_run())
    assert {m.content for m in msgs} == {"a", "b"}
    assert elapsed < 0.09


def test_gated_tools_run_serially():
    reg = ToolRegistry()
    order: list[str] = []

    async def one() -> str:
        order.append("start-a")
        await asyncio.sleep(0.04)
        order.append("end-a")
        return "a"

    async def two() -> str:
        order.append("start-b")
        await asyncio.sleep(0.04)
        order.append("end-b")
        return "b"

    reg.register(
        Tool(
            name="browser_a",
            description="a",
            parameters={"type": "object", "properties": {}},
            handler=one,
            risk_class="browser",
        )
    )
    reg.register(
        Tool(
            name="browser_b",
            description="b",
            parameters={"type": "object", "properties": {}},
            handler=two,
            risk_class="browser",
        )
    )
    gate = ApprovalGate(auto_approve=True)

    async def _run():
        return await execute_tool_calls(
            reg,
            [
                ToolCall(id="1", name="browser_a", arguments={}),
                ToolCall(id="2", name="browser_b", arguments={}),
            ],
            max_parallel=4,
            approvals=gate,
        )

    msgs = asyncio.run(_run())
    assert [m.content for m in msgs] == ["a", "b"]
    # Serial: a fully finishes before b starts
    assert order == ["start-a", "end-a", "start-b", "end-b"]


def test_approval_gate_lock_serializes_require():
    gate = ApprovalGate(auto_approve=False)
    active = 0
    max_active = 0

    async def slow_approver(_req: ApprovalRequest) -> bool:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1
        return True

    gate.approver = slow_approver

    async def _run():
        await asyncio.gather(
            gate.require(
                ApprovalRequest(action="a", detail="a", risk_class="hitl", default=ApprovalDecision.ASK)
            ),
            gate.require(
                ApprovalRequest(action="b", detail="b", risk_class="hitl", default=ApprovalDecision.ASK)
            ),
        )

    asyncio.run(_run())
    assert max_active == 1

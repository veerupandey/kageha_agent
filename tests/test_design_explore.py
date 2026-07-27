"""Design explore role/model failover UX."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kageha.loop.design_explore import (
    explore_before_plan,
    filter_design_tool_specs,
)
from kageha.models.base import ChatMessage, ChatResponse, ChatUsage, ToolCall, ToolSpec
from kageha.obs.events import EventLog


def _resp(text: str = "found README", tool_calls: list[ToolCall] | None = None) -> ChatResponse:
    return ChatResponse(
        message=ChatMessage(
            role="assistant",
            content=text,
            tool_calls=tool_calls or [],
        ),
        stop_reason="stop",
        usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
    )


def _model(mid: str) -> MagicMock:
    m = MagicMock()
    m.model_id = mid
    m.provider = "fake"
    return m


class _FailThenOkRouter:
    """Fail planning role (429), succeed on tool_calling."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._notices: list[dict[str, Any]] = []

    def ladder(self, role: str) -> list[str]:
        return {
            "planning": ["plan-model"],
            "tool_calling": ["tools-model"],
            "default": ["default-model"],
        }.get(role, ["default-model"])

    def drain_failover_notices(self) -> list[dict[str, Any]]:
        out = list(self._notices)
        self._notices.clear()
        return out

    async def chat(self, *_a: Any, role: str = "default", **_k: Any):
        self.calls.append(role)
        if role == "planning":
            raise RuntimeError(
                "All models failed for role=planning. plan-model: HTTP 429 rate limit"
            )
        return _model("tools-model"), _resp("notes after failover")


class _LadderFailoverRouter:
    """Same role recovers via router ladder notices."""

    def __init__(self) -> None:
        self._notices = [
            {
                "from": "plan-a",
                "to": "plan-b",
                "error": "HTTP 503",
                "role": "planning",
            }
        ]

    def ladder(self, role: str) -> list[str]:
        return ["plan-a", "plan-b"]

    def drain_failover_notices(self) -> list[dict[str, Any]]:
        out = list(self._notices)
        self._notices.clear()
        return out

    async def chat(self, *_a: Any, role: str = "default", **_k: Any):
        return _model("plan-b"), _resp("recovered notes")


class _AllFailRouter:
    def ladder(self, role: str) -> list[str]:
        return [f"{role}-model"]

    def drain_failover_notices(self) -> list[dict[str, Any]]:
        return []

    async def chat(self, *_a: Any, role: str = "default", **_k: Any):
        raise RuntimeError(f"HTTP 500 on {role}")


def test_filter_design_tool_specs_drops_mutating():
    specs = [
        ToolSpec(name="read_file", description="r", parameters={}),
        ToolSpec(name="write_file", description="w", parameters={}),
        ToolSpec(name="bash", description="b", parameters={}),
        ToolSpec(name="web_search", description="s", parameters={}),
    ]
    allowed = {s.name for s in filter_design_tool_specs(specs)}
    assert "read_file" in allowed
    assert "web_search" in allowed
    assert "write_file" not in allowed
    assert "bash" not in allowed


@pytest.mark.asyncio
async def test_explore_failover_planning_429_uses_tool_calling():
    router = _FailThenOkRouter()
    events = EventLog()
    specs = [ToolSpec(name="read_file", description="r", parameters={})]

    async def exec_tools(_calls):
        raise AssertionError("no tools expected")

    notes = await explore_before_plan(
        task="Add healthcheck",
        router=router,
        tool_specs=specs,
        execute_tools=exec_tools,
        events=events,
        max_steps=2,
        role="planning",
    )
    assert "failover" in notes or "notes after failover" in notes
    assert "planning" in router.calls
    assert "tool_calling" in router.calls
    kinds = [e["kind"] for e in events.events]
    assert "design_explore_failover" in kinds
    failovers = [e for e in events.events if e["kind"] == "design_explore_failover"]
    msg = str(failovers[0]["data"].get("message") or "")
    assert msg.startswith("Explore:")
    assert "→" in msg
    assert "tools-model" in msg or "tool_calling" in msg


@pytest.mark.asyncio
async def test_explore_emits_model_ladder_failover_notice():
    router = _LadderFailoverRouter()
    events = EventLog()
    specs = [ToolSpec(name="list_dir", description="l", parameters={})]

    notes = await explore_before_plan(
        task="Survey repo",
        router=router,
        tool_specs=specs,
        execute_tools=AsyncMock(return_value=[]),
        events=events,
        max_steps=1,
        role="planning",
    )
    assert "recovered" in notes
    failovers = [e for e in events.events if e["kind"] == "design_explore_failover"]
    assert failovers
    assert failovers[0]["data"]["from"] == "plan-a"
    assert failovers[0]["data"]["to"] == "plan-b"
    assert "Explore: plan-a → plan-b" in failovers[0]["data"]["message"]


@pytest.mark.asyncio
async def test_explore_all_roles_fail_raises():
    router = _AllFailRouter()
    events = EventLog()
    specs = [ToolSpec(name="read_file", description="r", parameters={})]
    with pytest.raises(RuntimeError, match="500"):
        await explore_before_plan(
            task="x",
            router=router,
            tool_specs=specs,
            execute_tools=AsyncMock(return_value=[]),
            events=events,
            max_steps=1,
            role="planning",
        )


@pytest.mark.asyncio
async def test_explore_blocks_mutating_tool_calls():
    """Mutating tool_calls never reach execute_tools."""
    router = MagicMock()
    router.ladder = MagicMock(return_value=["m"])
    router.drain_failover_notices = MagicMock(return_value=[])
    router.chat = AsyncMock(
        return_value=(
            _model("m"),
            _resp(
                "will try write",
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="write_file",
                        arguments={"path": "x", "content": "y"},
                    )
                ],
            ),
        )
    )
    executed: list[str] = []

    async def exec_tools(calls):
        executed.extend(c.name for c in calls)
        return []

    events = EventLog()
    notes = await explore_before_plan(
        task="mutate?",
        router=router,
        tool_specs=[
            ToolSpec(name="write_file", description="w", parameters={}),
            ToolSpec(name="read_file", description="r", parameters={}),
        ],
        execute_tools=exec_tools,
        events=events,
        max_steps=2,
    )
    assert executed == []
    assert "will try write" in notes


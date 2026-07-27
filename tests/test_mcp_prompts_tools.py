"""MCP prompt + reload meta-tools (in-process hub with fake session)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

import pytest

from kageha.harness.approvals import ApprovalGate
from kageha.harness.runtime import HarnessContext
from kageha.harness.sandbox import SessionWorkspace
from kageha.harness.tools.base import Tool, ToolRegistry
from kageha.harness.tools.mcp_tools import register_mcp_tools
from kageha.mcp.client import ConnectedServer, McpHub
from kageha.mcp.config import McpServerConfig
from kageha.mcp.stdio_rpc import McpPromptInfo, McpToolInfo
from kageha.models.registry import ModelRegistry
from kageha.models.router import ModelRouter


@dataclass
class _FakeSession:
    tools: list[McpToolInfo] = field(default_factory=list)
    resources: list = field(default_factory=list)
    prompts: list[McpPromptInfo] = field(default_factory=list)
    closed: bool = False

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        return f"called:{name}:{json.dumps(arguments or {})}"

    async def read_resource(self, uri: str) -> str:
        return f"resource:{uri}"

    async def get_prompt(
        self, name: str, arguments: dict[str, str] | None = None
    ) -> str:
        return json.dumps(
            {
                "description": "d",
                "messages": [
                    {
                        "role": "user",
                        "content": f"{name}:{json.dumps(arguments or {})}",
                    }
                ],
            }
        )

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    ws = SessionWorkspace.create("mcp-prompt-test")
    c = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=True),
        router=ModelRouter(ModelRegistry.load()),
    )
    c.tools = ToolRegistry()
    return c


@pytest.mark.asyncio
async def test_mcp_list_and_get_prompt_tools(ctx, monkeypatch):
    cfg = McpServerConfig(name="demo", command="true")
    hub = McpHub({"demo": cfg})
    session = _FakeSession(
        tools=[McpToolInfo(name="ping")],
        prompts=[
            McpPromptInfo(
                name="greet",
                description="hi",
                arguments=[{"name": "who", "required": True}],
            )
        ],
    )
    hub.connected["demo"] = ConnectedServer(
        config=cfg,
        session=session,  # type: ignore[arg-type]
        tools=session.tools,
        prompts=session.prompts,
    )
    ctx.meta["mcp_hub"] = hub
    ctx.meta["mcp_hub_needs_connect"] = False

    async def _no_connect(c):
        return hub

    monkeypatch.setattr(
        "kageha.harness.tools.mcp_tools.connect_mcp_into_context", _no_connect
    )

    reg = register_mcp_tools(ctx)
    for t in reg.specs():
        ctx.tools.register(reg.tools[t.name])

    listed = await ctx.tools.get("mcp_list_prompts").call(server="demo")
    data = json.loads(listed)
    assert data["prompts"][0]["prompts"][0]["name"] == "greet"

    got = await ctx.tools.get("mcp_get_prompt").call(
        server="demo",
        prompt_name="greet",
        arguments_json='{"who":"Ada"}',
    )
    assert "greet" in got and "Ada" in got


@pytest.mark.asyncio
async def test_mcp_reload_tool_resyncs_remote_tools(ctx, monkeypatch):
    cfg_a = McpServerConfig(name="a", command="true")
    cfg_b = McpServerConfig(name="b", command="true")
    hub = McpHub({"a": cfg_a})
    sess_a = _FakeSession(tools=[McpToolInfo(name="old")])
    hub.connected["a"] = ConnectedServer(
        config=cfg_a,
        session=sess_a,  # type: ignore[arg-type]
        tools=sess_a.tools,
    )
    ctx.meta["mcp_hub"] = hub
    ctx.tools.register(
        Tool(
            name="mcp_a_old",
            description="stale",
            parameters={"type": "object", "properties": {}},
            handler=AsyncMock(return_value="stale"),
            risk_class="mcp",
        )
    )

    async def _reload(servers=None):
        hub.configs = {"b": cfg_b}
        hub.connected.pop("a", None)
        sess_b = _FakeSession(tools=[McpToolInfo(name="new")])
        hub.connected["b"] = ConnectedServer(
            config=cfg_b,
            session=sess_b,  # type: ignore[arg-type]
            tools=sess_b.tools,
        )
        return {
            "added": ["b"],
            "removed": ["a"],
            "changed": [],
            "unchanged": [],
            "errors": {},
            "roots": hub.roots,
        }

    hub.reload = _reload  # type: ignore[method-assign]
    reg = register_mcp_tools(ctx)
    for t in reg.specs():
        ctx.tools.register(reg.tools[t.name])

    out = await ctx.tools.get("mcp_reload").call()
    summary = json.loads(out)
    assert summary["added"] == ["b"]
    assert ctx.tools.get("mcp_a_old") is None
    assert ctx.tools.get("mcp_b_new") is not None

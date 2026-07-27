"""Bridge configured MCP servers into the ToolRegistry."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from kageha.harness.tools.base import Tool, ToolRegistry, tool
from kageha.mcp.client import McpHub
from kageha.mcp.config import ensure_default_mcp_yaml, load_mcp_config

if TYPE_CHECKING:
    from kageha.harness.runtime import HarnessContext

_MCP_META_TOOLS = frozenset(
    {
        "mcp_list_servers",
        "mcp_call",
        "mcp_read_resource",
        "mcp_list_prompts",
        "mcp_get_prompt",
        "mcp_reload",
        "mcp_protocol_status",
    }
)


async def connect_mcp_into_context(ctx: "HarnessContext") -> McpHub:
    """Connect all enabled MCP servers and register their tools on ctx.tools."""
    ensure_default_mcp_yaml()
    hub = ctx.meta.get("mcp_hub")
    if not isinstance(hub, McpHub):
        hub = McpHub(load_mcp_config())
        ctx.meta["mcp_hub"] = hub
    await hub.connect_all()
    ctx.meta["mcp_hub_needs_connect"] = False
    _sync_remote_tools(ctx, hub)
    _record_mcp_warnings(ctx, hub)
    return hub


def _record_mcp_warnings(ctx: "HarnessContext", hub: McpHub) -> None:
    warnings = list(ctx.meta.get("tool_load_warnings") or [])
    for row in hub.status():
        if row.get("enabled") and not row.get("ok") and row.get("error") not in {
            "disabled",
            "",
        }:
            msg = f"mcp:{row['name']}: {row.get('error')}"
            if msg not in warnings:
                warnings.append(msg)
    ctx.meta["tool_load_warnings"] = warnings


def register_mcp_tools(ctx: "HarnessContext") -> ToolRegistry:
    """Register MCP meta-tools; remote tools attach after ``connect_mcp_into_context``."""
    reg = ToolRegistry()
    ensure_default_mcp_yaml()
    if "mcp_hub" not in ctx.meta:
        ctx.meta["mcp_hub"] = McpHub(load_mcp_config())
        ctx.meta["mcp_hub_needs_connect"] = True

    @tool(
        description=(
            "List configured MCP servers and their tools/resources/prompts. "
            "Connects servers on first use if needed."
        )
    )
    async def mcp_list_servers() -> str:
        hub = await connect_mcp_into_context(ctx)
        detail = []
        for name, conn in hub.connected.items():
            detail.append(
                {
                    "name": name,
                    "ok": conn.ok,
                    "error": conn.error,
                    "tools": [t.name for t in conn.tools],
                    "resources": [r.uri for r in conn.resources[:20]],
                    "prompts": [p.name for p in conn.prompts[:20]],
                }
            )
        return json.dumps(
            {"servers": detail, "roots": list(hub.roots)}, indent=2
        )

    @tool(
        description=(
            "Call an MCP tool by server + tool name. "
            "Prefer direct mcp_<server>_<tool> tools when available. "
            "arguments_json is a JSON object."
        ),
        risk_class="mcp",
    )
    async def mcp_call(server: str, tool_name: str, arguments_json: str = "{}") -> str:
        hub = await connect_mcp_into_context(ctx)
        try:
            args = json.loads(arguments_json or "{}")
            if not isinstance(args, dict):
                return "ERROR: arguments_json must be a JSON object"
        except json.JSONDecodeError as e:
            return f"ERROR: bad arguments_json: {e}"
        try:
            return await hub.call_tool(server, tool_name, args)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: mcp_call failed: {e}"

    @tool(
        description="Read an MCP resource URI from a connected server.",
        risk_class="mcp",
    )
    async def mcp_read_resource(server: str, uri: str) -> str:
        hub = await connect_mcp_into_context(ctx)
        try:
            return await hub.read_resource(server, uri)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: mcp_read_resource failed: {e}"

    @tool(
        description=(
            "List MCP prompts on a connected server (or all servers if server is empty). "
            "Prompts are reusable prompt templates exposed by the MCP server."
        ),
        risk_class="mcp",
    )
    async def mcp_list_prompts(server: str = "") -> str:
        hub = await connect_mcp_into_context(ctx)
        rows: list[dict[str, Any]] = []
        names = [server] if server.strip() else list(hub.connected)
        for name in names:
            conn = hub.connected.get(name)
            if not conn:
                rows.append({"server": name, "error": "not connected"})
                continue
            rows.append(
                {
                    "server": name,
                    "ok": conn.ok,
                    "error": conn.error,
                    "prompts": [
                        {
                            "name": p.name,
                            "description": p.description,
                            "arguments": p.arguments,
                        }
                        for p in conn.prompts
                    ],
                }
            )
        return json.dumps({"prompts": rows}, indent=2)

    @tool(
        description=(
            "Get an MCP prompt by server + prompt name. "
            "arguments_json is a JSON object of string values for prompt arguments."
        ),
        risk_class="mcp",
    )
    async def mcp_get_prompt(
        server: str,
        prompt_name: str,
        arguments_json: str = "{}",
    ) -> str:
        hub = await connect_mcp_into_context(ctx)
        try:
            args = json.loads(arguments_json or "{}")
            if not isinstance(args, dict):
                return "ERROR: arguments_json must be a JSON object"
            str_args = {str(k): str(v) for k, v in args.items()}
        except json.JSONDecodeError as e:
            return f"ERROR: bad arguments_json: {e}"
        try:
            return await hub.get_prompt(server, prompt_name, str_args)
        except Exception as e:  # noqa: BLE001
            return f"ERROR: mcp_get_prompt failed: {e}"

    @tool(
        description=(
            "Hot-reload MCP server configs from mcp.yaml layers without restarting "
            "the agent run. Reconnects added/changed servers; drops removed ones; "
            "re-registers mcp_<server>_<tool> tools. Fail-soft on per-server errors."
        ),
        risk_class="mcp",
    )
    async def mcp_reload() -> str:
        ensure_default_mcp_yaml()
        hub = ctx.meta.get("mcp_hub")
        if not isinstance(hub, McpHub):
            hub = McpHub(load_mcp_config())
            ctx.meta["mcp_hub"] = hub
        try:
            summary = await hub.reload()
        except Exception as e:  # noqa: BLE001
            return f"ERROR: mcp_reload failed: {e}"
        ctx.meta["mcp_hub_needs_connect"] = False
        _sync_remote_tools(ctx, hub)
        _record_mcp_warnings(ctx, hub)
        return json.dumps(summary, indent=2)

    @tool(
        description=(
            "Report which MCP protocol surfaces Kageha supports in the trimmed "
            "harness (tools/resources/prompts/roots vs sampling/elicitation/…)."
        ),
        risk_class="safe",
    )
    async def mcp_protocol_status() -> str:
        return json.dumps(
            {
                "supported": [
                    "tools",
                    "resources",
                    "prompts",
                    "roots",
                    "stdio_client",
                    "sse_http_client_optional",
                    "stdio_serve",
                    "hot_reload",
                ],
                "intentionally_unsupported": [
                    {
                        "feature": "sampling",
                        "reason": "trimmed harness — use model router directly",
                    },
                    {
                        "feature": "elicitation",
                        "reason": "trimmed harness — use ask_human",
                    },
                    {
                        "feature": "completions",
                        "reason": "trimmed harness — not exposed as agent tools",
                    },
                    {
                        "feature": "http_sse_serve",
                        "reason": "mcp serve remains stdio-only",
                    },
                ],
                "binary_content": "text_stub",
            },
            indent=2,
        )

    for t in (
        mcp_list_servers,
        mcp_call,
        mcp_read_resource,
        mcp_list_prompts,
        mcp_get_prompt,
        mcp_reload,
        mcp_protocol_status,
    ):
        if hasattr(t, "name"):
            reg.register(t)  # type: ignore[arg-type]
    return reg


def _sync_remote_tools(ctx: "HarnessContext", hub: McpHub) -> None:
    """Drop stale remote MCP tools, then register tools from connected servers."""
    for name in list(ctx.tools.names()):
        if name.startswith("mcp_") and name not in _MCP_META_TOOLS:
            ctx.tools.unregister(name)
    _register_remote_tools(ctx, hub)


def _register_remote_tools(ctx: "HarnessContext", hub: McpHub) -> None:
    for name, conn in hub.connected.items():
        if not conn.ok or not conn.session:
            continue
        prefix = conn.config.tool_prefix()
        risk = conn.config.risk_class or "mcp"
        for info in conn.tools:
            tool_name = f"{prefix}{info.name}"
            if ctx.tools.get(tool_name):
                continue
            schema = info.input_schema or {"type": "object", "properties": {}}
            server_name = name
            remote_name = info.name
            session = conn.session

            props = schema.get("properties") if isinstance(schema, dict) else {}
            required = schema.get("required") if isinstance(schema, dict) else []
            parameters = {
                "type": "object",
                "properties": dict(props) if isinstance(props, dict) else {},
                "required": list(required) if isinstance(required, list) else [],
            }
            parameters["properties"]["arguments_json"] = {
                "type": "string",
                "description": "Optional JSON object of arguments (escape hatch)",
            }

            async def _flex(
                __server=server_name,
                __remote=remote_name,
                __session=session,
                arguments_json: str = "",
                **kwargs: Any,
            ) -> str:
                args = dict(kwargs)
                if arguments_json:
                    try:
                        parsed = json.loads(arguments_json)
                        if isinstance(parsed, dict):
                            args.update(parsed)
                    except json.JSONDecodeError:
                        pass
                args.pop("arguments_json", None)
                try:
                    return await __session.call_tool(__remote, args)
                except Exception as e:  # noqa: BLE001
                    return f"ERROR: MCP {__server}.{__remote}: {e}"

            ctx.tools.register(
                Tool(
                    name=tool_name,
                    description=f"[mcp:{name}] {info.description or info.name}",
                    parameters=parameters,
                    handler=_flex,
                    risk_class=risk,
                )
            )

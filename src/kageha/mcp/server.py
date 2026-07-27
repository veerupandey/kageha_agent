"""Expose Kageha tools as an MCP server (stdio)."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from kageha import __version__


async def run_mcp_server(*, auto_approve: bool = True) -> None:
    """Serve the current ToolRegistry over MCP stdio.

    Prefers the official ``mcp`` SDK low-level Server (real JSON schemas).
    Falls back to a minimal JSON-RPC stdio server if the SDK is unavailable.
    """
    try:
        await _run_with_sdk(auto_approve=auto_approve)
    except ImportError:
        await _run_minimal(auto_approve=auto_approve)


async def _build_registry(auto_approve: bool):
    from kageha.harness.approvals import ApprovalGate
    from kageha.harness.runtime import HarnessContext
    from kageha.harness.sandbox import SessionWorkspace
    from kageha.harness.tools.builtin import load_entry_point_tools
    from kageha.models.registry import ModelRegistry
    from kageha.models.router import ModelRouter

    ws = SessionWorkspace.create("mcp-server")
    ctx = HarnessContext(
        workspace=ws,
        approvals=ApprovalGate(auto_approve=auto_approve),
        router=ModelRouter(ModelRegistry.load()),
    )
    ctx.tools = load_entry_point_tools(ctx)
    return ctx


async def _run_with_sdk(*, auto_approve: bool) -> None:
    """Serve with official SDK, preserving each tool's JSON Schema."""
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    ctx = await _build_registry(auto_approve)
    server = Server("kageha")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        out: list[types.Tool] = []
        for spec in ctx.tools.specs():
            out.append(
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.parameters
                    or {"type": "object", "properties": {}},
                )
            )
        return out

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[types.TextContent]:
        tool = ctx.tools.get(name)
        if tool is None:
            return [
                types.TextContent(
                    type="text",
                    text=f"ERROR: unknown tool {name}",
                )
            ]
        args = arguments if isinstance(arguments, dict) else {}
        try:
            text = await tool.call(**args)
        except TypeError:
            # Some forged tools accept a single JSON blob
            try:
                text = await tool.call(arguments_json=json.dumps(args))
            except Exception as e:  # noqa: BLE001
                text = f"ERROR: {name}: {e}"
        except Exception as e:  # noqa: BLE001
            text = f"ERROR: {name}: {e}"
        return [types.TextContent(type="text", text=str(text))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


async def _run_minimal(*, auto_approve: bool) -> None:
    """Minimal MCP server: initialize + tools/list + tools/call over Content-Length stdio."""
    ctx = await _build_registry(auto_approve)
    tools = ctx.tools
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)
    # Use thread for blocking stdin if needed — simpler: sync read in executor

    loop = asyncio.get_event_loop()

    def _read_message() -> dict[str, Any] | None:
        # Content-Length framing from stdin.buffer
        while True:
            header = sys.stdin.buffer.readline()
            if not header:
                return None
            if header.lower().startswith(b"content-length:"):
                length = int(header.split(b":", 1)[1].strip())
                while True:
                    line = sys.stdin.buffer.readline()
                    if line in {b"\r\n", b"\n", b""}:
                        break
                body = sys.stdin.buffer.read(length)
                return json.loads(body.decode("utf-8"))
            # NDJSON fallback
            line = header.decode("utf-8", errors="replace").strip()
            if line:
                return json.loads(line)

    def _write_message(msg: dict[str, Any]) -> None:
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()

    while True:
        msg = await loop.run_in_executor(None, _read_message)
        if msg is None:
            break
        if "method" not in msg:
            continue
        method = msg["method"]
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "kageha", "version": __version__},
            }
            _write_message({"jsonrpc": "2.0", "id": req_id, "result": result})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            listed = []
            for spec in tools.specs():
                listed.append(
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "inputSchema": spec.parameters
                        or {"type": "object", "properties": {}},
                    }
                )
            _write_message(
                {"jsonrpc": "2.0", "id": req_id, "result": {"tools": listed}}
            )
        elif method == "tools/call":
            name = str(params.get("name") or "")
            args = params.get("arguments") or {}
            tool = tools.get(name)
            if tool is None:
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": f"ERROR: unknown tool {name}"}],
                            "isError": True,
                        },
                    }
                )
                continue
            try:
                text = await tool.call(**(args if isinstance(args, dict) else {}))
                is_err = text.startswith("ERROR:")
            except Exception as e:  # noqa: BLE001
                text = f"ERROR: {e}"
                is_err = True
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": is_err,
                    },
                }
            )
        elif req_id is not None:
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


def main() -> None:
    asyncio.run(run_mcp_server(auto_approve=True))


if __name__ == "__main__":
    main()

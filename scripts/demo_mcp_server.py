#!/usr/bin/env python3
"""Tiny MCP stdio server for Kageha demos (no npx required)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/tmp/kageha-mcp-demo")
ROOT.mkdir(parents=True, exist_ok=True)


def read_msg():
    while True:
        header = sys.stdin.buffer.readline()
        if not header:
            return None
        if header.lower().startswith(b"content-length:"):
            length = int(header.split(b":", 1)[1].strip())
            while True:
                line = sys.stdin.buffer.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            body = sys.stdin.buffer.read(length)
            return json.loads(body.decode("utf-8"))
        line = header.decode("utf-8", errors="replace").strip()
        if line:
            return json.loads(line)


def write_msg(msg: dict) -> None:
    body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


TOOLS = [
    {
        "name": "list_demo_files",
        "description": "List files in the Kageha MCP demo folder (/tmp/kageha-mcp-demo).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "read_demo_file",
        "description": "Read a text file from the demo folder by name.",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "write_demo_note",
        "description": "Write a note file into the demo folder.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["name", "text"],
        },
    },
    {
        "name": "server_time",
        "description": "Return current UTC time from the MCP server.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def call_tool(name: str, args: dict) -> str:
    if name == "list_demo_files":
        files = sorted(p.name for p in ROOT.iterdir() if p.is_file())
        return json.dumps({"files": files, "root": str(ROOT)})
    if name == "read_demo_file":
        path = ROOT / Path(args.get("name") or "").name
        if not path.is_file():
            return f"ERROR: not found: {path.name}"
        return path.read_text(errors="replace")
    if name == "write_demo_note":
        fname = Path(args.get("name") or "note.txt").name
        path = ROOT / fname
        path.write_text(str(args.get("text") or ""))
        return json.dumps({"ok": True, "path": str(path)})
    if name == "server_time":
        return datetime.now(tz=timezone.utc).isoformat()
    return f"ERROR: unknown tool {name}"


def main() -> None:
    while True:
        msg = read_msg()
        if msg is None:
            break
        method = msg.get("method")
        rid = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            write_msg(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "kageha-demo", "version": "0.1.0"},
                    },
                }
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            write_msg({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "resources/list":
            write_msg({"jsonrpc": "2.0", "id": rid, "result": {"resources": []}})
        elif method == "tools/call":
            tname = str(params.get("name") or "")
            args = params.get("arguments") or {}
            text = call_tool(tname, args if isinstance(args, dict) else {})
            write_msg(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": text.startswith("ERROR:"),
                    },
                }
            )
        elif rid is not None:
            write_msg(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            )


if __name__ == "__main__":
    main()

"""MCP stdio client + config (fake server subprocess)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from kageha.mcp.client import McpHub
from kageha.mcp.config import McpServerConfig, _parse_servers
from kageha.mcp.stdio_rpc import StdioMcpSession


FAKE_SERVER = r"""
import json, sys

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
            return json.loads(body.decode())
        line = header.decode().strip()
        if line:
            return json.loads(line)

def write_msg(msg):
    body = json.dumps(msg).encode()
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode())
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()

# After initialize, request client roots once (tests client roots/list handling)
state = {"roots_requested": False, "last_roots": None}

while True:
    msg = read_msg()
    if msg is None:
        break
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        write_msg({"jsonrpc":"2.0","id":rid,"result":{
            "protocolVersion":"2024-11-05",
            "capabilities":{"tools":{},"prompts":{},"resources":{}},
            "serverInfo":{"name":"fake","version":"0"},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        if not state["roots_requested"]:
            state["roots_requested"] = True
            write_msg({"jsonrpc":"2.0","id":9001,"method":"roots/list","params":{}})
            state["last_roots"] = read_msg()
        write_msg({"jsonrpc":"2.0","id":rid,"result":{"tools":[
            {"name":"echo","description":"Echo text",
             "inputSchema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"]}},
            {"name":"show_roots","description":"Return last roots/list reply",
             "inputSchema":{"type":"object","properties":{}}}
        ]}})
    elif method == "resources/list":
        write_msg({"jsonrpc":"2.0","id":rid,"result":{"resources":[]}})
    elif method == "prompts/list":
        write_msg({"jsonrpc":"2.0","id":rid,"result":{"prompts":[
            {"name":"greet","description":"Say hello",
             "arguments":[{"name":"who","description":"Name","required":True}]}
        ]}})
    elif method == "prompts/get":
        params = msg.get("params") or {}
        args = params.get("arguments") or {}
        who = args.get("who", "world")
        write_msg({"jsonrpc":"2.0","id":rid,"result":{
            "description":"greeting",
            "messages":[{"role":"user","content":{"type":"text","text":f"Hello {who}"}}]
        }})
    elif method == "tools/call":
        params = msg.get("params") or {}
        tname = params.get("name")
        args = params.get("arguments") or {}
        if tname == "show_roots":
            text = json.dumps(state.get("last_roots") or {})
        else:
            text = "echo:" + str(args.get("text", ""))
        write_msg({"jsonrpc":"2.0","id":rid,"result":{
            "content":[{"type":"text","text":text}]
        }})
    elif rid is not None:
        write_msg({"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":method}})
"""


@pytest.fixture()
def fake_server_script(tmp_path: Path) -> Path:
    p = tmp_path / "fake_mcp_server.py"
    p.write_text(FAKE_SERVER)
    return p


def test_parse_claude_desktop_shape():
    data = {
        "mcpServers": {
            "fs": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            }
        }
    }
    servers = _parse_servers(data)
    assert "fs" in servers
    assert servers["fs"].command == "npx"
    assert servers["fs"].transport == "stdio"


def test_stdio_session_lists_and_calls(fake_server_script: Path):
    async def _run():
        session = StdioMcpSession(sys.executable, [str(fake_server_script)])
        await session.start()
        try:
            assert any(t.name == "echo" for t in session.tools)
            out = await session.call_tool("echo", {"text": "hi"})
            assert "echo:hi" in out
        finally:
            await session.close()

    asyncio.run(_run())


def test_stdio_prompts_list_and_get(fake_server_script: Path, tmp_path: Path):
    async def _run():
        session = StdioMcpSession(
            sys.executable,
            [str(fake_server_script)],
            roots=[str(tmp_path)],
        )
        await session.start()
        try:
            assert any(p.name == "greet" for p in session.prompts)
            out = await session.get_prompt("greet", {"who": "Ada"})
            data = json.loads(out)
            assert data["description"] == "greeting"
            assert "Hello Ada" in data["messages"][0]["content"]
            roots_blob = await session.call_tool("show_roots", {})
            assert "file://" in roots_blob
            assert tmp_path.name in roots_blob or str(tmp_path) in roots_blob
        finally:
            await session.close()

    asyncio.run(_run())


def test_hub_connects_fake(tmp_path, monkeypatch, fake_server_script: Path):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    cfg = {
        "fake": McpServerConfig(
            name="fake",
            command=sys.executable,
            args=[str(fake_server_script)],
        )
    }

    async def _run():
        hub = McpHub(cfg, roots=[str(tmp_path)])
        await hub.connect_all()
        try:
            st = hub.status()
            assert st[0]["ok"] is True
            assert st[0]["tools"] == 2
            assert st[0]["prompts"] == 1
            out = await hub.call_tool("fake", "echo", {"text": "ok"})
            assert "echo:ok" in out
            prompt = await hub.get_prompt("fake", "greet", {"who": "Kageha"})
            assert "Hello Kageha" in prompt
        finally:
            await hub.close()

    asyncio.run(_run())


def test_hub_hot_reload(tmp_path, monkeypatch, fake_server_script: Path):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    cfg_a = {
        "fake": McpServerConfig(
            name="fake",
            command=sys.executable,
            args=[str(fake_server_script)],
        )
    }
    # Second "server" is the same fake under a new name; removal of fake tests drop.
    cfg_b = {
        "other": McpServerConfig(
            name="other",
            command=sys.executable,
            args=[str(fake_server_script)],
        )
    }

    async def _run():
        hub = McpHub(cfg_a, roots=[str(tmp_path)])
        await hub.connect_all()
        assert hub.connected["fake"].ok
        summary = await hub.reload(cfg_b)
        assert summary["removed"] == ["fake"]
        assert summary["added"] == ["other"]
        assert "fake" not in hub.connected
        assert hub.connected["other"].ok
        out = await hub.call_tool("other", "echo", {"text": "reload"})
        assert "echo:reload" in out
        await hub.close()

    asyncio.run(_run())


HUNG_SERVER = r"""
import time
time.sleep(120)
"""


def test_hung_mcp_server_fails_fast(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("KAGEHA_HOME", str(tmp_path))
    monkeypatch.setenv("KAGEHA_MCP_TIMEOUT", "1")
    hung = tmp_path / "hung_mcp.py"
    hung.write_text(HUNG_SERVER)
    cfg = {
        "hung": McpServerConfig(
            name="hung",
            command=sys.executable,
            args=[str(hung)],
        )
    }

    async def _run():
        hub = McpHub(cfg)
        await hub.connect_all()
        try:
            st = {r["name"]: r for r in hub.status()}
            assert st["hung"]["ok"] is False
            assert "timeout" in (st["hung"].get("error") or "").lower()
        finally:
            await hub.close()

    asyncio.run(_run())

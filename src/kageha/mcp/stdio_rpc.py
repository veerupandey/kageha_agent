"""Minimal MCP JSON-RPC client over stdio (no SDK required)."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha import __version__


def mcp_timeout_seconds() -> float:
    """Per-request timeout for MCP stdio (env ``KAGEHA_MCP_TIMEOUT``, default 20s)."""
    raw = (os.environ.get("KAGEHA_MCP_TIMEOUT") or "20").strip()
    try:
        return max(1.0, float(raw))
    except ValueError:
        return 20.0


@dataclass
class McpToolInfo:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpResourceInfo:
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


@dataclass
class McpPromptInfo:
    name: str
    description: str = ""
    arguments: list[dict[str, Any]] = field(default_factory=list)


class StdioMcpSession:
    """Speak MCP initialize / tools / resources / prompts over a subprocess."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        roots: list[str] | None = None,
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env or {}
        self.cwd = cwd or None
        self.roots = list(roots or [])
        self._proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._lock = asyncio.Lock()
        self.server_name = ""
        self.tools: list[McpToolInfo] = []
        self.resources: list[McpResourceInfo] = []
        self.prompts: list[McpPromptInfo] = []

    async def start(self) -> None:
        if self._proc is not None:
            return
        merged = os.environ.copy()
        merged.update(self.env)
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=merged,
            cwd=self.cwd,
        )
        try:
            # MCP initialize handshake (bounded — hung npx servers must not block runs)
            capabilities: dict[str, Any] = {}
            if self.roots:
                capabilities["roots"] = {"listChanged": True}
            init = await self.request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": capabilities,
                    "clientInfo": {"name": "kageha", "version": __version__},
                },
            )
            self.server_name = str(
                ((init or {}).get("serverInfo") or {}).get("name") or ""
            )
            await self.notify("notifications/initialized", {})
            await self.refresh()
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except Exception:  # noqa: BLE001
            pass

    async def refresh(self) -> None:
        try:
            tools = await self.request("tools/list", {})
            self.tools = [
                McpToolInfo(
                    name=str(t.get("name") or ""),
                    description=str(t.get("description") or ""),
                    input_schema=dict(t.get("inputSchema") or {"type": "object"}),
                )
                for t in (tools or {}).get("tools") or []
                if t.get("name")
            ]
        except Exception:  # noqa: BLE001
            self.tools = []
        try:
            resources = await self.request("resources/list", {})
            self.resources = [
                McpResourceInfo(
                    uri=str(r.get("uri") or ""),
                    name=str(r.get("name") or ""),
                    description=str(r.get("description") or ""),
                    mime_type=str(r.get("mimeType") or ""),
                )
                for r in (resources or {}).get("resources") or []
                if r.get("uri")
            ]
        except Exception:  # noqa: BLE001
            self.resources = []
        try:
            prompts = await self.request("prompts/list", {})
            self.prompts = [
                McpPromptInfo(
                    name=str(p.get("name") or ""),
                    description=str(p.get("description") or ""),
                    arguments=[
                        dict(a)
                        for a in (p.get("arguments") or [])
                        if isinstance(a, dict)
                    ],
                )
                for p in (prompts or {}).get("prompts") or []
                if p.get("name")
            ]
        except Exception:  # noqa: BLE001
            self.prompts = []

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        result = await self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return _format_tool_result(result)

    async def read_resource(self, uri: str) -> str:
        result = await self.request("resources/read", {"uri": uri})
        contents = (result or {}).get("contents") or []
        parts: list[str] = []
        for c in contents:
            if c.get("text") is not None:
                parts.append(str(c["text"]))
            elif c.get("blob") is not None:
                parts.append(f"[blob mime={c.get('mimeType','')} len={len(c.get('blob') or '')}]")
        return "\n".join(parts) if parts else json.dumps(result)[:8000]

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> str:
        result = await self.request(
            "prompts/get",
            {"name": name, "arguments": arguments or {}},
        )
        return _format_prompt_result(result)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        await self._write(msg)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        timeout = mcp_timeout_seconds()
        async with self._lock:
            self._id += 1
            req_id = self._id
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
                "params": params or {},
            }
            await self._write(msg)
            try:
                return await asyncio.wait_for(
                    self._read_response(req_id, method),
                    timeout=timeout,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"MCP timeout after {timeout:.0f}s during {method} "
                    f"(set KAGEHA_MCP_TIMEOUT to raise)"
                ) from exc

    async def _read_response(self, req_id: int, method: str) -> Any:
        while True:
            raw = await self._readline()
            if raw is None:
                raise RuntimeError(f"MCP server closed during {method}")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # Server → client request (e.g. roots/list) interleaved with our reply
            if (
                "method" in data
                and "id" in data
                and "result" not in data
                and "error" not in data
            ):
                await self._handle_server_request(data)
                continue
            # Skip notifications / progress
            if "id" not in data:
                continue
            if data.get("id") != req_id:
                continue
            if "error" in data:
                err = data["error"]
                raise RuntimeError(
                    f"MCP error {err.get('code')}: {err.get('message')}"
                )
            return data.get("result")

    async def _handle_server_request(self, data: dict[str, Any]) -> None:
        method = str(data.get("method") or "")
        req_id = data.get("id")
        if method == "roots/list":
            await self._write(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"roots": _roots_payload(self.roots)},
                }
            )
            return
        await self._write(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        )

    async def _write(self, msg: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("MCP session not started")
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        # Prefer Content-Length framing (MCP stdio spec); also works with NDJSON servers
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)
        await self._proc.stdin.drain()

    async def _readline(self) -> str | None:
        if not self._proc or not self._proc.stdout:
            return None
        # Support Content-Length framing and newline-delimited JSON
        header = await self._proc.stdout.readline()
        if not header:
            return None
        text = header.decode("utf-8", errors="replace")
        if text.lower().startswith("content-length:"):
            try:
                length = int(text.split(":", 1)[1].strip())
            except ValueError:
                return None
            # consume headers until blank line
            while True:
                h = await self._proc.stdout.readline()
                if not h or h in {b"\r\n", b"\n"}:
                    break
            body = await self._proc.stdout.readexactly(length)
            return body.decode("utf-8", errors="replace")
        return text.strip()


def _roots_payload(roots: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in roots:
        try:
            path = Path(raw).expanduser().resolve()
        except Exception:  # noqa: BLE001
            continue
        out.append({"uri": path.as_uri(), "name": path.name or str(path)})
    return out


def _format_tool_result(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            if block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
            else:
                parts.append(json.dumps(block)[:2000])
        text = "\n".join(p for p in parts if p)
        if result.get("isError"):
            return f"ERROR: {text}"
        return text
    return json.dumps(result)[:12000]


def _format_prompt_result(result: Any) -> str:
    """Normalize prompts/get result (stdio dict or SDK-like object) to JSON text."""
    if result is None:
        return "{}"
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        description = getattr(result, "description", None) or ""
        messages = []
        for msg in getattr(result, "messages", None) or []:
            role = getattr(msg, "role", "") or ""
            content = getattr(msg, "content", None)
            text = getattr(content, "text", None)
            if text is None:
                text = str(content)
            messages.append({"role": role, "content": text})
        return json.dumps(
            {"description": description, "messages": messages},
            indent=2,
        )[:12000]
    messages_out: list[dict[str, str]] = []
    for msg in result.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, dict):
            text = str(content.get("text") or json.dumps(content)[:2000])
        else:
            text = str(content or "")
        messages_out.append({"role": str(msg.get("role") or ""), "content": text})
    return json.dumps(
        {
            "description": str(result.get("description") or ""),
            "messages": messages_out,
        },
        indent=2,
    )[:12000]

"""MCP hub — connect configured servers and expose tools/resources/prompts."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kageha.config import expand_home
from kageha.mcp.config import McpServerConfig, load_mcp_config
from kageha.mcp.stdio_rpc import (
    McpPromptInfo,
    McpResourceInfo,
    McpToolInfo,
    StdioMcpSession,
    _format_prompt_result,
)


def default_mcp_roots() -> list[str]:
    """Filesystem roots advertised to MCP servers (``KAGEHA_MCP_ROOTS`` or cwd)."""
    raw = (os.environ.get("KAGEHA_MCP_ROOTS") or "").strip()
    if raw:
        out: list[str] = []
        for part in raw.split(":"):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(str(expand_home(part)))
            except Exception:  # noqa: BLE001
                continue
        if out:
            return out
    return [str(Path.cwd().resolve())]


def _cfg_fingerprint(cfg: McpServerConfig) -> tuple[Any, ...]:
    return (
        cfg.transport,
        cfg.command,
        tuple(cfg.args),
        tuple(sorted(cfg.env.items())),
        cfg.cwd,
        cfg.url,
        cfg.enabled,
        cfg.prefix,
        cfg.risk_class,
    )


@dataclass
class ConnectedServer:
    config: McpServerConfig
    session: StdioMcpSession | None = None
    error: str = ""
    tools: list[McpToolInfo] = field(default_factory=list)
    resources: list[McpResourceInfo] = field(default_factory=list)
    prompts: list[McpPromptInfo] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.session is not None and not self.error


class McpHub:
    """Lifecycle manager for MCP servers for one agent run / CLI session."""

    def __init__(
        self,
        servers: dict[str, McpServerConfig] | None = None,
        *,
        roots: list[str] | None = None,
    ) -> None:
        self.configs = servers if servers is not None else load_mcp_config()
        self.connected: dict[str, ConnectedServer] = {}
        self.roots = list(roots) if roots is not None else default_mcp_roots()

    async def connect_all(self) -> dict[str, ConnectedServer]:
        for name, cfg in self.configs.items():
            if not cfg.enabled:
                self.connected[name] = ConnectedServer(
                    config=cfg, error="disabled"
                )
                continue
            await self.connect(name)
        return self.connected

    async def connect(self, name: str) -> ConnectedServer:
        cfg = self.configs.get(name)
        if cfg is None:
            raise KeyError(f"unknown MCP server {name!r}")
        # Close previous
        prev = self.connected.get(name)
        if prev and prev.session:
            await prev.session.close()

        if cfg.transport in {
            "sse",
            "http",
            "streamable_http",
            "streamable-http",
            "streamable",
        }:
            conn = await self._connect_httpish(cfg)
            self.connected[name] = conn
            return conn

        if not cfg.command:
            conn = ConnectedServer(config=cfg, error="missing command for stdio transport")
            self.connected[name] = conn
            return conn

        from kageha.mcp.stdio_rpc import mcp_timeout_seconds

        session = StdioMcpSession(
            cfg.command,
            cfg.args,
            env=cfg.env,
            cwd=cfg.cwd or None,
            roots=self.roots,
        )
        # Outer budget: initialize + tools/list + resources/list + prompts/list
        budget = max(5.0, mcp_timeout_seconds() * 4)
        try:
            await asyncio.wait_for(session.start(), timeout=budget)
            conn = ConnectedServer(
                config=cfg,
                session=session,
                tools=list(session.tools),
                resources=list(session.resources),
                prompts=list(session.prompts),
            )
        except Exception as e:  # noqa: BLE001
            await session.close()
            conn = ConnectedServer(config=cfg, error=str(e))
        self.connected[name] = conn
        return conn

    async def _connect_httpish(self, cfg: McpServerConfig) -> ConnectedServer:
        """SSE or Streamable HTTP via official mcp SDK when installed."""
        if not cfg.url:
            return ConnectedServer(config=cfg, error="missing url for sse/http transport")
        transport = (cfg.transport or "sse").strip().lower()
        need_streamable = transport in {
            "http",
            "streamable_http",
            "streamable-http",
            "streamable",
        }
        try:
            __import__("mcp")
            if need_streamable:
                __import__("mcp.client.streamable_http")
            else:
                __import__("mcp.client.sse")
        except Exception as e:  # noqa: BLE001
            return ConnectedServer(
                config=cfg,
                error=(
                    f"Remote MCP ({transport}) requires the optional 'mcp' package ({e}). "
                    "Install: uv sync --extra mcp"
                ),
            )
        try:
            wrapper = _SdkHttpSession(
                cfg.url, transport=transport, roots=self.roots
            )
            await wrapper.start()
            return ConnectedServer(
                config=cfg,
                session=wrapper,  # type: ignore[arg-type]
                tools=list(wrapper.tools),
                resources=list(wrapper.resources),
                prompts=list(wrapper.prompts),
            )
        except Exception as e:  # noqa: BLE001
            return ConnectedServer(config=cfg, error=str(e))

    async def reload(
        self,
        servers: dict[str, McpServerConfig] | None = None,
    ) -> dict[str, Any]:
        """Reload MCP server configs mid-session (hot-reload).

        Reconnects added/changed servers, closes removed ones. Fail-soft per
        server (same timeouts as ``connect``). Pass ``servers`` to inject a
        config map (tests); otherwise re-reads layered ``mcp.yaml`` paths.
        """
        new_configs = servers if servers is not None else load_mcp_config()
        old_configs = self.configs
        old_names = set(old_configs)
        new_names = set(new_configs)

        added = sorted(new_names - old_names)
        removed = sorted(old_names - new_names)
        changed: list[str] = []
        unchanged: list[str] = []
        for name in sorted(old_names & new_names):
            if _cfg_fingerprint(old_configs[name]) != _cfg_fingerprint(
                new_configs[name]
            ):
                changed.append(name)
            else:
                unchanged.append(name)

        self.configs = new_configs

        for name in removed:
            prev = self.connected.pop(name, None)
            if prev and prev.session:
                try:
                    await prev.session.close()
                except Exception:  # noqa: BLE001
                    pass

        reconnect = added + changed
        errors: dict[str, str] = {}
        for name in reconnect:
            cfg = new_configs[name]
            if not cfg.enabled:
                self.connected[name] = ConnectedServer(config=cfg, error="disabled")
                continue
            conn = await self.connect(name)
            if not conn.ok and conn.error not in {"disabled", ""}:
                errors[name] = conn.error

        # Drop stale connected entries that are no longer configured
        for name in list(self.connected):
            if name not in new_configs:
                prev = self.connected.pop(name)
                if prev.session:
                    try:
                        await prev.session.close()
                    except Exception:  # noqa: BLE001
                        pass

        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "unchanged": unchanged,
            "errors": errors,
            "roots": list(self.roots),
        }

    async def close(self) -> None:
        for conn in self.connected.values():
            if conn.session:
                try:
                    await conn.session.close()
                except Exception:  # noqa: BLE001
                    pass
        self.connected.clear()

    def status(self) -> list[dict[str, Any]]:
        rows = []
        for name, cfg in self.configs.items():
            conn = self.connected.get(name)
            rows.append(
                {
                    "name": name,
                    "transport": cfg.transport,
                    "enabled": cfg.enabled,
                    "ok": bool(conn and conn.ok),
                    "error": conn.error if conn else "",
                    "tools": len(conn.tools) if conn else 0,
                    "resources": len(conn.resources) if conn else 0,
                    "prompts": len(conn.prompts) if conn else 0,
                    "command": cfg.command,
                    "url": cfg.url,
                }
            )
        return rows

    async def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> str:
        conn = self.connected.get(server)
        if not conn or not conn.session:
            raise RuntimeError(f"MCP server {server!r} not connected: {conn.error if conn else 'missing'}")
        return await conn.session.call_tool(tool, arguments)

    async def read_resource(self, server: str, uri: str) -> str:
        conn = self.connected.get(server)
        if not conn or not conn.session:
            raise RuntimeError(f"MCP server {server!r} not connected")
        return await conn.session.read_resource(uri)

    async def get_prompt(
        self,
        server: str,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> str:
        conn = self.connected.get(server)
        if not conn or not conn.session:
            raise RuntimeError(f"MCP server {server!r} not connected")
        return await conn.session.get_prompt(name, arguments)


class _SdkHttpSession:
    """Thin adapter so HTTP/SSE sessions look like StdioMcpSession.

    Transports:
    - ``sse`` — legacy SSE (`mcp.client.sse.sse_client`)
    - ``http`` / ``streamable_http`` / ``streamable-http`` — Streamable HTTP
    """

    def __init__(
        self,
        url: str,
        *,
        transport: str = "sse",
        roots: list[str] | None = None,
    ) -> None:
        self.url = url
        self.transport = (transport or "sse").strip().lower()
        self.roots = list(roots or [])
        self.tools: list[McpToolInfo] = []
        self.resources: list[McpResourceInfo] = []
        self.prompts: list[McpPromptInfo] = []
        self._cm = None
        self._session = None
        self._stack = None

    def _use_streamable(self) -> bool:
        return self.transport in {"http", "streamable_http", "streamable-http", "streamable"}

    async def start(self) -> None:
        from contextlib import AsyncExitStack

        from mcp import ClientSession
        from mcp import types as mcp_types
        from mcp.shared.context import RequestContext

        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        if self._use_streamable():
            try:
                from mcp.client.streamable_http import streamablehttp_client
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"Streamable HTTP requires mcp>=1.6 with streamable_http ({e}). "
                    "Install: uv sync --extra mcp"
                ) from e
            # streamablehttp_client yields (read, write, get_session_id)
            streams = await self._stack.enter_async_context(
                streamablehttp_client(self.url)
            )
            read, write = streams[0], streams[1]
        else:
            from mcp.client.sse import sse_client

            read, write = await self._stack.enter_async_context(sse_client(self.url))

        roots_snapshot = list(self.roots)

        async def _list_roots(
            _context: RequestContext[ClientSession, Any],
        ) -> mcp_types.ListRootsResult | mcp_types.ErrorData:
            payload = []
            for raw in roots_snapshot:
                try:
                    path = Path(raw).expanduser().resolve()
                    payload.append(
                        mcp_types.Root(
                            uri=path.as_uri(),  # type: ignore[arg-type]
                            name=path.name or str(path),
                        )
                    )
                except Exception:  # noqa: BLE001
                    continue
            return mcp_types.ListRootsResult(roots=payload)

        session_kwargs: dict[str, Any] = {}
        if roots_snapshot:
            session_kwargs["list_roots_callback"] = _list_roots

        session = await self._stack.enter_async_context(
            ClientSession(read, write, **session_kwargs)
        )
        await session.initialize()
        self._session = session
        listed = await session.list_tools()
        self.tools = [
            McpToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=dict(t.inputSchema or {"type": "object"}),
            )
            for t in listed.tools
        ]
        try:
            res = await session.list_resources()
            self.resources = [
                McpResourceInfo(
                    uri=str(r.uri),
                    name=getattr(r, "name", "") or "",
                    description=getattr(r, "description", "") or "",
                    mime_type=getattr(r, "mimeType", "") or "",
                )
                for r in res.resources
            ]
        except Exception:  # noqa: BLE001
            self.resources = []
        try:
            prompts = await session.list_prompts()
            self.prompts = [
                McpPromptInfo(
                    name=p.name,
                    description=getattr(p, "description", "") or "",
                    arguments=[
                        {
                            "name": a.name,
                            "description": getattr(a, "description", None) or "",
                            "required": bool(getattr(a, "required", False)),
                        }
                        for a in (getattr(p, "arguments", None) or [])
                    ],
                )
                for p in prompts.prompts
            ]
        except Exception:  # noqa: BLE001
            self.prompts = []

    async def close(self) -> None:
        if self._stack:
            await self._stack.__aexit__(None, None, None)
        self._stack = None
        self._session = None

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        assert self._session
        result = await self._session.call_tool(name, arguments or {})
        # Normalize SDK result
        parts = []
        for block in getattr(result, "content", []) or []:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
            else:
                parts.append(str(block))
        out = "\n".join(parts)
        if getattr(result, "isError", False):
            return f"ERROR: {out}"
        return out

    async def read_resource(self, uri: str) -> str:
        from pydantic import AnyUrl

        assert self._session
        result = await self._session.read_resource(AnyUrl(uri))
        parts = []
        for c in getattr(result, "contents", []) or []:
            text = getattr(c, "text", None)
            if text is not None:
                parts.append(text)
        return "\n".join(parts) if parts else json.dumps({"uri": uri})

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> str:
        assert self._session
        result = await self._session.get_prompt(name, arguments)
        return _format_prompt_result(result)

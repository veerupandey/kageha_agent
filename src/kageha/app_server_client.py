"""Attachable App Server client (unix / loopback WebSocket).

Used by WebUI and CLI so multiple surfaces share one long-lived daemon.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlparse

from kageha.app_server_listen import default_unix_socket, parse_listen_url


class RemoteAppServer:
    """Drop-in async ``handle`` client for a listening App Server.

    Also exposes local ``threads`` / ``memory`` / ``runtime`` mirrors so the
    WebUI can keep using the same attribute surface as in-process AppServer
    (session files and SQLite live under the shared ``~/.kageha`` home).
    """

    def __init__(self, listen: str) -> None:
        kind, target = parse_listen_url(listen)
        if kind == "stdio":
            raise ValueError("RemoteAppServer cannot attach to stdio://")
        self.kind = kind
        self.target = target
        self._closed = False
        self.threads: dict[str, dict[str, Any]] = {}
        self._memory = None
        self._runtime = None

    @property
    def memory(self):
        if self._memory is None:
            from kageha.memory.service import get_memory_service

            self._memory = get_memory_service(start_worker=True)
        return self._memory

    @property
    def runtime(self):
        if self._runtime is None:
            from kageha.runtime import AgentRuntime

            self._runtime = AgentRuntime()
        return self._runtime

    def close(self) -> None:
        self._closed = True
        if self._runtime is not None:
            try:
                self._runtime.close()
            except Exception:  # noqa: BLE001
                pass
        if self._memory is not None:
            try:
                self._memory.stop_worker(timeout=2.0)
            except Exception:  # noqa: BLE001
                pass

    async def handle(self, req: dict[str, Any]) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("RemoteAppServer is closed")
        if self.kind == "unix":
            return await _rpc_unix(self.target, req)
        if self.kind == "ws":
            return await _rpc_ws(self.target, req)
        raise ValueError(f"unsupported attach transport: {self.kind}")


def resolve_attach_url(raw: str | None) -> str | None:
    """Normalize attach URL; bare ``unix://`` / ``auto`` expands to default socket."""
    text = (raw or "").strip()
    if not text:
        return None
    if text in {"auto", "daemon", "default"}:
        return f"unix://{default_unix_socket()}"
    kind, target = parse_listen_url(text)
    if kind == "stdio":
        raise ValueError(
            "cannot attach to stdio://; use unix:// or ws://127.0.0.1:PORT"
        )
    if kind == "unix":
        return f"unix://{target}"
    return target


def open_app_server(attach: str | None = None):
    """Return local AppServer or RemoteAppServer based on ``attach``."""
    url = resolve_attach_url(attach)
    if url is None:
        from kageha.app_server import AppServer

        return AppServer()
    return RemoteAppServer(url)


async def _rpc_unix(path: str, request: dict[str, Any]) -> dict[str, Any]:
    from kageha.app_server_listen import rpc_over_unix

    return await rpc_over_unix(path, request)


async def _rpc_ws(url: str, request: dict[str, Any]) -> dict[str, Any]:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "WebSocket attach requires websockets. "
            "Install: uv sync --extra server-ws"
        ) from exc
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ws attach is restricted to loopback hosts")
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps(request))
        raw = await asyncio.wait_for(ws.recv(), timeout=600)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw)

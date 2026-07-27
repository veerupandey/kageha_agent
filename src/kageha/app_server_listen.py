"""Multi-transport App Server listeners (stdio, Unix socket, WebSocket).

Wire protocol matches stdio: one JSON-RPC request object per message,
one JSON-RPC response object per reply. WebSocket/UDS send text frames /
newline-delimited JSON.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from kageha.app_server import AppServer
from kageha.config import kageha_home

# NDJSON JSON-RPC over Unix sockets uses asyncio.StreamReader.readline().
# Default limit is 64 KiB — one oversized chat/result line raises:
#   "Separator is not found, and chunk exceed the limit"
# Long tool transcripts / skill writes routinely exceed that.
_UNIX_STREAM_LIMIT = 32 * 1024 * 1024  # 32 MiB


def default_unix_socket() -> Path:
    return kageha_home() / "run" / "app-server.sock"


def parse_listen_url(raw: str) -> tuple[str, str]:
    """Return (kind, target) where kind is stdio|unix|ws."""
    text = (raw or "stdio://").strip()
    if text in {"stdio", "stdio://", "-"}:
        return "stdio", ""
    if text.startswith("unix://"):
        path = text[len("unix://") :]
        if not path or path == "/":
            return "unix", str(default_unix_socket())
        return "unix", path
    if text.startswith("ws://") or text.startswith("http://"):
        return "ws", text.replace("http://", "ws://", 1)
    raise ValueError(
        "Unsupported --listen value. Use stdio://, unix://[path], or ws://host:port"
    )


async def _handle_line(server: AppServer, line: str) -> dict[str, Any] | None:
    line = (line or "").strip()
    if not line:
        return None
    req = json.loads(line)
    return await server.handle(req)


async def serve_unix(path: str) -> None:
    sock_path = Path(path).expanduser()
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    if sock_path.exists():
        sock_path.unlink()
    server = AppServer()

    async def _client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    resp = await _handle_line(server, line.decode("utf-8", errors="replace"))
                except Exception as exc:  # noqa: BLE001
                    resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32603, "message": str(exc)},
                    }
                if resp is not None:
                    writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                    await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    try:
        unix_server = await asyncio.start_unix_server(
            _client, path=str(sock_path), limit=_UNIX_STREAM_LIMIT
        )
        # Restrict socket permissions when possible.
        try:
            os.chmod(sock_path, 0o600)
        except OSError:
            pass
        async with unix_server:
            await unix_server.serve_forever()
    finally:
        server.close()
        try:
            if sock_path.exists():
                sock_path.unlink()
        except OSError:
            pass


async def serve_ws(url: str) -> None:
    try:
        import websockets
        from websockets.asyncio.server import serve as ws_serve
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "WebSocket listen requires the 'websockets' package. "
            "Install with: pip install websockets"
        ) from exc

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = int(parsed.port or 4500)
    # Local-only by default for safety.
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ws listen is restricted to loopback hosts")

    server = AppServer()

    async def _handler(websocket: Any) -> None:
        async for message in websocket:
            try:
                if isinstance(message, bytes):
                    message = message.decode("utf-8", errors="replace")
                resp = await _handle_line(server, str(message))
            except Exception as exc:  # noqa: BLE001
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(exc)},
                }
            if resp is not None:
                await websocket.send(json.dumps(resp))

    try:
        async with ws_serve(_handler, host, port):
            await asyncio.Future()
    finally:
        server.close()


async def serve_listen(listen: str = "stdio://") -> None:
    kind, target = parse_listen_url(listen)
    if kind == "stdio":
        from kageha.app_server import serve_stdio

        await serve_stdio()
        return
    if kind == "unix":
        await serve_unix(target)
        return
    if kind == "ws":
        await serve_ws(target)
        return
    raise ValueError(f"unknown listen kind: {kind}")


def main_listen(listen: str = "stdio://") -> None:
    asyncio.run(serve_listen(listen))


async def rpc_over_unix(path: str, request: dict[str, Any]) -> dict[str, Any]:
    """Client helper: one request/response over the Unix socket."""
    reader, writer = await asyncio.open_unix_connection(
        path, limit=_UNIX_STREAM_LIMIT
    )
    try:
        writer.write((json.dumps(request) + "\n").encode("utf-8"))
        await writer.drain()
        try:
            line = await reader.readline()
        except (asyncio.LimitOverrunError, ValueError) as exc:
            raise ConnectionError(
                "App-server JSON-RPC line exceeded stream buffer "
                f"({_UNIX_STREAM_LIMIT} bytes). Original: {exc}"
            ) from exc
        if not line:
            raise ConnectionError("app-server closed connection")
        return json.loads(line.decode("utf-8"))
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass

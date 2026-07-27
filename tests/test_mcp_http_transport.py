"""MCP HTTP/SSE session transport selection (no live network)."""

from __future__ import annotations

import pytest

from kageha.mcp.client import _SdkHttpSession
from kageha.mcp.config import McpServerConfig, _parse_servers


def test_url_defaults_to_sse_transport():
    servers = _parse_servers(
        {"servers": {"remote": {"url": "https://example.com/mcp"}}}
    )
    assert servers["remote"].transport == "sse"


def test_explicit_http_transport():
    servers = _parse_servers(
        {
            "servers": {
                "remote": {
                    "url": "https://example.com/mcp",
                    "transport": "streamable_http",
                }
            }
        }
    )
    assert servers["remote"].transport == "streamable_http"


def test_sdk_session_streamable_flag():
    sse = _SdkHttpSession("https://example.com/sse", transport="sse")
    assert not sse._use_streamable()
    http = _SdkHttpSession("https://example.com/mcp", transport="http")
    assert http._use_streamable()
    sh = _SdkHttpSession("https://example.com/mcp", transport="streamable_http")
    assert sh._use_streamable()


@pytest.mark.asyncio
async def test_connect_http_missing_mcp_package_message(monkeypatch):
    from kageha.mcp.client import McpHub

    hub = McpHub(
        {
            "r": McpServerConfig(
                name="r", transport="http", url="https://example.com/mcp"
            )
        }
    )

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mcp" or name.startswith("mcp."):
            raise ImportError("no mcp")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    conn = await hub.connect("r")
    assert not conn.ok
    assert "uv sync --extra mcp" in conn.error

"""MCP server configuration — ~/.kageha/mcp.yaml (+ project / Claude Desktop JSON)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kageha.config import expand_home, kageha_home, project_root


@dataclass
class McpServerConfig:
    name: str
    transport: str = "stdio"  # stdio | sse | http (streamable) | streamable_http
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    url: str = ""  # sse / http
    enabled: bool = True
    # Tool name prefix; default mcp_<name>_
    prefix: str = ""
    risk_class: str = "mcp"

    def tool_prefix(self) -> str:
        if self.prefix:
            return self.prefix
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in self.name)
        return f"mcp_{safe}_"


def mcp_config_paths() -> list[Path]:
    """Layered config paths (later overrides earlier server names).

    Host editor configs (Cursor / Claude Desktop) load first when
    ``KAGEHA_MCP_IMPORT_HOST=1``. Kageha's own ``mcp.yaml`` always wins last
    so a local demo server is not overridden by IDE MCP entries.
    """
    paths: list[Path] = []
    import_host = os.environ.get("KAGEHA_MCP_IMPORT_HOST", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if import_host:
        paths.extend(
            [
                expand_home(
                    "~/Library/Application Support/Claude/claude_desktop_config.json"
                ),
                expand_home("~/.cursor/mcp.json"),
            ]
        )
    paths.extend(
        [
            project_root() / "mcp.yaml",
            Path.cwd() / ".kageha" / "mcp.yaml",
            Path.cwd() / ".kageha" / "mcp.json",
            kageha_home() / "mcp.yaml",
        ]
    )
    extra = os.environ.get("KAGEHA_MCP_CONFIG", "")
    for part in extra.split(":"):
        part = part.strip()
        if part:
            paths.append(expand_home(part))
    return [p for p in paths if p.is_file()]


def _parse_servers(data: dict[str, Any]) -> dict[str, McpServerConfig]:
    out: dict[str, McpServerConfig] = {}
    # Native Kageha: { servers: { name: {...} } }
    block = data.get("servers") or data.get("mcpServers") or {}
    if not isinstance(block, dict):
        return out
    for name, raw in block.items():
        if not isinstance(raw, dict):
            continue
        if raw.get("disabled") is True or raw.get("enabled") is False:
            enabled = False
        else:
            enabled = bool(raw.get("enabled", True))
        transport = str(raw.get("transport") or raw.get("type") or "stdio").lower()
        # URL without an explicit transport → SSE (legacy). Prefer
        # transport: http | streamable_http for Streamable HTTP endpoints.
        if raw.get("url") and transport == "stdio":
            transport = "sse"
        out[str(name)] = McpServerConfig(
            name=str(name),
            transport=transport,
            command=str(raw.get("command") or ""),
            args=[str(a) for a in (raw.get("args") or [])],
            env={str(k): str(v) for k, v in (raw.get("env") or {}).items()},
            cwd=str(raw.get("cwd") or ""),
            url=str(raw.get("url") or raw.get("serverUrl") or ""),
            enabled=enabled,
            prefix=str(raw.get("prefix") or ""),
            risk_class=str(raw.get("risk_class") or "mcp"),
        )
    return out


def load_mcp_config() -> dict[str, McpServerConfig]:
    """Merge all discovered MCP configs. Later files override same server name."""
    merged: dict[str, McpServerConfig] = {}
    for path in mcp_config_paths():
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".json":
                data = json.loads(text)
            else:
                data = yaml.safe_load(text) or {}
            if not isinstance(data, dict):
                continue
            for name, cfg in _parse_servers(data).items():
                merged[name] = cfg
        except Exception:  # noqa: BLE001
            continue
    return merged


def save_mcp_config(
    servers: dict[str, McpServerConfig],
    *,
    path: Path | None = None,
) -> Path:
    """Write Kageha-native mcp.yaml (does not overwrite Claude Desktop JSON)."""
    dest = path or (kageha_home() / "mcp.yaml")
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "servers": {
            name: {
                "transport": s.transport,
                "command": s.command,
                "args": s.args,
                "env": s.env,
                "cwd": s.cwd,
                "url": s.url,
                "enabled": s.enabled,
                "prefix": s.prefix,
                "risk_class": s.risk_class,
            }
            for name, s in servers.items()
        }
    }
    dest.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return dest


def ensure_default_mcp_yaml() -> Path:
    """Create an empty mcp.yaml with comments if missing."""
    dest = kageha_home() / "mcp.yaml"
    if dest.is_file():
        return dest
    dest.write_text(
        "# Kageha MCP servers (Agent Skills–compatible hosts can also use ~/.cursor/mcp.json)\n"
        "# Example:\n"
        "# servers:\n"
        "#   filesystem:\n"
        "#     command: npx\n"
        "#     args: [\"-y\", \"@modelcontextprotocol/server-filesystem\", \"/tmp\"]\n"
        "servers: {}\n",
        encoding="utf-8",
    )
    return dest


def server_to_dict(s: McpServerConfig) -> dict[str, Any]:
    return asdict(s)

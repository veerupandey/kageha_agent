"""Gateway configuration — ~/.kageha/gateway.yaml + env overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from kageha.config import expand_home, kageha_home

# Maps gateway channel id → existing `kageha <cli>` argv (without python -m).
CHANNEL_CLI: dict[str, tuple[str, ...]] = {
    "telegram": ("telegram",),
    "discord": ("discord",),
    "slack": ("slack",),
    "whatsapp": ("whatsapp",),
    "whatsapp-qr": ("whatsapp-qr",),
    "signal": ("signal",),
    "matrix": ("matrix",),
    "email": ("email",),
    "imessage": ("imessage",),
    "irc": ("irc",),
    "mattermost": ("mattermost",),
    "teams": ("teams",),
}

DEFAULT_GATEWAY_YAML = """\
# Kageha channel gateway — supervise multiple adapters under one process group.
# Each enabled channel is started as the existing CLI (`kageha telegram`, etc.).
# Adapters share the durable RuntimeStore / channel queues automatically.
#
# Env overrides:
#   KAGEHA_GATEWAY_CHANNELS=telegram,discord,whatsapp-qr
#   KAGEHA_GATEWAY_CONFIG=/path/to/gateway.yaml
#   KAGEHA_GATEWAY_AUTO_APPROVE=1

auto_approve_tasks: false

channels:
  telegram:
    enabled: false
  discord:
    enabled: false
  slack:
    enabled: false
  whatsapp-qr:
    enabled: false
  # whatsapp:          # Cloud API webhook
  #   enabled: false
  #   args: ["--port", "8787"]
  # signal:
  #   enabled: false
  # matrix:
  #   enabled: false
  # email:
  #   enabled: false
  # imessage:
  #   enabled: false
  # irc:
  #   enabled: false
  # mattermost:
  #   enabled: false
  # teams:
  #   enabled: false
"""


@dataclass
class ChannelSpec:
    name: str
    enabled: bool = True
    args: list[str] = field(default_factory=list)

    def cli_argv(self) -> tuple[str, ...]:
        base = CHANNEL_CLI.get(self.name)
        if base is None:
            raise KeyError(f"unknown channel: {self.name}")
        return base + tuple(self.args)


@dataclass
class GatewayConfig:
    auto_approve_tasks: bool = False
    channels: dict[str, ChannelSpec] = field(default_factory=dict)
    source: str = ""

    def enabled_channels(self) -> list[ChannelSpec]:
        return [spec for spec in self.channels.values() if spec.enabled]


def known_channels() -> list[str]:
    return sorted(CHANNEL_CLI)


def gateway_config_path() -> Path:
    override = (os.environ.get("KAGEHA_GATEWAY_CONFIG") or "").strip()
    if override:
        return expand_home(override)
    return kageha_home() / "gateway.yaml"


def ensure_default_gateway_yaml(*, path: Path | None = None) -> Path:
    dest = path or gateway_config_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file():
        dest.write_text(DEFAULT_GATEWAY_YAML, encoding="utf-8")
    return dest


def load_gateway_config(*, path: Path | None = None) -> GatewayConfig:
    """Load gateway.yaml (if present) and merge KAGEHA_GATEWAY_CHANNELS env."""
    cfg_path = path or gateway_config_path()
    data: dict[str, Any] = {}
    source = ""
    if cfg_path.is_file():
        try:
            loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            if isinstance(loaded, dict):
                data = loaded
                source = str(cfg_path)
        except (OSError, yaml.YAMLError):
            data = {}

    auto_approve = bool(data.get("auto_approve_tasks", False)) or _truthy(
        "KAGEHA_GATEWAY_AUTO_APPROVE"
    )

    channels: dict[str, ChannelSpec] = {}
    raw_channels = data.get("channels") or {}
    if isinstance(raw_channels, dict):
        for name, raw in raw_channels.items():
            key = str(name).strip().lower()
            if key not in CHANNEL_CLI:
                continue
            channels[key] = _parse_channel(key, raw)

    # Env list enables channels (and creates missing specs).
    for name in _env_channel_list():
        if name not in CHANNEL_CLI:
            continue
        if name in channels:
            channels[name].enabled = True
        else:
            channels[name] = ChannelSpec(name=name, enabled=True)
        if not source:
            source = "env:KAGEHA_GATEWAY_CHANNELS"

    if auto_approve:
        for spec in channels.values():
            if "--auto-approve-tasks" not in spec.args:
                spec.args.append("--auto-approve-tasks")

    return GatewayConfig(
        auto_approve_tasks=auto_approve,
        channels=channels,
        source=source or "defaults",
    )


def _parse_channel(name: str, raw: Any) -> ChannelSpec:
    if isinstance(raw, bool):
        return ChannelSpec(name=name, enabled=raw)
    if not isinstance(raw, dict):
        return ChannelSpec(name=name, enabled=True)
    if raw.get("disabled") is True or raw.get("enabled") is False:
        enabled = False
    else:
        enabled = bool(raw.get("enabled", True))
    args_raw = raw.get("args") or []
    args = [str(a) for a in args_raw] if isinstance(args_raw, list) else []
    return ChannelSpec(name=name, enabled=enabled, args=args)


def _env_channel_list() -> list[str]:
    raw = (os.environ.get("KAGEHA_GATEWAY_CHANNELS") or "").strip()
    if not raw:
        return []
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def _truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}

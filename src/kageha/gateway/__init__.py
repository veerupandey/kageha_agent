"""Unified channel gateway — supervise multiple adapters via existing CLIs."""

from kageha.gateway.config import (
    ChannelSpec,
    GatewayConfig,
    ensure_default_gateway_yaml,
    load_gateway_config,
    known_channels,
)
from kageha.gateway.supervisor import ChannelGateway

__all__ = [
    "ChannelGateway",
    "ChannelSpec",
    "GatewayConfig",
    "ensure_default_gateway_yaml",
    "known_channels",
    "load_gateway_config",
]

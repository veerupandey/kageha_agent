"""OAuth / credential connections for external services.

Tokens live under ``~/.kageha/connections/`` (mode 0600). Use the CLI:

    kageha connect list
    kageha connect login gmail
    kageha connect status gmail
    kageha connect logout gmail
"""

from __future__ import annotations

from kageha.connections.base import ConnectionProvider, ConnectionStatus
from kageha.connections.registry import (
    get_provider,
    list_providers,
    provider_ids,
)
from kageha.connections.store import ConnectionStore, connections_dir

__all__ = [
    "ConnectionProvider",
    "ConnectionStatus",
    "ConnectionStore",
    "connections_dir",
    "get_provider",
    "list_providers",
    "provider_ids",
]

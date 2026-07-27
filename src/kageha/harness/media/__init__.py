"""Pluggable media providers (trimmed harness — used by skills + optional media pack)."""

from kageha.harness.media.providers import (
    MediaCapabilities,
    MediaProvider,
    get_provider,
    list_providers,
    register_provider,
)

__all__ = [
    "MediaCapabilities",
    "MediaProvider",
    "get_provider",
    "list_providers",
    "register_provider",
]

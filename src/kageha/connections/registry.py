"""Provider registry with allowlist support."""

from __future__ import annotations

import os
from typing import Iterable

from kageha.connections.base import ConnectionProvider
from kageha.connections.providers import BUILTIN_PROVIDERS

# Fail-closed allowlist: only these ids may be registered by default.
_DEFAULT_ALLOWLIST = frozenset({"gmail", "gcal", "gdrive", "github"})


def _env_allowlist() -> frozenset[str] | None:
    """Optional override via ``KAGEHA_CONNECTIONS_ALLOWLIST=gmail,github``.

    Empty / unset → built-in default allowlist.
    ``*`` → allow all registered providers.
    """
    raw = os.environ.get("KAGEHA_CONNECTIONS_ALLOWLIST", "").strip()
    if not raw:
        return None
    if raw == "*":
        return frozenset({"*"})
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def _instantiate(builtins: Iterable[type[ConnectionProvider]] | None = None) -> dict[str, ConnectionProvider]:
    classes = builtins if builtins is not None else BUILTIN_PROVIDERS
    out: dict[str, ConnectionProvider] = {}
    for cls in classes:
        inst = cls()
        out[inst.id] = inst
    return out


_PROVIDERS: dict[str, ConnectionProvider] | None = None


def reset_registry() -> None:
    """Clear cached providers (tests)."""
    global _PROVIDERS
    _PROVIDERS = None


def _registry() -> dict[str, ConnectionProvider]:
    global _PROVIDERS
    if _PROVIDERS is None:
        _PROVIDERS = _instantiate()
    return _PROVIDERS


def allowed_provider_ids() -> frozenset[str]:
    override = _env_allowlist()
    if override is None:
        return _DEFAULT_ALLOWLIST
    if "*" in override:
        return frozenset(_registry().keys())
    return override


def provider_ids() -> list[str]:
    allow = allowed_provider_ids()
    return sorted(pid for pid in _registry() if pid in allow)


def list_providers() -> list[ConnectionProvider]:
    reg = _registry()
    return [reg[pid] for pid in provider_ids()]


def get_provider(provider_id: str) -> ConnectionProvider:
    pid = (provider_id or "").strip().lower()
    if pid not in allowed_provider_ids():
        raise KeyError(
            f"Unknown or disallowed connection {provider_id!r}. "
            f"Allowed: {', '.join(provider_ids()) or '(none)'}"
        )
    reg = _registry()
    if pid not in reg:
        raise KeyError(f"Unknown connection provider: {provider_id!r}")
    return reg[pid]


def register_provider(provider: ConnectionProvider, *, force: bool = False) -> None:
    """Register a custom provider (tests / extensions)."""
    reg = _registry()
    if provider.id in reg and not force:
        raise ValueError(f"provider already registered: {provider.id}")
    reg[provider.id] = provider

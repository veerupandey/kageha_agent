"""Connection provider protocol and status types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConnectionStatus:
    """Snapshot of a connection's login state."""

    provider: str
    connected: bool
    account: str = ""
    scopes: list[str] = field(default_factory=list)
    detail: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "connected": self.connected,
            "account": self.account,
            "scopes": list(self.scopes),
            "detail": self.detail,
            "error": self.error,
        }


class ConnectionProvider(ABC):
    """Pluggable OAuth / credential provider."""

    id: str
    label: str
    scopes: list[str]
    description: str = ""

    @abstractmethod
    def login(self, *, store: Any, open_browser: bool = True) -> ConnectionStatus:
        """Interactive login; persist tokens via ``store``."""

    @abstractmethod
    def status(self, *, store: Any) -> ConnectionStatus:
        """Return current connection status (refresh if needed)."""

    def logout(self, *, store: Any) -> ConnectionStatus:
        """Delete stored credentials for this provider."""
        store.delete(self.id)
        return ConnectionStatus(
            provider=self.id,
            connected=False,
            detail="logged out",
        )

    def require_connected(self, *, store: Any) -> ConnectionStatus:
        """Fail closed: raise if not connected."""
        st = self.status(store=store)
        if not st.connected:
            raise RuntimeError(
                f"{self.id} is not connected. Run: kageha connect login {self.id}"
            )
        return st

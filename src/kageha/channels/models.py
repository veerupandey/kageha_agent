"""Platform-neutral message contracts used by channel adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChannelMedia:
    kind: str
    filename: str = ""
    content_type: str = "application/octet-stream"
    external_id: str = ""
    local_path: str = ""
    caption: str = ""
    size_bytes: int = 0

    @property
    def path(self) -> Path | None:
        return Path(self.local_path) if self.local_path else None


@dataclass(frozen=True)
class ChannelMessage:
    channel: str
    account_id: str
    peer_id: str
    message_id: str
    text: str = ""
    thread_id: str = ""
    media: tuple[ChannelMedia, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        return f"{self.account_id}:{self.peer_id}:{self.thread_id}"


@dataclass(frozen=True)
class ChannelReply:
    text: str = ""
    artifacts: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

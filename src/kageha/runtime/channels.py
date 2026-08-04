"""Restart-safe channel message deduplication and delivery queues."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

from kageha.runtime.store import RuntimeStore


def identity_hash(channel: str, identity: str) -> str:
    """Return a stable private key without persisting the raw channel identity."""
    material = f"{channel.strip().lower()}\0{identity.strip()}".encode()
    return hashlib.sha256(material).hexdigest()


@dataclass(frozen=True)
class QueueReceipt:
    message_id: str
    accepted: bool
    status: str


class DurableChannelQueue:
    """Small transactional facade shared by every channel adapter."""

    def __init__(self, channel: str, store: RuntimeStore | None = None) -> None:
        self.channel = channel.strip().lower()
        if not self.channel:
            raise ValueError("channel is required")
        self._owns_store = store is None
        self.store = store or RuntimeStore()

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def register_inbound(
        self,
        *,
        identity: str,
        external_id: str,
        text: str,
        payload: dict[str, Any] | None = None,
    ) -> QueueReceipt:
        key = identity_hash(self.channel, identity)
        dedup_key = external_id.strip() or hashlib.sha256(
            f"{key}\0{text}\0{int(time.time() // 30)}".encode()
        ).hexdigest()
        row, created = self.store.enqueue_channel_message(
            channel=self.channel,
            identity_key=key,
            direction="inbound",
            external_id=external_id,
            dedup_key=dedup_key,
            payload=dict(payload or {"text": text}),
        )
        return QueueReceipt(
            message_id=str(row["id"]),
            accepted=created,
            status=str(row["status"]),
        )

    def identity_key(self, identity: str) -> str:
        return identity_hash(self.channel, identity)

    def enqueue_outbound(
        self,
        *,
        identity: str,
        text: str,
        idempotency_key: str,
        session_id: str = "",
        turn_id: str = "",
    ) -> QueueReceipt:
        row, created = self.store.enqueue_channel_message(
            channel=self.channel,
            identity_key=identity_hash(self.channel, identity),
            direction="outbound",
            dedup_key=idempotency_key,
            session_id=session_id,
            turn_id=turn_id,
            payload={"text": text},
        )
        return QueueReceipt(str(row["id"]), created, str(row["status"]))

    def claim_outbound(self, *, identity: str = "") -> dict[str, Any] | None:
        return self.store.claim_channel_message(
            channel=self.channel,
            direction="outbound",
            identity_key=identity_hash(self.channel, identity) if identity else "",
        )

    def finish_outbound(
        self,
        message_id: str,
        *,
        delivered: bool,
        retry_after_s: float = 0.0,
        external_id: str = "",
    ) -> dict[str, Any]:
        return self.store.finish_channel_message(
            message_id,
            delivered=delivered,
            retry_after_s=retry_after_s,
            external_id=external_id,
        )

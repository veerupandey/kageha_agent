"""Shared ingest helpers (hash idempotency)."""

from __future__ import annotations

import hashlib
from pathlib import Path


def content_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()


def already_ingested(root: Path, digest: str) -> bool:
    marker = root / "raw" / ".hashes"
    if not marker.is_file():
        return False
    return digest in marker.read_text().splitlines()


def mark_ingested(root: Path, digest: str) -> None:
    marker = root / "raw" / ".hashes"
    marker.parent.mkdir(parents=True, exist_ok=True)
    with marker.open("a") as f:
        f.write(digest + "\n")

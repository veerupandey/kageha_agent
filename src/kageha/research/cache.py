"""Tiny TTL cache for search/fetch results (process-local)."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict
from typing import Any


def cache_ttl_s() -> float:
    raw = (os.environ.get("KAGEHA_RESEARCH_CACHE_TTL") or "600").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 600.0


def cache_max_entries() -> int:
    raw = (os.environ.get("KAGEHA_RESEARCH_CACHE_MAX") or "256").strip()
    try:
        return max(16, min(4096, int(raw)))
    except ValueError:
        return 256


class TtlCache:
    def __init__(self, max_entries: int | None = None, ttl_s: float | None = None) -> None:
        self.max_entries = max_entries if max_entries is not None else cache_max_entries()
        self.ttl_s = ttl_s if ttl_s is not None else cache_ttl_s()
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(*parts: str) -> str:
        raw = "\0".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> Any | None:
        if self.ttl_s <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if expires < now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl_s <= 0:
            return
        expires = time.monotonic() + self.ttl_s
        with self._lock:
            self._data[key] = (expires, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


# Shared process caches
SEARCH_CACHE = TtlCache()
FETCH_CACHE = TtlCache()

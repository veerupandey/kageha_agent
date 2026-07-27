"""Circuit breaker utilities for tools and models.

In-process breakers and durable provider health share the same half-open
rule: once the cooldown elapses, the next attempt is allowed even if the
last write left the circuit marked open/unavailable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


def circuit_allows(
    *,
    open_until: float | None = None,
    opened_at: float | None = None,
    reset_after_s: float = 60.0,
    now: float | None = None,
) -> bool:
    """Return True when a circuit may accept a new attempt (closed or half-open)."""
    clock = time.time() if now is None else now
    if open_until is not None:
        return float(open_until) <= clock
    if opened_at is not None:
        return clock - float(opened_at) >= reset_after_s
    return True


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after_s: float = 60.0
    failures: dict[str, int] = field(default_factory=dict)
    opened_at: dict[str, float] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        opened = self.opened_at.get(key)
        if opened is None:
            return True
        return circuit_allows(
            opened_at=opened,
            reset_after_s=self.reset_after_s,
        )

    def success(self, key: str) -> None:
        self.failures[key] = 0
        self.opened_at.pop(key, None)

    def failure(self, key: str) -> None:
        self.failures[key] = self.failures.get(key, 0) + 1
        if self.failures[key] >= self.failure_threshold:
            self.opened_at[key] = time.time()
